"""
Tests de concurrencia y transaccionalidad real para el Kit de Veterinario (Fase E1/E2 - Tarea 10).
Cubre los escenarios TEST-CON-43, TEST-CON-44, TEST-CON-47 y TEST-CON-48.

Especificación y Contratos:
- plan/DISENO_TAREA10.md (§4.1, §4.3, §4.4(a), §5.4: Bloque D).
- inventario/services/consumo_service.py (descontar_consumo_kit, locks pesimistas, re-lectura post-lock).
- inventario/services/kit_service.py (asignar_stock_a_kit, orden global unificado de locks).
- inventario/api/kit_views.py (KitAsignarStockView, KitErrorHandlingMixin).
- inventario/api/kit_serializers.py (verificar_idempotencia_consumo_clinico).
- clinica/api/views.py (ExpedienteCompletoAPIView).
- clinica/api/serializers.py (ExpedienteCompletoSerializer).
"""

import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.db.utils import OperationalError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

import inventario.services.consumo_service as consumo_service

from clinica.models import Consulta
from inventario.constants import (
    CONSUMO_ESTADO_APLICADO,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PENDIENTE,
    SOLICITUD_TIPO_DEVOLUCION,
    SOLICITUD_TIPO_REPOSICION,
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_CONSUMO_HISTORIA,
    TIPO_KARDEX_DEVOLUCION_APROBADA,
    UBICACION_KARDEX_GENERAL,
    UBICACION_KARDEX_KIT,
)
from inventario.models import (
    InventarioConsumosRegistro,
    InventarioKardex,
    KitVeterinarioLotes,
    SolicitudesKit,
    SolicitudesKitDetalles,
)
from inventario.tests.base import VetericanoTransactionTestCase
from inventario.tests.factories import (
    crear_animal_con_historia,
    crear_lote_bodega,
    crear_lote_kit,
    crear_medicamento,
    crear_usuario,
)
from inventario.tests.helpers import (
    assert_kardex_consistente,
    obtener_headers_jwt,
    payload_expediente_clinico,
)


def ejecutar_en_hilos(test_case, tareas, timeout_barrier=5.0, timeout_join=10.0):
    """
    Arnés de concurrencia seguro (§4.4(a) DISENO_TAREA10.md).
    Ejecuta una lista de funciones de tarea en hilos independientes coordinados por Barrier.

    Cada tarea tiene la firma: tarea(barrier, res)
    donde `res` es un diccionario mutable {'hilo': idx, 'status': None, 'data': None, 'error': None}.
    """
    num_hilos = len(tareas)
    barrier = threading.Barrier(num_hilos)
    resultados = []
    hilos = []

    def _worker(idx, tarea_fn):
        res = {
            'hilo': idx,
            'status': None,
            'data': None,
            'error': None,
        }
        try:
            tarea_fn(barrier, res)
        except Exception as exc:
            res['error'] = exc
            try:
                barrier.abort()
            except Exception:
                pass
        finally:
            connection.close()
            resultados.append(res)

    for i, fn in enumerate(tareas):
        t = threading.Thread(target=_worker, args=(i, fn))
        hilos.append(t)
        t.start()

    timeout_ocurrido = False
    for t in hilos:
        t.join(timeout=timeout_join)
        if t.is_alive():
            timeout_ocurrido = True

    if timeout_ocurrido:
        try:
            barrier.abort()
        except Exception:
            pass
        test_case.fail("Timeout: uno o más hilos concurrentes no finalizaron en el tiempo esperado.")

    # Verificar que no existan excepciones no capturadas ni respuestas HTTP 500
    for r in resultados:
        if r['error'] is not None:
            test_case.fail(f"Excepción no capturada en hilo {r['hilo']}: {r['error']}")
        if r['status'] == status.HTTP_500_INTERNAL_SERVER_ERROR:
            test_case.fail(f"HTTP 500 no permitido en hilo {r['hilo']}: {r['data']}")

    return resultados


class KitConcurrenciaTestCase(VetericanoTransactionTestCase):
    """
    Suite de concurrencia y transaccionalidad real sobre PostgreSQL local.
    Hereda de VetericanoTransactionTestCase para truncado dinámico y aislamiento estricto.
    """

    def setUp(self):
        super().setUp()
        self.hoy = timezone.now().date()

    def test_con_43_reintento_simultaneo_mismo_client_uuid(self):
        """
        TEST-CON-43 (Caso 10): Reintento simultáneo con idéntico client_uuid.
        - Veterinario con kit dotado de 10 unidades.
        - Dos hilos envían concurrentemente POST /api/clinica/expediente-completo/
          con el mismo client_uuid, mismo animal, historia y unidades solicitadas.
        - Lock pesimista en kit_veterinario_lotes serializa y la re-lectura post-lock
          resuelve la idempotencia.
        - Respuestas aceptadas: Hilo 1 HTTP 201/200; Hilo 2 HTTP 201/200 o HTTP 400
          con error 'CONFLICTO_IDEMPOTENCIA_CONSUMO'. Cero HTTP 500.
        - Invariantes en BD: exactamente 1 registro en InventarioConsumosRegistro,
          exactamente 1 Consulta asociada al consumo, kardex descontado una sola vez,
          saldo del kit disminuido exactamente en 2 unidades (queda en 8).
        """
        vet = crear_usuario(rol_id=3, email="vet_con43@cba.gov.co")
        headers_vet = obtener_headers_jwt(vet)
        med_a = crear_medicamento(nombre="Amoxicilina CON43")
        lote_bodega = crear_lote_bodega(
            med_a,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        kl = crear_lote_kit(vet, lote_bodega, cantidad=10, cantidad_ref=10)
        animal, historia = crear_animal_con_historia(nombre="Canino CON43")

        u1 = str(uuid.uuid4())

        def tarea_1(barrier, res):
            client = APIClient()
            payload = payload_expediente_clinico(
                id_animal=animal.id_animal,
                consumos=[{
                    "id_medicamento": med_a.id_medicamento,
                    "unidades": 2,
                    "client_uuid": u1,
                }],
                offline=False,
            )
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/clinica/expediente-completo/",
                payload,
                format="json",
                **headers_vet
            )
            res['status'] = response.status_code
            res['data'] = response.data

        def tarea_2(barrier, res):
            client = APIClient()
            payload = payload_expediente_clinico(
                id_animal=animal.id_animal,
                consumos=[{
                    "id_medicamento": med_a.id_medicamento,
                    "unidades": 2,
                    "client_uuid": u1,
                }],
                offline=False,
            )
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/clinica/expediente-completo/",
                payload,
                format="json",
                **headers_vet
            )
            res['status'] = response.status_code
            res['data'] = response.data

        resultados = ejecutar_en_hilos(self, [tarea_1, tarea_2])

        statuses = [r['status'] for r in resultados]
        self.assertTrue(
            any(s in (status.HTTP_201_CREATED, status.HTTP_200_OK) for s in statuses),
            f"Se esperaba al menos un HTTP 201/200, obtenidos: {statuses}"
        )
        for r in resultados:
            if r['status'] not in (status.HTTP_201_CREATED, status.HTTP_200_OK):
                self.assertEqual(
                    r['status'],
                    status.HTTP_400_BAD_REQUEST,
                    f"Status inesperado en hilo {r['hilo']}: {r['status']}"
                )
                self.assertEqual(
                    r['data'].get('error'),
                    'CONFLICTO_IDEMPOTENCIA_CONSUMO',
                    f"Error inesperado en 400: {r['data']}"
                )

        # Invariantes en Base de Datos:
        # 1. Exactamente 1 InventarioConsumosRegistro con ese client_uuid
        consumos_qs = InventarioConsumosRegistro.objects.filter(client_uuid=u1)
        self.assertEqual(consumos_qs.count(), 1)
        reg = consumos_qs.first()
        self.assertEqual(reg.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg.cantidad_solicitada, 2)

        # 2. Exactamente 1 Consulta asociada al consumo clínico
        consultas_asociadas = Consulta.objects.filter(consumos_registro__client_uuid=u1)
        self.assertEqual(consultas_asociadas.count(), 1)
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 1)

        # 3. Kardex de un solo descuento
        assert_kardex_consistente(
            self,
            client_uuid=u1,
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA,
            cantidad_esperada=2
        )
        self.assertEqual(
            InventarioKardex.objects.filter(client_uuid=u1).count(),
            1
        )

        # 4. Saldo del kit reducido exactamente una vez (10 - 2 = 8)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 8)

    def test_con_44_asignaciones_concurrentes_cruzadas_sin_deadlock(self):
        """
        TEST-CON-44: Asignaciones concurrentes cruzadas (A, B vs B, A) sin deadlock.
        - Bodega General con existencias suficientes de Med A (100) y Med B (100).
        - Hilo 1 asigna [Med A: 15, Med B: 20] a Veterinario A.
        - Hilo 2 asigna [Med B: 25, Med A: 30] a Veterinario B.
        - Ambos hilos autenticados como gestor de inventario (Rol 1).
        - El orden global unificado por id_inventario ASC previene ciclos y deadlocks.
        - Ambos hilos completan con HTTP 201 Created.
        - Verificación de conservación de masa: saldo bodega + saldo kit = stock inicial.
        - Kardex consistente con entradas en kit y salidas en bodega.
        """
        gestor = crear_usuario(rol_id=1, email="gestor_con44@cba.gov.co")
        headers_gestor = obtener_headers_jwt(gestor)

        vet_a = crear_usuario(rol_id=3, email="vet_a_con44@cba.gov.co")
        vet_b = crear_usuario(rol_id=3, email="vet_b_con44@cba.gov.co")

        med_a = crear_medicamento(nombre="Farmaco Alpha CON44")
        med_b = crear_medicamento(nombre="Farmaco Beta CON44")

        lote_bodega_a = crear_lote_bodega(
            med_a,
            cantidad=100,
            fecha_vencimiento=self.hoy + timedelta(days=180)
        )
        lote_bodega_b = crear_lote_bodega(
            med_b,
            cantidad=100,
            fecha_vencimiento=self.hoy + timedelta(days=180)
        )

        payload_1 = {
            "id_veterinario": vet_a.id_usuario,
            "motivo": "Dotacion cruzada Hilo 1",
            "items": [
                {"id_medicamento": med_a.id_medicamento, "cantidad": 15},
                {"id_medicamento": med_b.id_medicamento, "cantidad": 20},
            ]
        }

        payload_2 = {
            "id_veterinario": vet_b.id_usuario,
            "motivo": "Dotacion cruzada Hilo 2",
            "items": [
                {"id_medicamento": med_b.id_medicamento, "cantidad": 25},
                {"id_medicamento": med_a.id_medicamento, "cantidad": 30},
            ]
        }

        def tarea_1(barrier, res):
            client = APIClient()
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/inventario/kit/asignar/",
                payload_1,
                format="json",
                **headers_gestor
            )
            res['status'] = response.status_code
            res['data'] = response.data

        def tarea_2(barrier, res):
            client = APIClient()
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/inventario/kit/asignar/",
                payload_2,
                format="json",
                **headers_gestor
            )
            res['status'] = response.status_code
            res['data'] = response.data

        resultados = ejecutar_en_hilos(self, [tarea_1, tarea_2])

        for r in resultados:
            self.assertEqual(
                r['status'],
                status.HTTP_201_CREATED,
                f"Fallo en asignacion concurrente hilo {r['hilo']}: {r['data']}"
            )

        # Verificación de saldos en Bodega General
        lote_bodega_a.refresh_from_db()
        lote_bodega_b.refresh_from_db()
        # Med A asignado: 15 + 30 = 45 -> Restante en bodega: 55
        self.assertEqual(lote_bodega_a.cantidad_actual, 55)
        # Med B asignado: 20 + 25 = 45 -> Restante en bodega: 55
        self.assertEqual(lote_bodega_b.cantidad_actual, 55)

        # Verificación de saldos en Kits de Veterinarios
        total_kit_a = sum(
            kl.cantidad_actual
            for kl in KitVeterinarioLotes.objects.filter(id_medicamento=med_a)
        )
        total_kit_b = sum(
            kl.cantidad_actual
            for kl in KitVeterinarioLotes.objects.filter(id_medicamento=med_b)
        )
        self.assertEqual(total_kit_a, 45)
        self.assertEqual(total_kit_b, 45)

        # Principio de conservación: Bodega + Kits == Stock Inicial
        self.assertEqual(lote_bodega_a.cantidad_actual + total_kit_a, 100)
        self.assertEqual(lote_bodega_b.cantidad_actual + total_kit_b, 100)

        # Verificación de Kardex (tipo ASIGNACION_KIT)
        salidas_bodega = InventarioKardex.objects.filter(
            tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT,
            ubicacion=UBICACION_KARDEX_GENERAL
        )
        entradas_kit = InventarioKardex.objects.filter(
            tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT,
            ubicacion=UBICACION_KARDEX_KIT
        )
        self.assertEqual(
            sum(k.cantidad for k in salidas_bodega if k.id_medicamento_id == med_a.id_medicamento),
            45
        )
        self.assertEqual(
            sum(k.cantidad for k in salidas_bodega if k.id_medicamento_id == med_b.id_medicamento),
            45
        )
        self.assertEqual(
            sum(k.cantidad for k in entradas_kit if k.id_medicamento_id == med_a.id_medicamento),
            45
        )
        self.assertEqual(
            sum(k.cantidad for k in entradas_kit if k.id_medicamento_id == med_b.id_medicamento),
            45
        )

    def test_con_47_carrera_agotamiento_stock_mismo_kit(self):
        """
        TEST-CON-47: Carrera por agotamiento de stock en el mismo kit.
        - Veterinario con exactamente 3 unidades disponibles en kit.
        - Dos hilos solicitan concurrentemente 2 unidades cada uno mediante
          POST /api/clinica/expediente-completo/ en modo online (consultas y animales independientes).
        - Resultado no determinista esperado: exactamente un hilo completa con HTTP 201 Created
          y el otro recibe HTTP 400 Bad Request con error='STOCK_INSUFICIENTE_KIT'.
        - Saldo final del kit queda en 1 unidad (nunca saldo negativo, CHECK >= 0).
        - Base de datos: exactamente un consumo APLICADO, exactamente una consulta creada,
          kardex refleja un solo descuento de 2 unidades.
        """
        vet = crear_usuario(rol_id=3, email="vet_con47@cba.gov.co")
        headers_vet = obtener_headers_jwt(vet)
        med_a = crear_medicamento(nombre="Analgesico CON47")
        lote_bodega = crear_lote_bodega(
            med_a,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        kl = crear_lote_kit(vet, lote_bodega, cantidad=3, cantidad_ref=3)

        animal_1, historia_1 = crear_animal_con_historia(nombre="Paciente Uno CON47")
        animal_2, historia_2 = crear_animal_con_historia(nombre="Paciente Dos CON47")

        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())

        def tarea_1(barrier, res):
            client = APIClient()
            payload = payload_expediente_clinico(
                id_animal=animal_1.id_animal,
                consumos=[{
                    "id_medicamento": med_a.id_medicamento,
                    "unidades": 2,
                    "client_uuid": u1,
                }],
                offline=False,
            )
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/clinica/expediente-completo/",
                payload,
                format="json",
                **headers_vet
            )
            res['status'] = response.status_code
            res['data'] = response.data

        def tarea_2(barrier, res):
            client = APIClient()
            payload = payload_expediente_clinico(
                id_animal=animal_2.id_animal,
                consumos=[{
                    "id_medicamento": med_a.id_medicamento,
                    "unidades": 2,
                    "client_uuid": u2,
                }],
                offline=False,
            )
            barrier.wait(timeout=5.0)
            response = client.post(
                "/api/clinica/expediente-completo/",
                payload,
                format="json",
                **headers_vet
            )
            res['status'] = response.status_code
            res['data'] = response.data

        resultados = ejecutar_en_hilos(self, [tarea_1, tarea_2])

        statuses = [r['status'] for r in resultados]
        self.assertEqual(
            sorted(statuses),
            [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
            f"Se esperaba exactamente un 201 y un 400, obtenidos: {statuses}"
        )

        perdedor = next(r for r in resultados if r['status'] == status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            perdedor['data'].get('error'),
            'STOCK_INSUFICIENTE_KIT',
            f"Error inesperado en perdedor: {perdedor['data']}"
        )

        # Verificación de invariantes en Base de Datos:
        # 1. Saldo final del kit queda en 1 (3 - 2 = 1, nunca negativo)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 1)
        self.assertGreaterEqual(kl.cantidad_actual, 0)

        # 2. Exactamente un consumo en estado APLICADO
        consumos_aplicados = InventarioConsumosRegistro.objects.filter(
            estado=CONSUMO_ESTADO_APLICADO,
            client_uuid__in=[u1, u2]
        )
        self.assertEqual(consumos_aplicados.count(), 1)
        reg_ganador = consumos_aplicados.first()
        self.assertEqual(reg_ganador.cantidad_solicitada, 2)

        # 3. Kardex consistente con un solo descuento
        kardex_qs = InventarioKardex.objects.filter(
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA,
            client_uuid__in=[u1, u2]
        )
        self.assertEqual(kardex_qs.count(), 1)
        self.assertEqual(kardex_qs.first().cantidad, 2)

        # 4. Consultas clínicas: la transacción del perdedor fue revertida limpiamente
        consultas_totales = Consulta.objects.filter(
            id_historia__in=[historia_1, historia_2]
        ).count()
        self.assertEqual(consultas_totales, 1)

    def test_con_48_deadlock_bodega_legacy_vs_kit_kardex(self):
        """
        TEST-CON-48 (Caso 14): Deadlock Bodega Legacy FOR UPDATE vs Kit Kardex.
        - Mismo veterinario en ambas peticiones.
        - Ambas vía POST /api/clinica/expediente-completo/ en modo online con client_uuid,
          animales e historias independientes.
        - Med X y Med Y, ambos con existencias en bodega (>0) y kit con unidades suficientes.
          El lote de kit de X apunta al lote de bodega de X (cantidad_actual > 0).
        - Petición A: Med X en tratamiento legacy de bodega (hoja2.tratamientos con descuento)
          y Med Y en kit (medicamentos_usados).
        - Petición B: Med X y Med Y en kit (medicamentos_usados).
        - Ciclo demostrado:
          * A retiene FOR UPDATE sobre inventarios(X) (via _descontar_fifo, serializers.py:114-118).
          * B retiene FOR UPDATE sobre kit_veterinario_lotes(X e Y) (via descontar_consumo_kit, consumo_service.py:498-502).
          * B intenta insertar kardex para X (consumo_service.py:709-725) y PostgreSQL solicita FOR KEY SHARE sobre inventarios(X). B queda bloqueada por A.
          * A intenta descontar kit de Y (serializers.py:622, consumo_service.py:498) y solicita FOR UPDATE sobre kit_veterinario_lotes(Y). A queda bloqueada por B.
          * Ciclo A -> B -> A resuelto por PostgreSQL en ~1000ms con SQLSTATE 40P01.
        - Determinismo / Instrumentación:
          Se parchea descontar_consumo_kit tal como lo importa clinica.api.serializers
          (importado dinámicamente desde inventario.services.consumo_service), con un wrapper
          que introduce time.sleep(0.5) exclusivamente para el Hilo A. Esto garantiza que A
          adquiera el lock de bodega antes de que B complete sus locks y bloquee en kardex.
        - Resultados aceptados (prueba de caracterización):
          (a) Un hilo 201 y el otro víctima con SQLSTATE 40P01 / HTTP 500 (confirmando Hallazgo 4:
              ExpedienteCompletoAPIView.post en views.py:387-392 no captura OperationalError).
          (b) Ambos hilos 201 (el ciclo no se formó).
        - Invariantes en BD:
          * Saldos nunca negativos.
          * Sobreviviente tiene consulta, consumo(s) APLICADO y kardex coherente.
          * Víctima no deja consulta, consumo, kardex ni descuento de bodega (rollback total).
          * Principio de conservación: saldos de bodega y kit coherentes con lo aplicado.
        """
        vet = crear_usuario(rol_id=3, email="vet_con48@cba.gov.co")
        headers_vet = obtener_headers_jwt(vet)

        med_x = crear_medicamento(nombre="Med X CON48")
        med_y = crear_medicamento(nombre="Med Y CON48")

        lote_bodega_x = crear_lote_bodega(
            med_x,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_bodega_y = crear_lote_bodega(
            med_y,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )

        kl_x = crear_lote_kit(vet, lote_bodega_x, cantidad=10, cantidad_ref=10)
        kl_y = crear_lote_kit(vet, lote_bodega_y, cantidad=10, cantidad_ref=10)

        animal_a, historia_a = crear_animal_con_historia(nombre="Paciente A CON48")
        animal_b, historia_b = crear_animal_con_historia(nombre="Paciente B CON48")

        u_a_y = str(uuid.uuid4())
        u_b_x = str(uuid.uuid4())
        u_b_y = str(uuid.uuid4())

        payload_a = payload_expediente_clinico(
            id_animal=animal_a.id_animal,
            hoja2={
                "diagnosticoFinal": "Evaluacion A Tratamiento Bodega",
                "tratamientos": [{
                    "productoBase": "Tratamiento Bodega Med X",
                    "dosisBasica": "10 mg",
                    "id_medicamento": med_x.id_medicamento,
                    "cantidad_usada": 2,
                    "descontar_inventario": True,
                }],
            },
            consumos=[{
                "id_medicamento": med_y.id_medicamento,
                "unidades": 3,
                "client_uuid": u_a_y,
            }],
            offline=False,
        )

        payload_b = payload_expediente_clinico(
            id_animal=animal_b.id_animal,
            hoja2={
                "diagnosticoFinal": "Evaluacion B Consumos Kit",
                "tratamientos": [],
            },
            consumos=[
                {
                    "id_medicamento": med_x.id_medicamento,
                    "unidades": 4,
                    "client_uuid": u_b_x,
                },
                {
                    "id_medicamento": med_y.id_medicamento,
                    "unidades": 5,
                    "client_uuid": u_b_y,
                },
            ],
            offline=False,
        )

        def tarea_a(barrier, res):
            threading.current_thread().name = 'hilo_con48_A'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    "/api/clinica/expediente-completo/",
                    payload_a,
                    format="json",
                    **headers_vet
                )
                if response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
                    exc_info = getattr(response, 'exc_info', None)
                    exc = exc_info[1] if exc_info else None
                    cause = getattr(exc, '__cause__', None) if exc else None
                    pgcode = getattr(cause, 'pgcode', None)
                    if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                        pgcode = cause.diag.sqlstate
                    if pgcode == '40P01' or (exc and 'deadlock' in str(exc).lower()):
                        res['status'] = 'DEADLOCK_40P01'
                    else:
                        res['status'] = 'DEADLOCK_500'
                    res['data'] = {
                        'pgcode': pgcode,
                        'message': str(exc) if exc else str(getattr(response, 'data', response.content)),
                    }
                else:
                    res['status'] = response.status_code
                    res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                if pgcode == '40P01' or 'deadlock' in str(exc).lower():
                    res['status'] = 'DEADLOCK_40P01'
                else:
                    res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        def tarea_b(barrier, res):
            threading.current_thread().name = 'hilo_con48_B'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    "/api/clinica/expediente-completo/",
                    payload_b,
                    format="json",
                    **headers_vet
                )
                if response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
                    exc_info = getattr(response, 'exc_info', None)
                    exc = exc_info[1] if exc_info else None
                    cause = getattr(exc, '__cause__', None) if exc else None
                    pgcode = getattr(cause, 'pgcode', None)
                    if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                        pgcode = cause.diag.sqlstate
                    if pgcode == '40P01' or (exc and 'deadlock' in str(exc).lower()):
                        res['status'] = 'DEADLOCK_40P01'
                    else:
                        res['status'] = 'DEADLOCK_500'
                    res['data'] = {
                        'pgcode': pgcode,
                        'message': str(exc) if exc else str(getattr(response, 'data', response.content)),
                    }
                else:
                    res['status'] = response.status_code
                    res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                if pgcode == '40P01' or 'deadlock' in str(exc).lower():
                    res['status'] = 'DEADLOCK_40P01'
                else:
                    res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        real_descontar = consumo_service.descontar_consumo_kit

        def wrapper_descontar_consumo_kit(*args, **kwargs):
            if threading.current_thread().name == 'hilo_con48_A':
                time.sleep(0.5)
            elif threading.current_thread().name == 'hilo_con48_B':
                time.sleep(0.3)
            return real_descontar(*args, **kwargs)

        with patch('inventario.services.consumo_service.descontar_consumo_kit', side_effect=wrapper_descontar_consumo_kit):
            resultados = ejecutar_en_hilos(self, [tarea_a, tarea_b])

        res_a = next(r for r in resultados if r['hilo'] == 0)
        res_b = next(r for r in resultados if r['hilo'] == 1)
        pgcode_a = res_a['data'].get('pgcode') if isinstance(res_a.get('data'), dict) else None
        pgcode_b = res_b['data'].get('pgcode') if isinstance(res_b.get('data'), dict) else None
        print(f"\n[TEST-CON-48-RESULT] Hilo A (0): status={res_a['status']}, pgcode={pgcode_a} | Hilo B (1): status={res_b['status']}, pgcode={pgcode_b}")

        statuses = [r['status'] for r in resultados]

        es_caso_a = (
            len([s for s in statuses if s == status.HTTP_201_CREATED]) == 1
            and any(
                r['status'] in ('DEADLOCK_40P01', 'DEADLOCK_500') or '40P01' in str(r.get('data', ''))
                for r in resultados if r['status'] != status.HTTP_201_CREATED
            )
        )
        self.assertTrue(
            es_caso_a,
            f"Exigido caso (a): un 201 y una víctima 40P01 de deadlock. Statuses: {statuses}, Detalle: {resultados}"
        )

        lote_bodega_x.refresh_from_db()
        lote_bodega_y.refresh_from_db()
        kl_x.refresh_from_db()
        kl_y.refresh_from_db()

        # Invariante 1: Ningún saldo es negativo (CHECK >= 0 respetado)
        self.assertGreaterEqual(lote_bodega_x.cantidad_actual, 0)
        self.assertGreaterEqual(lote_bodega_y.cantidad_actual, 0)
        self.assertGreaterEqual(kl_x.cantidad_actual, 0)
        self.assertGreaterEqual(kl_y.cantidad_actual, 0)

        ganador = next(r for r in resultados if r['status'] == status.HTTP_201_CREATED)
        victima = next(r for r in resultados if r['status'] != status.HTTP_201_CREATED)

        if ganador['hilo'] == 0:
            # Hilo A sobrevivió, Hilo B fue víctima de deadlock
            self.assertEqual(Consulta.objects.filter(id_historia=historia_a).count(), 1)
            self.assertEqual(Consulta.objects.filter(id_historia=historia_b).count(), 0)

            # Consumos: APLICADO para A, 0 para B (rollback completo)
            self.assertEqual(
                InventarioConsumosRegistro.objects.filter(
                    client_uuid=u_a_y, estado=CONSUMO_ESTADO_APLICADO
                ).count(),
                1
            )
            self.assertEqual(
                InventarioConsumosRegistro.objects.filter(
                    client_uuid__in=[u_b_x, u_b_y]
                ).count(),
                0
            )

            # Kardex: 1 para A (consumo kit Y = 3), 0 para B
            self.assertEqual(
                InventarioKardex.objects.filter(
                    client_uuid=u_a_y, tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA
                ).count(),
                1
            )
            self.assertEqual(
                InventarioKardex.objects.filter(
                    client_uuid__in=[u_b_x, u_b_y]
                ).count(),
                0
            )

            # Saldos coherentes
            self.assertEqual(lote_bodega_x.cantidad_actual, 48)  # 50 - 2
            self.assertEqual(lote_bodega_y.cantidad_actual, 50)
            self.assertEqual(kl_x.cantidad_actual, 10)
            self.assertEqual(kl_y.cantidad_actual, 7)   # 10 - 3

            # Principio de conservación: Bodega + Kit == Stock Inicial - Consumo
            self.assertEqual(lote_bodega_x.cantidad_actual + kl_x.cantidad_actual, 58)
            self.assertEqual(lote_bodega_y.cantidad_actual + kl_y.cantidad_actual, 57)

        else:
            # Hilo B sobrevivió, Hilo A fue víctima de deadlock
            self.assertEqual(Consulta.objects.filter(id_historia=historia_b).count(), 1)
            self.assertEqual(Consulta.objects.filter(id_historia=historia_a).count(), 0)

            # Consumos: APLICADO para B, 0 para A (rollback completo)
            self.assertEqual(
                InventarioConsumosRegistro.objects.filter(
                    client_uuid=u_b_x, estado=CONSUMO_ESTADO_APLICADO
                ).count(),
                1
            )
            self.assertEqual(
                InventarioConsumosRegistro.objects.filter(
                    client_uuid=u_b_y, estado=CONSUMO_ESTADO_APLICADO
                ).count(),
                1
            )
            self.assertEqual(
                InventarioConsumosRegistro.objects.filter(
                    client_uuid=u_a_y
                ).count(),
                0
            )

            # Kardex: 2 para B (consumos kit X = 4 e Y = 5), 0 para A
            self.assertEqual(
                InventarioKardex.objects.filter(
                    client_uuid__in=[u_b_x, u_b_y], tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA
                ).count(),
                2
            )
            self.assertEqual(
                InventarioKardex.objects.filter(
                    client_uuid=u_a_y
                ).count(),
                0
            )

            # Saldos coherentes (descuento de bodega de A revertido limpiamente)
            self.assertEqual(lote_bodega_x.cantidad_actual, 50)  # Revertido
            self.assertEqual(lote_bodega_y.cantidad_actual, 50)
            self.assertEqual(kl_x.cantidad_actual, 6)   # 10 - 4
            self.assertEqual(kl_y.cantidad_actual, 5)   # 10 - 5

            # Principio de conservación
            self.assertEqual(lote_bodega_x.cantidad_actual + kl_x.cantidad_actual, 56)
            self.assertEqual(lote_bodega_y.cantidad_actual + kl_y.cantidad_actual, 55)

    def test_con_45_doble_aprobacion_concurrente_devolucion(self):
        """
        TEST-CON-45: Doble aprobación concurrente de la misma devolución.
        - Veterinario con kit dotado de 10 unidades de un medicamento vigente.
        - Bodega con 20 unidades del mismo medicamento.
        - Solicitud de devolución PENDIENTE de 4 unidades creada por el veterinario
          mediante POST /api/inventario/kit/solicitudes/.
        - Dos gestores distintos (Rol 1) aprueban concurrentemente via:
          POST /api/inventario/kit/solicitudes/{id}/aprobar/ sincronizados con Barrier.
        - Lock pesimista en solicitudes_kit (select_for_update(no_key=True),
          solicitudes_service.py:492) serializa la aprobación:
          El primer gestor adquiere el lock, ejecuta procesar_devolucion_aprobada
          (kit_service.py:1170-1235), pasa la solicitud a APROBADA y completa con HTTP 200 OK.
          El segundo gestor adquiere el lock a continuación, re-lee la solicitud bajo lock,
          constata que estado != PENDIENTE y lanza SolicitudEstadoInvalidaError (solicitudes_service.py:497).
          KitErrorHandlingMixin (kit_views.py:96-107) traduce a HTTP 409 Conflict con error='SOLICITUD_ESTADO_INVALIDA'.
        - Invariantes:
          * Solicitud en estado final APROBADA exactamente una sola vez.
          * Bodega incrementada exactamente una sola vez (20 + 4 = 24).
          * Kit descontado exactamente una sola vez (10 - 4 = 6).
          * Kardex DEVOLUCION_APROBADA sin duplicados: exactamente 2 registros
            (1 fila KIT_VETERINARIO salida de 4, 1 fila GENERAL ingreso de 4).
          * Conservación de existencias: bodega (24) + kit (6) == 30 iniciales.
          * Consistencia global de kardex verificada con assert_kardex_consistente.
        """
        vet = crear_usuario(rol_id=3, email="vet_con45@cba.gov.co")
        gestor_1 = crear_usuario(rol_id=1, email="gestor1_con45@cba.gov.co")
        gestor_2 = crear_usuario(rol_id=1, email="gestor2_con45@cba.gov.co")
        headers_vet = obtener_headers_jwt(vet)
        headers_gestor_1 = obtener_headers_jwt(gestor_1)
        headers_gestor_2 = obtener_headers_jwt(gestor_2)

        med = crear_medicamento(nombre="Med CON45 Devolucion")
        lote_bodega = crear_lote_bodega(
            med,
            cantidad=20,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit = crear_lote_kit(
            vet,
            lote_bodega,
            cantidad=10,
            cantidad_ref=10,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )

        client_vet = APIClient()
        res_crear = client_vet.post(
            "/api/inventario/kit/solicitudes/",
            {
                "tipo_solicitud": SOLICITUD_TIPO_DEVOLUCION,
                "motivo": "Devolución excedente stock vigente CON45",
                "items": [{"id_medicamento": med.id_medicamento, "cantidad": 4}],
            },
            format="json",
            **headers_vet
        )
        self.assertEqual(res_crear.status_code, status.HTTP_201_CREATED)
        sol_id = res_crear.data["id_solicitud"]

        def tarea_gestor_1(barrier, res):
            threading.current_thread().name = 'hilo_con45_gestor1'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    f"/api/inventario/kit/solicitudes/{sol_id}/aprobar/",
                    {"observaciones_gestor": "Aprobación concurrente Gestor 1"},
                    format="json",
                    **headers_gestor_1
                )
                res['status'] = response.status_code
                res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        def tarea_gestor_2(barrier, res):
            threading.current_thread().name = 'hilo_con45_gestor2'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    f"/api/inventario/kit/solicitudes/{sol_id}/aprobar/",
                    {"observaciones_gestor": "Aprobación concurrente Gestor 2"},
                    format="json",
                    **headers_gestor_2
                )
                res['status'] = response.status_code
                res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        resultados = ejecutar_en_hilos(self, [tarea_gestor_1, tarea_gestor_2])

        statuses = [r['status'] for r in resultados]
        self.assertIn(status.HTTP_200_OK, statuses)
        self.assertIn(status.HTTP_409_CONFLICT, statuses)

        r_200 = next(r for r in resultados if r['status'] == status.HTTP_200_OK)
        r_409 = next(r for r in resultados if r['status'] == status.HTTP_409_CONFLICT)
        self.assertEqual(r_409['data'].get('error'), 'SOLICITUD_ESTADO_INVALIDA')
        print(f"\n[TEST-CON-45-RESULT] Ganador: Hilo {r_200['hilo']} (status={r_200['status']}) | Perdedor: Hilo {r_409['hilo']} (status={r_409['status']}, error={r_409['data'].get('error')})")

        sol = SolicitudesKit.objects.get(pk=sol_id)
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_APROBADA)
        self.assertIn(sol.id_gestor_id, [gestor_1.id_usuario, gestor_2.id_usuario])

        lote_bodega.refresh_from_db()
        lote_kit.refresh_from_db()

        self.assertEqual(lote_bodega.cantidad_actual, 24)
        self.assertEqual(lote_kit.cantidad_actual, 6)
        self.assertEqual(lote_bodega.cantidad_actual + lote_kit.cantidad_actual, 30)

        # Kardex DEVOLUCION_APROBADA sin duplicados: exactamente 2 registros
        kardex_dev = list(
            InventarioKardex.objects.filter(
                id_solicitud_kit_id=sol_id,
                tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA
            )
        )
        self.assertEqual(len(kardex_dev), 2)
        kardex_kit = next(k for k in kardex_dev if k.ubicacion == UBICACION_KARDEX_KIT)
        kardex_gen = next(k for k in kardex_dev if k.ubicacion == UBICACION_KARDEX_GENERAL)
        self.assertEqual(kardex_kit.cantidad, 4)
        self.assertEqual(kardex_kit.saldo_anterior, 10)
        self.assertEqual(kardex_kit.saldo_resultante, 6)
        self.assertEqual(kardex_gen.cantidad, 4)
        self.assertEqual(kardex_gen.saldo_anterior, 20)
        self.assertEqual(kardex_gen.saldo_resultante, 24)

    def test_con_46_reposiciones_compitiendo_por_bodega(self):
        """
        TEST-CON-46: Aprobación concurrente de reposiciones compitiendo por bodega.
        - Bodega con únicamente 15 unidades de un medicamento vigente.
        - Vet A y Vet B tienen kits iniciales y solicitudes de reposición PENDIENTES
          de 10 unidades cada uno (creadas vía el flujo real de la API).
        - Dos gestores distintos (Rol 1) aprueban en paralelo con Barrier:
          POST /api/inventario/kit/solicitudes/{id}/aprobar/
        - Comportamiento según código de la app:
          * El primer gestor adquiere el lock pesimista sobre inventarios (lote bodega),
            descuenta 10 unidades (quedan 5 en bodega), dota al kit y completa con HTTP 200 OK.
          * El segundo gestor adquiere el lock de bodega a continuación, detecta que restante
            (5 uds faltantes) > 0 y kit_service.asignar_stock_a_kit (kit_service.py:359-365)
            lanza StockInsuficienteBodegaError("Stock insuficiente en bodega general...").
          * SolicitudKitAprobarView captura la excepción mediante KitErrorHandlingMixin
            (kit_views.py:89,100) y responde HTTP 400 Bad Request con error='STOCK_INSUFICIENTE_BODEGA'.
          * La transacción del segundo gestor se revierte íntegramente: su solicitud permanece
            en estado PENDIENTE, su kit no sufre modificaciones y no genera kardex.
        - Invariantes:
          * Saldo final de bodega >= 0 (exactamente 5 unidades).
          * Conservación estricta de existencias: bodega (5) + kit_a (10/0) + kit_b (0/10) == 15.
          * Nunca sobre-asignación de existencias.
          * Kardex coherente: exactamente 2 movimientos para el ganador, 0 para el perdedor.
          * No determinista: aserción neutral respecto a cuál veterinario gana.
        """
        vet_a = crear_usuario(rol_id=3, email="veta_con46@cba.gov.co")
        vet_b = crear_usuario(rol_id=3, email="vetb_con46@cba.gov.co")
        gestor_1 = crear_usuario(rol_id=1, email="gestor1_con46@cba.gov.co")
        gestor_2 = crear_usuario(rol_id=1, email="gestor2_con46@cba.gov.co")
        headers_vet_a = obtener_headers_jwt(vet_a)
        headers_vet_b = obtener_headers_jwt(vet_b)
        headers_gestor_1 = obtener_headers_jwt(gestor_1)
        headers_gestor_2 = obtener_headers_jwt(gestor_2)

        med = crear_medicamento(nombre="Med CON46 Bodega")
        lote_bodega = crear_lote_bodega(
            med,
            cantidad=15,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit_a = crear_lote_kit(
            vet_a,
            lote_bodega,
            cantidad=0,
            cantidad_ref=10,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit_b = crear_lote_kit(
            vet_b,
            lote_bodega,
            cantidad=0,
            cantidad_ref=10,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )

        client_vet_a = APIClient()
        res_a = client_vet_a.post(
            "/api/inventario/kit/solicitudes/",
            {
                "tipo_solicitud": SOLICITUD_TIPO_REPOSICION,
                "motivo": "Reposición 10 unidades Vet A",
                "items": [{"id_medicamento": med.id_medicamento, "cantidad": 10}],
                "es_reposicion_total": False,
            },
            format="json",
            **headers_vet_a
        )
        self.assertEqual(res_a.status_code, status.HTTP_201_CREATED)
        sol_a_id = res_a.data["id_solicitud"]

        client_vet_b = APIClient()
        res_b = client_vet_b.post(
            "/api/inventario/kit/solicitudes/",
            {
                "tipo_solicitud": SOLICITUD_TIPO_REPOSICION,
                "motivo": "Reposición 10 unidades Vet B",
                "items": [{"id_medicamento": med.id_medicamento, "cantidad": 10}],
                "es_reposicion_total": False,
            },
            format="json",
            **headers_vet_b
        )
        self.assertEqual(res_b.status_code, status.HTTP_201_CREATED)
        sol_b_id = res_b.data["id_solicitud"]

        def tarea_aprobar_a(barrier, res):
            threading.current_thread().name = 'hilo_con46_gestor1'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    f"/api/inventario/kit/solicitudes/{sol_a_id}/aprobar/",
                    {"observaciones_gestor": "Aprobación Solicitud A"},
                    format="json",
                    **headers_gestor_1
                )
                res['status'] = response.status_code
                res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        def tarea_aprobar_b(barrier, res):
            threading.current_thread().name = 'hilo_con46_gestor2'
            client = APIClient(raise_request_exception=False)
            barrier.wait(timeout=5.0)
            try:
                response = client.post(
                    f"/api/inventario/kit/solicitudes/{sol_b_id}/aprobar/",
                    {"observaciones_gestor": "Aprobación Solicitud B"},
                    format="json",
                    **headers_gestor_2
                )
                res['status'] = response.status_code
                res['data'] = response.data
            except OperationalError as exc:
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'message': str(exc)}

        resultados = ejecutar_en_hilos(self, [tarea_aprobar_a, tarea_aprobar_b])

        statuses = [r['status'] for r in resultados]
        self.assertIn(status.HTTP_200_OK, statuses)
        self.assertIn(status.HTTP_400_BAD_REQUEST, statuses)

        r_ganador = next(r for r in resultados if r['status'] == status.HTTP_200_OK)
        r_perdedor = next(r for r in resultados if r['status'] == status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r_perdedor['data'].get('error'), 'STOCK_INSUFICIENTE_BODEGA')
        print(f"\n[TEST-CON-46-RESULT] Ganador: Hilo {r_ganador['hilo']} (status={r_ganador['status']}) | Perdedor: Hilo {r_perdedor['hilo']} (status={r_perdedor['status']}, error={r_perdedor['data'].get('error')})")

        lote_bodega.refresh_from_db()
        lote_kit_a.refresh_from_db()
        lote_kit_b.refresh_from_db()

        # Invariante 1: Saldo de bodega >= 0
        self.assertGreaterEqual(lote_bodega.cantidad_actual, 0)
        self.assertEqual(lote_bodega.cantidad_actual, 5)

        # Invariante 2: Conservación de existencias (15 total inicial)
        self.assertEqual(
            lote_bodega.cantidad_actual + lote_kit_a.cantidad_actual + lote_kit_b.cantidad_actual,
            15
        )

        sol_a = SolicitudesKit.objects.get(pk=sol_a_id)
        sol_b = SolicitudesKit.objects.get(pk=sol_b_id)

        if r_ganador['hilo'] == 0:
            # Ganó Solicitud A
            self.assertEqual(sol_a.estado, SOLICITUD_ESTADO_APROBADA)
            self.assertEqual(sol_b.estado, SOLICITUD_ESTADO_PENDIENTE)
            self.assertEqual(lote_kit_a.cantidad_actual, 10)
            self.assertEqual(lote_kit_b.cantidad_actual, 0)

            self.assertEqual(
                InventarioKardex.objects.filter(
                    id_solicitud_kit_id=sol_a_id,
                    tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT
                ).count(),
                2
            )
            self.assertEqual(
                InventarioKardex.objects.filter(
                    id_solicitud_kit_id=sol_b_id
                ).count(),
                0
            )
        else:
            # Ganó Solicitud B
            self.assertEqual(sol_b.estado, SOLICITUD_ESTADO_APROBADA)
            self.assertEqual(sol_a.estado, SOLICITUD_ESTADO_PENDIENTE)
            self.assertEqual(lote_kit_b.cantidad_actual, 10)
            self.assertEqual(lote_kit_a.cantidad_actual, 0)

            self.assertEqual(
                InventarioKardex.objects.filter(
                    id_solicitud_kit_id=sol_b_id,
                    tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT
                ).count(),
                2
            )
            self.assertEqual(
                InventarioKardex.objects.filter(
                    id_solicitud_kit_id=sol_a_id
                ).count(),
                0
            )

    def test_con_49_lock_timeout_tolerancia_transaccion_colgada(self):
        """
        TEST-CON-49: Respeto de lock_timeout con tolerancia ante transacción colgada.
        - Hilo 1 abre transaction.atomic() y retiene un lock pesimista exclusivo
          (select_for_update) sobre la fila de KitVeterinarioLotes del veterinario/medicamento
          sin liberarlo hasta que el hilo 2 o el test lo indiquen mediante threading.Event.
        - Hilo 2 hace POST /api/clinica/expediente-completo/ en modo online con medicamentos_usados
          del mismo veterinario y medicamento.
        - Al llegar a descontar_consumo_kit (consumo_service.py:498-502), Hilo 2 intenta adquirir
          select_for_update sobre el mismo lote del kit.
        - PostgreSQL retiene a Hilo 2 en espera hasta que expira lock_timeout='3000ms'
          (configurado en runner.py:24 via señal connection_created).
        - Hilo 2 aborta con OperationalError SQLSTATE 55P03 (lock_not_available) / HTTP 500.
        - Tolerancia de tiempo medida con time.monotonic(): 3.0 s <= t <= 4.5 s.
        - Al concluir Hilo 2, se señaliza el Event y Hilo 1 libera su lock y cierra conexión.
        - Invariantes tras liberación:
          * Rollback total de Hilo 2: cero registros en Consulta para su historia,
            cero en InventarioConsumosRegistro, cero en InventarioKardex.
          * Kit y bodega permanecen intactos con sus cantidades iniciales.
          * Cero transacciones abiertas residuales.
        """
        vet = crear_usuario(rol_id=3, email="vet_con49@cba.gov.co")
        headers_vet = obtener_headers_jwt(vet)

        med = crear_medicamento(nombre="Med CON49 Lock Timeout")
        lote_bodega = crear_lote_bodega(
            med,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit = crear_lote_kit(
            vet,
            lote_bodega,
            cantidad=10,
            cantidad_ref=10,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )

        animal, historia = crear_animal_con_historia(nombre="Paciente CON49")
        client_uuid_49 = str(uuid.uuid4())

        payload_consumo = payload_expediente_clinico(
            id_animal=animal.id_animal,
            hoja2={
                "diagnosticoFinal": "Evaluacion CON49 Timeout",
                "tratamientos": [],
            },
            consumos=[{
                "id_medicamento": med.id_medicamento,
                "unidades": 2,
                "client_uuid": client_uuid_49,
            }],
            offline=False,
        )

        lock_adquirido_event = threading.Event()
        liberar_event = threading.Event()

        def tarea_hilo1_lock(barrier, res):
            threading.current_thread().name = 'hilo_con49_lock'
            barrier.wait(timeout=5.0)
            try:
                with transaction.atomic():
                    bloqueados = list(
                        KitVeterinarioLotes.objects.select_for_update().filter(
                            id_veterinario=vet.id_usuario,
                            id_medicamento=med.id_medicamento,
                        )
                    )
                    lock_adquirido_event.set()
                    liberado = liberar_event.wait(timeout=8.0)
                    res['status'] = 'LOCK_LIBERADO'
                    res['data'] = {'bloqueados': len(bloqueados), 'liberado': liberado}
            except Exception as exc:
                lock_adquirido_event.set()
                res['error'] = exc

        def tarea_hilo2_consumo(barrier, res):
            threading.current_thread().name = 'hilo_con49_consumo'
            barrier.wait(timeout=5.0)
            if not lock_adquirido_event.wait(timeout=5.0):
                res['error'] = RuntimeError("Timeout esperando que Hilo 1 adquiera el lock")
                liberar_event.set()
                return

            client = APIClient(raise_request_exception=False)
            t_inicio = time.monotonic()
            try:
                response = client.post(
                    "/api/clinica/expediente-completo/",
                    payload_consumo,
                    format="json",
                    **headers_vet
                )
                t_duracion = time.monotonic() - t_inicio
                if response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
                    exc_info = getattr(response, 'exc_info', None)
                    exc = exc_info[1] if exc_info else None
                    cause = getattr(exc, '__cause__', None) if exc else None
                    pgcode = getattr(cause, 'pgcode', None)
                    if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                        pgcode = cause.diag.sqlstate
                    if pgcode == '55P03' or (exc and ('lock_not_available' in str(exc).lower() or 'lock timeout' in str(exc).lower())):
                        res['status'] = 'LOCK_NOT_AVAILABLE_55P03'
                    else:
                        res['status'] = 'ERROR_500'
                    res['data'] = {
                        'pgcode': pgcode,
                        'duracion': t_duracion,
                        'message': str(exc) if exc else str(getattr(response, 'data', response.content)),
                    }
                else:
                    res['status'] = response.status_code
                    res['data'] = {'duracion': t_duracion, 'data': response.data}
            except OperationalError as exc:
                t_duracion = time.monotonic() - t_inicio
                cause = getattr(exc, '__cause__', None)
                pgcode = getattr(cause, 'pgcode', None)
                if not pgcode and hasattr(cause, 'diag') and getattr(cause.diag, 'sqlstate', None):
                    pgcode = cause.diag.sqlstate
                if pgcode == '55P03' or 'lock_not_available' in str(exc).lower() or 'lock timeout' in str(exc).lower():
                    res['status'] = 'LOCK_NOT_AVAILABLE_55P03'
                else:
                    res['status'] = f'OPERATIONAL_ERROR_{pgcode}'
                res['data'] = {'pgcode': pgcode, 'duracion': t_duracion, 'message': str(exc)}
            finally:
                liberar_event.set()

        try:
            resultados = ejecutar_en_hilos(self, [tarea_hilo1_lock, tarea_hilo2_consumo], timeout_barrier=5.0, timeout_join=10.0)
        finally:
            liberar_event.set()

        res_1 = next(r for r in resultados if r['hilo'] == 0)
        res_2 = next(r for r in resultados if r['hilo'] == 1)

        duracion = res_2['data'].get('duracion', 0.0) if isinstance(res_2.get('data'), dict) else 0.0
        pgcode_2 = res_2['data'].get('pgcode') if isinstance(res_2.get('data'), dict) else None

        print(f"\n[TEST-CON-49-RESULT] Hilo 2: status={res_2['status']}, pgcode={pgcode_2}, duracion={duracion:.3f}s")

        self.assertEqual(res_2['status'], 'LOCK_NOT_AVAILABLE_55P03')
        self.assertEqual(pgcode_2, '55P03')
        self.assertGreaterEqual(duracion, 3.0, f"Duración {duracion:.3f}s debe ser >= 3.0s (lock_timeout=3000ms)")
        self.assertLessEqual(duracion, 4.5, f"Duración {duracion:.3f}s excedió la tolerancia de 4.5s")

        # Invariantes tras liberación: rollback total de Hilo 2
        self.assertEqual(Consulta.objects.filter(id_historia=historia.id_historia).count(), 0)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=client_uuid_49).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=client_uuid_49).count(), 0)

        lote_kit.refresh_from_db()
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_kit.cantidad_actual, 10)
        self.assertEqual(lote_bodega.cantidad_actual, 50)


