"""
Tests de integración para el ciclo de Solicitudes de Kit (Reposición y Devolución).
Fase C1 (Tarea 10): 13 casos (TEST-KIT-25 a TEST-KIT-32, incluyendo TEST-KIT-27F).

Especificación:
- plan/DISENO_TAREA10.md (§4.5, §5.3: Bloque C).
- plan/DECISIONES.md (§2, §4, §5, §7, §12).

Casos cubiertos:
- TEST-KIT-25: Solicitud de reposición y anti-solapamiento (409 SOLICITUD_DUPLICADA).
- TEST-KIT-26: Reposición total y rechazo por kit completo (409 KIT_COMPLETO).
- TEST-KIT-27: Aprobación total de reposición y conservación de referencia max(R, stock).
- TEST-KIT-27B: Rechazo de asignación vinculada a solicitud de otro veterinario (nivel servicio).
- TEST-KIT-27C: Rechazo de asignación vinculada a solicitud con tipo incorrecto (nivel servicio).
- TEST-KIT-27D: Rechazo de asignación vinculada a solicitud en estado no PENDIENTE (nivel servicio).
- TEST-KIT-27E: Rechazo al aprobar solicitud no PENDIENTE (RECHAZADA, APROBADA, PARCIAL).
- TEST-KIT-27F: HALLAZGO H-C1: id_solicitud es ignorado por el endpoint HTTP /asignar/.
- TEST-KIT-28: Aprobación parcial de reposición como estado final y bloqueo de reintento.
- TEST-KIT-29: Rechazo de solicitud de kit por gestor.
- TEST-KIT-30: Cancelación de solicitud por su dueño (200) y por otro veterinario (403).
- TEST-KIT-31: Devolución de vigentes, orden FIFO inverso y reintegro a lote agotado ('Disponible').
- TEST-KIT-32: Devolución con múltiples lotes vencidos (BAJA_VENCIMIENTO) y stock insuficiente.
"""

from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from inventario.constants import (
    SOLICITUD_TIPO_REPOSICION,
    SOLICITUD_TIPO_DEVOLUCION,
    SOLICITUD_ESTADO_PENDIENTE,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PARCIAL,
    SOLICITUD_ESTADO_RECHAZADA,
    SOLICITUD_ESTADO_CANCELADA,
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_DEVOLUCION_APROBADA,
    TIPO_KARDEX_BAJA_VENCIMIENTO,
    UBICACION_KARDEX_GENERAL,
    UBICACION_KARDEX_KIT,
)
from inventario.models import (
    KitVeterinarioLotes,
    SolicitudesKit,
    SolicitudesKitDetalles,
    InventarioKardex,
)
from inventario.services.kit_service import asignar_stock_a_kit
from inventario.services.exceptions import SolicitudInvalidaError
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_lote_kit,
)
from inventario.tests.helpers import obtener_headers_jwt


class SolicitudesFlujoTestCase(TestCase):
    """
    Suite de integración del ciclo completo de solicitudes del kit y reglas de DECISIONES §12.
    Ejecutada sobre transacciones aisladas con rollback (django.test.TestCase).
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.hoy = timezone.now().date()

        # Actores institucionales
        self.gestor = crear_usuario(rol_id=1, email="gestor_solicitudes@cba.gov.co")
        self.vet_a = crear_usuario(rol_id=3, email="vet_a_solicitudes@cba.gov.co")
        self.vet_b = crear_usuario(rol_id=3, email="vet_b_solicitudes@cba.gov.co")

        # Headers JWT
        self.headers_gestor = obtener_headers_jwt(self.gestor)
        self.headers_vet_a = obtener_headers_jwt(self.vet_a)
        self.headers_vet_b = obtener_headers_jwt(self.vet_b)

        # Catálogo farmacéutico
        self.med_amox = crear_medicamento(nombre="Amoxicilina 500mg Polvo")
        self.med_melo = crear_medicamento(nombre="Meloxicam 0.5% Solución")

    def test_kit_25_solicitud_reposicion_y_antisolapamiento(self):
        """
        TEST-KIT-25: Crear reposición específica (201) y verificar que un segundo intento
        con el mismo medicamento devuelve HTTP 409 Conflict (SOLICITUD_DUPLICADA).
        Verifica integridad de BD: SolicitudesKit en PENDIENTE, 0 kardex, bodega y kit intactos.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega,
            cantidad=5,
            cantidad_ref=20
        )

        payload1 = {
            "tipo_solicitud": SOLICITUD_TIPO_REPOSICION,
            "motivo": "Reposición por consumo en jornadas de esterilización",
            "items": [
                {
                    "id_medicamento": self.med_amox.id_medicamento,
                    "cantidad": 10
                }
            ]
        }

        # 1. Primer envío: creación exitosa (HTTP 201)
        response1 = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload1,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response1.data.get("tipo_solicitud"), SOLICITUD_TIPO_REPOSICION)
        self.assertEqual(response1.data.get("estado"), SOLICITUD_ESTADO_PENDIENTE)
        self.assertEqual(response1.data.get("id_veterinario"), self.vet_a.id_usuario)

        sol_id = response1.data["id_solicitud"]
        sol_bd = SolicitudesKit.objects.get(pk=sol_id)
        self.assertEqual(sol_bd.estado, SOLICITUD_ESTADO_PENDIENTE)
        self.assertEqual(sol_bd.tipo_solicitud, SOLICITUD_TIPO_REPOSICION)
        self.assertEqual(sol_bd.id_veterinario_id, self.vet_a.id_usuario)

        detalles = sol_bd.detalles.all()
        self.assertEqual(detalles.count(), 1)
        det = detalles.first()
        self.assertEqual(det.id_medicamento_id, self.med_amox.id_medicamento)
        self.assertEqual(det.cantidad_solicitada, 10)
        self.assertEqual(det.cantidad_aprobada, 0)
        self.assertFalse(det.es_vencido)

        # Bodega, kit y kardex permanecen inalterados tras la creación de la solicitud
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 50)
        lote_kit.refresh_from_db()
        self.assertEqual(lote_kit.cantidad_actual, 5)
        self.assertEqual(InventarioKardex.objects.count(), 0)

        # 2. Segundo envío del mismo medicamento por el mismo veterinario: HTTP 409
        payload2 = {
            "tipo_solicitud": SOLICITUD_TIPO_REPOSICION,
            "motivo": "Segundo intento para el mismo medicamento",
            "items": [
                {
                    "id_medicamento": self.med_amox.id_medicamento,
                    "cantidad": 5
                }
            ]
        }
        response2 = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload2,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(response2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response2.data.get("error"), "SOLICITUD_DUPLICADA")
        self.assertIn(
            f"Ya existe una solicitud de reposición pendiente que incluye el medicamento #{self.med_amox.id_medicamento}",
            response2.data.get("detalle", "")
        )

        # Base de datos: exactamente 1 solicitud registrada
        self.assertEqual(
            SolicitudesKit.objects.filter(id_veterinario=self.vet_a).count(),
            1
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_26_reposicion_total_kit_completo(self):
        """
        TEST-KIT-26: Reposición total (es_reposicion_total=True) cuando el kit cuenta con stock
        al 100% de su cantidad de referencia.
        Debe fallar con HTTP 409 Conflict, error='KIT_COMPLETO', y 0 solicitudes creadas en BD.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_melo,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=120)
        )
        # Kit al 100% (stock vigente == cantidad_referencia)
        crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega,
            cantidad=10,
            cantidad_ref=10,
            fecha_vencimiento=self.hoy + timedelta(days=120)
        )

        payload = {
            "tipo_solicitud": SOLICITUD_TIPO_REPOSICION,
            "motivo": "Reposición total solicitada",
            "es_reposicion_total": True
        }

        response = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("error"), "KIT_COMPLETO")
        self.assertIn(
            "El kit no requiere reposición: todos los medicamentos vigentes cuentan con stock completo",
            response.data.get("detalle", "")
        )

        # Cero solicitudes creadas en base de datos
        self.assertEqual(SolicitudesKit.objects.count(), 0)
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_27_aprobar_reposicion_total_y_conservacion_referencia(self):
        """
        TEST-KIT-27: Aprobación total de reposición por Gestor: kit sube, bodega baja,
        referencia se conserva en N = max(R, stock_vigente) = 20, y se generan exactamente
        2 filas de kardex ASIGNACION_KIT (1 GENERAL salida, 1 KIT_VETERINARIO entrada).
        Compara con DECISIONES §12.2.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra=self.hoy - timedelta(days=30)
        )
        lote_kit = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega,
            cantidad=10,
            cantidad_ref=20,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra_origen=self.hoy - timedelta(days=30)
        )

        # Crear solicitud de reposición de 10 unidades
        sol = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Reposición de 10 unidades para igualar referencia",
            es_reposicion_total=False
        )
        det = SolicitudesKitDetalles.objects.create(
            id_solicitud=sol,
            id_medicamento=self.med_amox,
            id_inventario_origen=None,
            cantidad_solicitada=10,
            cantidad_aprobada=0,
            es_vencido=False
        )

        # Gestor aprueba la solicitud
        url = f"/api/inventario/kit/solicitudes/{sol.id_solicitud}/aprobar/"
        payload = {
            "observaciones_gestor": "Aprobación total autorizada por stock suficiente"
        }
        response = self.client.post(
            url,
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("estado"), SOLICITUD_ESTADO_APROBADA)
        self.assertEqual(response.data.get("id_solicitud"), sol.id_solicitud)

        # Estado en base de datos: SolicitudesKit
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_APROBADA)
        self.assertEqual(sol.id_gestor_id, self.gestor.id_usuario)
        self.assertIsNotNone(sol.fecha_resolucion)
        det.refresh_from_db()
        self.assertEqual(det.cantidad_aprobada, 10)

        # Bodega descuenta 10 unidades
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 40)

        # Kit sube a 20 unidades
        lote_kit.refresh_from_db()
        self.assertEqual(lote_kit.cantidad_actual, 20)

        # Conservación de referencia bajo lock (DECISIONES §12.2):
        # ref_previa = 20, nuevo stock_vigente = 20 => N = max(20, 20) = 20.
        # No debe inflarse a 30 ni duplicarse.
        total_ref = sum(
            kl.cantidad_referencia
            for kl in KitVeterinarioLotes.objects.filter(
                id_veterinario=self.vet_a,
                id_medicamento=self.med_amox
            )
        )
        self.assertEqual(total_ref, 20)
        self.assertEqual(lote_kit.cantidad_referencia, 20)

        # Kardex: exactamente 2 filas ASIGNACION_KIT
        kardex_rows = InventarioKardex.objects.filter(
            id_medicamento=self.med_amox,
            tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT
        ).order_by("id_kardex")
        self.assertEqual(kardex_rows.count(), 2)

        k_gen = kardex_rows.filter(ubicacion=UBICACION_KARDEX_GENERAL).first()
        self.assertIsNotNone(k_gen)
        self.assertEqual(k_gen.cantidad, 10)
        self.assertEqual(k_gen.saldo_anterior, 50)
        self.assertEqual(k_gen.saldo_resultante, 40)
        self.assertEqual(k_gen.id_usuario_ejecuta_id, self.gestor.id_usuario)
        self.assertEqual(k_gen.id_solicitud_kit_id, sol.id_solicitud)

        k_kit = kardex_rows.filter(ubicacion=UBICACION_KARDEX_KIT).first()
        self.assertIsNotNone(k_kit)
        self.assertEqual(k_kit.cantidad, 10)
        self.assertEqual(k_kit.saldo_anterior, 10)
        self.assertEqual(k_kit.saldo_resultante, 20)
        self.assertEqual(k_kit.id_usuario_ejecuta_id, self.gestor.id_usuario)
        self.assertEqual(k_kit.id_solicitud_kit_id, sol.id_solicitud)

    def test_kit_27b_rechazo_asignacion_solicitud_otro_veterinario(self):
        """
        TEST-KIT-27B: Rechazo de asignación vinculada a solicitud de otro veterinario.
        Llamada a nivel de servicio kit_service.asignar_stock_a_kit con id_solicitud de otro veterinario.
        Debe lanzar SolicitudInvalidaError con código 'SOLICITUD_INVALIDA'.
        Afirma que bodega, kit, solicitud y kardex permanecen intactos.

        Nota arquitectural: A nivel HTTP este flujo no es alcanzable actualmente porque el serializer
        AsignarStockKitSerializer no expone el campo id_solicitud (ver HALLAZGO H-C1 en test_kit_27f).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol_vet_a = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Solicitud original Vet A",
            es_reposicion_total=False
        )
        SolicitudesKitDetalles.objects.create(
            id_solicitud=sol_vet_a,
            id_medicamento=self.med_amox,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False
        )

        with self.assertRaises(SolicitudInvalidaError) as ctx:
            asignar_stock_a_kit(
                id_gestor=self.gestor.id_usuario,
                id_veterinario=self.vet_b.id_usuario,
                items=[
                    {
                        "id_medicamento": self.med_amox.id_medicamento,
                        "cantidad": 5,
                    }
                ],
                motivo="Asignación cruzada con solicitud de otro veterinario",
                id_solicitud=sol_vet_a.id_solicitud,
            )

        self.assertEqual(ctx.exception.codigo, "SOLICITUD_INVALIDA")
        self.assertIn(
            f"La solicitud #{sol_vet_a.id_solicitud} pertenece al veterinario #{self.vet_a.id_usuario}, no al veterinario #{self.vet_b.id_usuario} especificado.",
            str(ctx.exception)
        )

        # Invariantes en base de datos:
        sol_vet_a.refresh_from_db()
        self.assertEqual(sol_vet_a.estado, SOLICITUD_ESTADO_PENDIENTE)
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 50)
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_b).count(),
            0
        )
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
            0
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_27c_rechazo_asignacion_solicitud_tipo_devolucion(self):
        """
        TEST-KIT-27C: Rechazo de asignación vinculada a solicitud con tipo incorrecto (DEVOLUCION).
        Llamada a nivel de servicio kit_service.asignar_stock_a_kit con id_solicitud de tipo DEVOLUCION.
        Debe lanzar SolicitudInvalidaError con código 'SOLICITUD_INVALIDA'.
        Afirma que bodega, kit, solicitud y kardex permanecen intactos.

        Nota arquitectural: A nivel HTTP este flujo no es alcanzable actualmente porque el serializer
        AsignarStockKitSerializer no expone el campo id_solicitud (ver HALLAZGO H-C1 en test_kit_27f).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol_dev = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_DEVOLUCION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Devolución pendiente",
            es_reposicion_total=False
        )
        SolicitudesKitDetalles.objects.create(
            id_solicitud=sol_dev,
            id_medicamento=self.med_amox,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False
        )

        with self.assertRaises(SolicitudInvalidaError) as ctx:
            asignar_stock_a_kit(
                id_gestor=self.gestor.id_usuario,
                id_veterinario=self.vet_a.id_usuario,
                items=[
                    {
                        "id_medicamento": self.med_amox.id_medicamento,
                        "cantidad": 5,
                    }
                ],
                motivo="Asignación referenciando solicitud de devolución",
                id_solicitud=sol_dev.id_solicitud,
            )

        self.assertEqual(ctx.exception.codigo, "SOLICITUD_INVALIDA")
        self.assertIn(
            f"La solicitud #{sol_dev.id_solicitud} no es de tipo {SOLICITUD_TIPO_REPOSICION} (tipo actual: {SOLICITUD_TIPO_DEVOLUCION}).",
            str(ctx.exception)
        )

        # Invariantes en base de datos:
        sol_dev.refresh_from_db()
        self.assertEqual(sol_dev.estado, SOLICITUD_ESTADO_PENDIENTE)
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 50)
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
            0
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_27d_rechazo_asignacion_solicitud_estado_no_pendiente(self):
        """
        TEST-KIT-27D: Rechazo de asignación vinculada a solicitud en estado no PENDIENTE.
        Llamada a nivel de servicio kit_service.asignar_stock_a_kit con id_solicitud en estado RECHAZADA.
        Debe lanzar SolicitudInvalidaError con código 'SOLICITUD_INVALIDA'.
        Afirma que bodega, kit, solicitud y kardex permanecen intactos.

        Nota arquitectural: A nivel HTTP este flujo no es alcanzable actualmente porque el serializer
        AsignarStockKitSerializer no expone el campo id_solicitud (ver HALLAZGO H-C1 en test_kit_27f).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol_cerrada = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_RECHAZADA,
            id_veterinario=self.vet_a,
            id_gestor=self.gestor,
            motivo_solicitud="Reposición rechazada previamente",
            observaciones_gestor="Rechazo previo",
            fecha_resolucion=timezone.now(),
            es_reposicion_total=False
        )
        SolicitudesKitDetalles.objects.create(
            id_solicitud=sol_cerrada,
            id_medicamento=self.med_amox,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False
        )

        with self.assertRaises(SolicitudInvalidaError) as ctx:
            asignar_stock_a_kit(
                id_gestor=self.gestor.id_usuario,
                id_veterinario=self.vet_a.id_usuario,
                items=[
                    {
                        "id_medicamento": self.med_amox.id_medicamento,
                        "cantidad": 5,
                    }
                ],
                motivo="Asignación referenciando solicitud ya rechazada",
                id_solicitud=sol_cerrada.id_solicitud,
            )

        self.assertEqual(ctx.exception.codigo, "SOLICITUD_INVALIDA")
        self.assertIn(
            f"La solicitud #{sol_cerrada.id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} (estado actual: {SOLICITUD_ESTADO_RECHAZADA}).",
            str(ctx.exception)
        )

        # Invariantes en base de datos:
        sol_cerrada.refresh_from_db()
        self.assertEqual(sol_cerrada.estado, SOLICITUD_ESTADO_RECHAZADA)
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 50)
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
            0
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_27e_rechazo_aprobar_solicitud_estado_no_pendiente(self):
        """
        TEST-KIT-27E: Rechazo al intentar aprobar una solicitud que no está en estado PENDIENTE.
        Evalúa estados finales RECHAZADA, APROBADA y PARCIAL (un subTest cada uno).
        Debe fallar con HTTP 409 Conflict, error='SOLICITUD_ESTADO_INVALIDA', y el estado
        de la solicitud debe permanecer inmutable; bodega, kit y kardex intactos.
        """
        estados_finales = [
            SOLICITUD_ESTADO_RECHAZADA,
            SOLICITUD_ESTADO_APROBADA,
            SOLICITUD_ESTADO_PARCIAL,
        ]

        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )

        for estado_cerrado in estados_finales:
            with self.subTest(estado_cerrado=estado_cerrado):
                sol = SolicitudesKit.objects.create(
                    tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
                    estado=estado_cerrado,
                    id_veterinario=self.vet_a,
                    id_gestor=self.gestor,
                    motivo_solicitud=f"Solicitud cerrada en {estado_cerrado}",
                    fecha_resolucion=timezone.now(),
                    es_reposicion_total=False
                )
                det = SolicitudesKitDetalles.objects.create(
                    id_solicitud=sol,
                    id_medicamento=self.med_amox,
                    cantidad_solicitada=10,
                    cantidad_aprobada=5 if estado_cerrado == SOLICITUD_ESTADO_PARCIAL else 0,
                    es_vencido=False
                )

                url = f"/api/inventario/kit/solicitudes/{sol.id_solicitud}/aprobar/"
                response = self.client.post(
                    url,
                    {"observaciones_gestor": "Intento de re-aprobación"},
                    format="json",
                    **self.headers_gestor
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(response.data.get("error"), "SOLICITUD_ESTADO_INVALIDA")
                self.assertIn(
                    f"La solicitud #{sol.id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} (estado actual: {estado_cerrado})",
                    response.data.get("detalle", "")
                )

                # Invariantes en base de datos:
                sol.refresh_from_db()
                self.assertEqual(sol.estado, estado_cerrado)
                lote_bodega.refresh_from_db()
                self.assertEqual(lote_bodega.cantidad_actual, 50)
                self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_27f_http_asignar_ignora_id_solicitud(self):
        """
        HALLAZGO H-C1: id_solicitud es ignorado por el endpoint.
        Demuestra que al invocar POST /api/inventario/kit/asignar/ enviando id_solicitud
        (incluso si pertenece a otro veterinario), la API devuelve HTTP 201 Created porque
        AsignarStockKitSerializer no define id_solicitud y la vista KitAsignarStockView
        lo ignora, ejecutando una asignación libre sin vincular la solicitud.
        Afirma que la solicitud original de Vet A permanece PENDIENTE y sin cambios,
        y que se creó una asignación libre de stock para Vet B.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol_vet_a = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Solicitud original Vet A",
            es_reposicion_total=False
        )
        det_sol_a = SolicitudesKitDetalles.objects.create(
            id_solicitud=sol_vet_a,
            id_medicamento=self.med_amox,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False
        )

        payload = {
            "id_veterinario": self.vet_b.id_usuario,
            "motivo": "Asignación libre que envía id_solicitud ignorado por endpoint",
            "items": [
                {
                    "id_medicamento": self.med_amox.id_medicamento,
                    "cantidad": 5
                }
            ],
            "id_solicitud": sol_vet_a.id_solicitud
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        # La API HTTP responde 201 Created (ignora id_solicitud)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # La solicitud de Vet A sigue intacta en estado PENDIENTE
        sol_vet_a.refresh_from_db()
        self.assertEqual(sol_vet_a.estado, SOLICITUD_ESTADO_PENDIENTE)
        det_sol_a.refresh_from_db()
        self.assertEqual(det_sol_a.cantidad_aprobada, 0)

        # Se ejecutó una asignación libre para Vet B
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 45)
        kl_vet_b = KitVeterinarioLotes.objects.get(
            id_veterinario=self.vet_b,
            id_medicamento=self.med_amox
        )
        self.assertEqual(kl_vet_b.cantidad_actual, 5)

        # En Kardex, las filas generadas NO tienen vinculación a la solicitud (id_solicitud_kit is None)
        kardex_rows = InventarioKardex.objects.filter(id_medicamento=self.med_amox)
        self.assertEqual(kardex_rows.count(), 2)
        for k in kardex_rows:
            self.assertIsNone(k.id_solicitud_kit_id)

    def test_kit_28_aprobacion_parcial_estado_final(self):
        """
        TEST-KIT-28: Aprobación parcial de reposición como estado final.
        El gestor aprueba 6 de las 10 unidades solicitadas.
        La solicitud pasa a PARCIAL (estado definitivo).
        Un reintento posterior de aprobar debe fallar con HTTP 409 Conflict (SOLICITUD_ESTADO_INVALIDA).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Reposición de 10 unidades",
            es_reposicion_total=False
        )
        det = SolicitudesKitDetalles.objects.create(
            id_solicitud=sol,
            id_medicamento=self.med_amox,
            cantidad_solicitada=10,
            cantidad_aprobada=0,
            es_vencido=False
        )

        url = f"/api/inventario/kit/solicitudes/{sol.id_solicitud}/aprobar/"
        payload = {
            "observaciones_gestor": "Aprobación parcial de 6 unidades por cuota semanal",
            "aprobaciones": [
                {
                    "id_detalle_solicitud": det.id_detalle_solicitud,
                    "cantidad_aprobada": 6
                }
            ]
        }

        # 1. Aprobación parcial exitosa (HTTP 200)
        response1 = self.client.post(
            url,
            payload,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response1.data.get("estado"), SOLICITUD_ESTADO_PARCIAL)
        self.assertEqual(response1.data.get("id_solicitud"), sol.id_solicitud)

        # Verificaciones en BD
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_PARCIAL)
        self.assertEqual(sol.id_gestor_id, self.gestor.id_usuario)
        self.assertIsNotNone(sol.fecha_resolucion)
        det.refresh_from_db()
        self.assertEqual(det.cantidad_aprobada, 6)

        # Bodega descuenta 6 unidades (50 -> 44)
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 44)

        # Kit recibe 6 unidades
        kl = KitVeterinarioLotes.objects.get(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_amox
        )
        self.assertEqual(kl.cantidad_actual, 6)

        # Kardex: 2 filas ASIGNACION_KIT con cantidad = 6
        kardex_rows = InventarioKardex.objects.filter(id_solicitud_kit=sol)
        self.assertEqual(kardex_rows.count(), 2)
        for k in kardex_rows:
            self.assertEqual(k.cantidad, 6)
            self.assertEqual(k.tipo_movimiento, TIPO_KARDEX_ASIGNACION_KIT)

        # 2. Reintento de aprobación: falla con HTTP 409 Conflict
        response2 = self.client.post(
            url,
            payload,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(response2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response2.data.get("error"), "SOLICITUD_ESTADO_INVALIDA")
        self.assertIn(
            f"La solicitud #{sol.id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} (estado actual: {SOLICITUD_ESTADO_PARCIAL})",
            response2.data.get("detalle", "")
        )

        # Estado en BD inalterado tras el reintento
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_PARCIAL)
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 44)
        self.assertEqual(InventarioKardex.objects.filter(id_solicitud_kit=sol).count(), 2)

    def test_kit_29_rechazar_solicitud_gestor(self):
        """
        TEST-KIT-29: Rechazo de solicitud de kit por Gestor.
        Pasa a RECHAZADA, cantidad_aprobada queda en 0, observaciones registradas,
        bodega y kit intactos, cero filas de kardex.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        sol = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Reposición urgente",
            es_reposicion_total=False
        )
        det = SolicitudesKitDetalles.objects.create(
            id_solicitud=sol,
            id_medicamento=self.med_amox,
            cantidad_solicitada=10,
            cantidad_aprobada=0,
            es_vencido=False
        )

        url = f"/api/inventario/kit/solicitudes/{sol.id_solicitud}/rechazar/"
        payload = {
            "observaciones_gestor": "Stock reservado para emergencias clínicas institucionales"
        }

        response = self.client.post(
            url,
            payload,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("estado"), SOLICITUD_ESTADO_RECHAZADA)
        self.assertEqual(
            response.data.get("observaciones_gestor"),
            "Stock reservado para emergencias clínicas institucionales"
        )

        # Estado en BD
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_RECHAZADA)
        self.assertEqual(sol.id_gestor_id, self.gestor.id_usuario)
        self.assertIsNotNone(sol.fecha_resolucion)
        det.refresh_from_db()
        self.assertEqual(det.cantidad_aprobada, 0)

        # Bodega y kit permanecen intactos; cero kardex
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 50)
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
            0
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_30_cancelar_solicitud_duenio_y_otro_veterinario(self):
        """
        TEST-KIT-30: Cancelación de solicitud por su dueño (HTTP 200, CANCELADA)
        y por otro veterinario no titular (HTTP 403, SOLICITUD_PERMISO_DENEGADO).
        Afirma el comportamiento real del código contra el diseño.
        """
        sol = SolicitudesKit.objects.create(
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE,
            id_veterinario=self.vet_a,
            motivo_solicitud="Solicitud de Vet A para cancelar",
            es_reposicion_total=False
        )
        SolicitudesKitDetalles.objects.create(
            id_solicitud=sol,
            id_medicamento=self.med_amox,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False
        )

        url = f"/api/inventario/kit/solicitudes/{sol.id_solicitud}/cancelar/"

        # 1. Vet B intenta cancelar la solicitud de Vet A: HTTP 403 Forbidden
        response_vet_b = self.client.post(
            url,
            format="json",
            **self.headers_vet_b
        )
        self.assertEqual(response_vet_b.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response_vet_b.data.get("error"), "SOLICITUD_PERMISO_DENEGADO")
        self.assertIn(
            f"La solicitud #{sol.id_solicitud} no pertenece al veterinario #{self.vet_b.id_usuario}",
            response_vet_b.data.get("detalle", "")
        )

        # En BD la solicitud sigue PENDIENTE
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_PENDIENTE)

        # 2. Vet A (su dueño) cancela su propia solicitud: HTTP 200 OK
        response_vet_a = self.client.post(
            url,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(response_vet_a.status_code, status.HTTP_200_OK)
        self.assertEqual(response_vet_a.data.get("estado"), SOLICITUD_ESTADO_CANCELADA)

        # En BD la solicitud pasa a CANCELADA con fecha_resolucion
        sol.refresh_from_db()
        self.assertEqual(sol.estado, SOLICITUD_ESTADO_CANCELADA)
        self.assertIsNotNone(sol.fecha_resolucion)
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_31_devolucion_vigentes_fifo_inverso_y_reintegro_agotado(self):
        """
        TEST-KIT-31: Solicitud y aprobación de devolución de stock vigente:
        - Orden FIFO inverso: el lote más reciente (compra más reciente) se reintegra primero.
        - Kardex DEVOLUCION_APROBADA (exactamente 2 filas por lote tocado: KIT salida, GENERAL entrada).
        - Bodega se incrementa con las unidades devueltas.
        - Un lote en bodega que estaba en 0 unidades con estado 'Agotado' pasa a 'Disponible'.
        """
        # Bodega: Lote 1 compra antigua (hace 60 días), Lote 2 compra reciente (hace 10 días, agotado)
        lote_bodega_1 = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=20,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra=self.hoy - timedelta(days=60),
            estado="Disponible"
        )
        lote_bodega_2 = crear_lote_bodega(
            medicamento=self.med_amox,
            cantidad=0,
            fecha_vencimiento=self.hoy + timedelta(days=120),
            fecha_compra=self.hoy - timedelta(days=10),
            estado="Agotado"
        )

        # Kit del veterinario: 5 unidades del Lote 1 y 5 unidades del Lote 2
        lote_kit_1 = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega_1,
            cantidad=5,
            cantidad_ref=5,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra_origen=self.hoy - timedelta(days=60)
        )
        lote_kit_2 = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega_2,
            cantidad=5,
            cantidad_ref=5,
            fecha_vencimiento=self.hoy + timedelta(days=120),
            fecha_compra_origen=self.hoy - timedelta(days=10)
        )

        # Veterinario solicita devolución de 5 unidades vigentes
        payload_crear = {
            "tipo_solicitud": SOLICITUD_TIPO_DEVOLUCION,
            "motivo": "Devolución de excedente de stock vigente",
            "items": [
                {
                    "id_medicamento": self.med_amox.id_medicamento,
                    "cantidad": 5
                }
            ]
        }
        res_crear = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload_crear,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(res_crear.status_code, status.HTTP_201_CREATED)
        sol_id = res_crear.data["id_solicitud"]

        # Gestor aprueba la devolución al 100%
        url_aprobar = f"/api/inventario/kit/solicitudes/{sol_id}/aprobar/"
        res_aprobar = self.client.post(
            url_aprobar,
            {"observaciones_gestor": "Devolución aceptada e ingresada a bodega"},
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_aprobar.status_code, status.HTTP_200_OK)
        self.assertEqual(res_aprobar.data.get("estado"), SOLICITUD_ESTADO_APROBADA)

        # Verificación de FIFO inverso en el kit:
        # Lote 2 (compra reciente, -10 días) debe vaciarse primero (5 -> 0).
        # Lote 1 (compra antigua, -60 días) debe quedar intacto (5 unidades).
        lote_kit_2.refresh_from_db()
        self.assertEqual(lote_kit_2.cantidad_actual, 0)
        lote_kit_1.refresh_from_db()
        self.assertEqual(lote_kit_1.cantidad_actual, 5)

        # Verificación en bodega:
        # Lote Bodega 2 sube de 0 a 5 y su estado pasa de 'Agotado' a 'Disponible'
        lote_bodega_2.refresh_from_db()
        self.assertEqual(lote_bodega_2.cantidad_actual, 5)
        self.assertEqual(lote_bodega_2.estado, "Disponible")

        # Lote Bodega 1 queda intacto (20 unidades)
        lote_bodega_1.refresh_from_db()
        self.assertEqual(lote_bodega_1.cantidad_actual, 20)

        # Kardex: exactamente 2 filas de DEVOLUCION_APROBADA
        kardex_rows = InventarioKardex.objects.filter(
            id_solicitud_kit_id=sol_id,
            tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA
        ).order_by("id_kardex")
        self.assertEqual(kardex_rows.count(), 2)

        k_kit = kardex_rows.filter(ubicacion=UBICACION_KARDEX_KIT).first()
        self.assertIsNotNone(k_kit)
        self.assertEqual(k_kit.cantidad, 5)
        self.assertEqual(k_kit.saldo_anterior, 5)
        self.assertEqual(k_kit.saldo_resultante, 0)
        self.assertEqual(k_kit.id_inventario_id, lote_bodega_2.id_inventario)

        k_gen = kardex_rows.filter(ubicacion=UBICACION_KARDEX_GENERAL).first()
        self.assertIsNotNone(k_gen)
        self.assertEqual(k_gen.cantidad, 5)
        self.assertEqual(k_gen.saldo_anterior, 0)
        self.assertEqual(k_gen.saldo_resultante, 5)
        self.assertEqual(k_gen.id_inventario_id, lote_bodega_2.id_inventario)

    def test_kit_32_devolucion_multiples_vencidos_y_stock_insuficiente(self):
        """
        TEST-KIT-32: Devolución con 2 lotes vencidos (2 y 3 unidades) en el kit:
        - Con es_vencido=True y cantidad=5: genera exactamente 2 filas BAJA_VENCIMIENTO en
          KIT_VETERINARIO (1 por lote), bodega general permanece SIN CAMBIO.
        - Si se piden 6 unidades (disponibles solo 5): falla con HTTP 400 Bad Request,
          error='STOCK_INSUFICIENTE_KIT' (afirma el status real).

        Nota: es_vencido=True se fija por ORM porque la API no lo expone al crear (el serializer
        de creación SolicitudKitItemCreateSerializer no expone el campo es_vencido).
        """
        lote_bodega_1 = crear_lote_bodega(
            medicamento=self.med_melo,
            cantidad=50,
            fecha_vencimiento=self.hoy - timedelta(days=15)
        )
        lote_bodega_2 = crear_lote_bodega(
            medicamento=self.med_melo,
            cantidad=50,
            fecha_vencimiento=self.hoy - timedelta(days=5)
        )

        # Dos lotes vencidos en kit: Lote 1 con 2 uds, Lote 2 con 3 uds (total vencido = 5)
        lote_vencido_1 = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega_1,
            cantidad=2,
            cantidad_ref=2,
            fecha_vencimiento=self.hoy - timedelta(days=15)
        )
        lote_vencido_2 = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega_2,
            cantidad=3,
            cantidad_ref=3,
            fecha_vencimiento=self.hoy - timedelta(days=5)
        )

        # 1. Intento de solicitar 6 unidades cuando solo hay 5 vencidas en kit:
        # Falla con HTTP 400 Bad Request (STOCK_INSUFICIENTE_KIT)
        payload_excedido = {
            "tipo_solicitud": SOLICITUD_TIPO_DEVOLUCION,
            "motivo": "Intento de devolver 6 unidades cuando hay 5",
            "items": [
                {
                    "id_medicamento": self.med_melo.id_medicamento,
                    "cantidad": 6
                }
            ]
        }
        res_excedido = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload_excedido,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(res_excedido.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_excedido.data.get("error"), "STOCK_INSUFICIENTE_KIT")

        # Cero solicitudes creadas tras el fallo
        self.assertEqual(SolicitudesKit.objects.count(), 0)

        # 2. Devolución de 5 unidades con es_vencido=True
        # Creación válida de la solicitud para las 5 unidades disponibles
        payload_valido = {
            "tipo_solicitud": SOLICITUD_TIPO_DEVOLUCION,
            "motivo": "Devolución de 5 unidades vencidas para destrucción",
            "items": [
                {
                    "id_medicamento": self.med_melo.id_medicamento,
                    "cantidad": 5
                }
            ]
        }
        res_crear = self.client.post(
            "/api/inventario/kit/solicitudes/",
            payload_valido,
            format="json",
            **self.headers_vet_a
        )
        self.assertEqual(res_crear.status_code, status.HTTP_201_CREATED)
        sol_id = res_crear.data["id_solicitud"]

        # Se marca explícitamente es_vencido=True en el detalle de la solicitud
        det = SolicitudesKitDetalles.objects.get(id_solicitud_id=sol_id)
        det.es_vencido = True
        det.save(update_fields=["es_vencido"])

        # 3. Sub-test: si durante la aprobación se intentara aprobar 6 unidades con es_vencido=True
        # el servicio de kit lanza StockInsuficienteKitError (HTTP 400 Bad Request)
        with self.subTest(prueba="aprobacion_excedida_vencidos"):
            sol_6 = SolicitudesKit.objects.create(
                tipo_solicitud=SOLICITUD_TIPO_DEVOLUCION,
                estado=SOLICITUD_ESTADO_PENDIENTE,
                id_veterinario=self.vet_a,
                motivo_solicitud="Devolución 6 vencidos",
                es_reposicion_total=False
            )
            det_6 = SolicitudesKitDetalles.objects.create(
                id_solicitud=sol_6,
                id_medicamento=self.med_melo,
                cantidad_solicitada=6,
                cantidad_aprobada=0,
                es_vencido=True
            )
            res_aprob_6 = self.client.post(
                f"/api/inventario/kit/solicitudes/{sol_6.id_solicitud}/aprobar/",
                {
                    "aprobaciones": [
                        {
                            "id_detalle_solicitud": det_6.id_detalle_solicitud,
                            "cantidad_aprobada": 6
                        }
                    ]
                },
                format="json",
                **self.headers_gestor
            )
            self.assertEqual(res_aprob_6.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res_aprob_6.data.get("error"), "STOCK_INSUFICIENTE_KIT")

        # 4. Aprobación exitosa de las 5 unidades vencidas
        url_aprobar = f"/api/inventario/kit/solicitudes/{sol_id}/aprobar/"
        res_aprobar = self.client.post(
            url_aprobar,
            {"observaciones_gestor": "Aprobación y registro de baja por vencimiento"},
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_aprobar.status_code, status.HTTP_200_OK)
        self.assertEqual(res_aprobar.data.get("estado"), SOLICITUD_ESTADO_APROBADA)

        # Ambos lotes vencidos quedan en 0 en el kit
        lote_vencido_1.refresh_from_db()
        self.assertEqual(lote_vencido_1.cantidad_actual, 0)
        lote_vencido_2.refresh_from_db()
        self.assertEqual(lote_vencido_2.cantidad_actual, 0)

        # Bodega general permanece SIN CAMBIO (las unidades vencidas no reingresan a bodega)
        lote_bodega_1.refresh_from_db()
        self.assertEqual(lote_bodega_1.cantidad_actual, 50)
        lote_bodega_2.refresh_from_db()
        self.assertEqual(lote_bodega_2.cantidad_actual, 50)

        # Kardex: exactamente 2 filas de BAJA_VENCIMIENTO, ambas en KIT_VETERINARIO
        kardex_rows = InventarioKardex.objects.filter(
            id_solicitud_kit_id=sol_id,
            tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO
        ).order_by("id_kardex")
        self.assertEqual(kardex_rows.count(), 2)

        # Ninguna fila en ubicación GENERAL
        self.assertEqual(
            kardex_rows.filter(ubicacion=UBICACION_KARDEX_GENERAL).count(),
            0
        )

        k1 = kardex_rows.filter(id_inventario=lote_vencido_1.id_inventario).first()
        self.assertIsNotNone(k1)
        self.assertEqual(k1.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k1.cantidad, 2)
        self.assertEqual(k1.saldo_anterior, 2)
        self.assertEqual(k1.saldo_resultante, 0)
        self.assertEqual(k1.id_usuario_ejecuta_id, self.gestor.id_usuario)

        k2 = kardex_rows.filter(id_inventario=lote_vencido_2.id_inventario).first()
        self.assertIsNotNone(k2)
        self.assertEqual(k2.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k2.cantidad, 3)
        self.assertEqual(k2.saldo_anterior, 3)
        self.assertEqual(k2.saldo_resultante, 0)
        self.assertEqual(k2.id_usuario_ejecuta_id, self.gestor.id_usuario)
