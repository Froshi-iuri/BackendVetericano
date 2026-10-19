"""
Tests de integración para los enganches clínicos del Kit de Veterinario (Fase D1b - Tarea 10).
Cubre los escenarios TEST-CLI-01 a TEST-CLI-17 (21 tests).

Especificación y Contratos:
- plan/DISENO_TAREA10.md (§1.5, §3.4, §3.5, §4.5, §4.7, §5.1: Bloque A).
- clinica/api/views.py (ExpedienteCompletoAPIView, KitErrorHandlingMixin).
- clinica/api/serializers.py (ExpedienteCompletoSerializer).
- peticiones/api/views.py (SeguimientoPeticionesVisitaCreateView).
- peticiones/api/serializers.py (SeguimientoPeticionesVisitaSerializer).
- inventario/services/consumo_service.py (descontar_consumo_kit).
- inventario/api/kit_serializers.py (validar_payload_consumo_kit, verificar_idempotencia_consumo_clinico).
- inventario/constants.py.
"""

import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from animales.models import Animal
from clinica.models import HistoriaClinica, Consulta
from peticiones.models import (
    SeguimientoPeticionesVisita,
    SeguimientoVisitaFuncionarios,
    EvidenciaSeguimientoVisita,
)
from inventario.constants import (
    TIPO_KARDEX_CONSUMO_HISTORIA,
    TIPO_KARDEX_CONSUMO_ACTA,
    UBICACION_KARDEX_KIT,
    CONSUMO_ESTADO_APLICADO,
    CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
    CONSUMO_ORIGEN_HISTORIA,
    CONSUMO_ORIGEN_DIRECTO,
)
from inventario.models import (
    InventarioKardex,
    InventarioConsumosRegistro,
)
from inventario.services import consumo_service
from inventario.services.exceptions import ConsumoInvalidoError
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_lote_kit,
    crear_animal_con_historia,
    crear_peticion_visita,
    crear_consulta,
)
from inventario.tests.helpers import (
    obtener_headers_jwt,
    payload_expediente_clinico,
    payload_acta_visita,
    assert_kardex_consistente,
    assert_consumo_registro,
)


class KitClinicaHookTestCase(TestCase):
    """
    Suite de integración para los enganches entre el Kit de Veterinario y los módulos clínicos.
    Valida descuentos, idempotencia, rollbacks y sincronización offline en Historia Clínica y Actas de Visita.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.hoy = timezone.now().date()

        # Veterinario titular autenticado (Rol 3)
        self.vet = crear_usuario(rol_id=3, email="vet_hook@cba.gov.co")
        self.headers_vet = obtener_headers_jwt(self.vet)

        # Insumos farmacéuticos de prueba
        self.med_a = crear_medicamento(nombre="Amoxicilina Clinica Hook")
        self.med_b = crear_medicamento(nombre="Meloxicam Clinica Hook")

    def test_cli_01_expediente_completo_online_descuento_exitoso(self):
        """
        TEST-CLI-01: Primer envío online de consulta clínica con descuento kit (Regresión 8b).
        - Paciente existente con historia clínica.
        - Veterinario con 5 unidades en kit.
        - POST /api/clinica/expediente-completo/ con unidades=2 online.
        - Retorna HTTP 201 Created con consulta_id y medicamentos_descontados.
        - DB: Consulta creada, consumo en APLICADO, kit queda en 3, 1 kardex CONSUMO_HISTORIA.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u1,
            }],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data.get("status"), "success")
        self.assertIn("consulta_id", resp.data)
        self.assertIn("medicamentos_descontados", resp.data)
        self.assertNotIn("alertas_farmacia", resp.data)

        consulta_id = resp.data["consulta_id"]
        self.assertTrue(Consulta.objects.filter(pk=consulta_id).exists())

        # Consumo registrado en estado APLICADO
        reg = assert_consumo_registro(self, u1, CONSUMO_ESTADO_APLICADO, origen_tipo=CONSUMO_ORIGEN_HISTORIA)
        self.assertEqual(reg.id_consulta_id, consulta_id)
        self.assertEqual(reg.cantidad_descontada, 2)
        self.assertEqual(reg.cantidad_solicitada, 2)

        # Saldo en kit actualizado a 3 unidades
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)

        # Kardex: exactamente 1 fila de CONSUMO_HISTORIA en KIT_VETERINARIO
        assert_kardex_consistente(self, u1, TIPO_KARDEX_CONSUMO_HISTORIA, cantidad_esperada=2)
        k_row = InventarioKardex.objects.filter(client_uuid=u1).first()
        self.assertEqual(k_row.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k_row.saldo_resultante, 3)

    def test_cli_02_acta_visita_online_descuento_exitoso(self):
        """
        TEST-CLI-02: Crear acta de visita con descuento del kit online.
        - Petición existente asignada a veterinario.
        - Kit con 10 unidades.
        - POST /api/peticiones/seguimiento/crear/ con unidades=4 online.
        - Retorna HTTP 201 Created con id_seguimiento.
        - DB: Acta creada, consumo en APLICADO, kit queda en 6, 1 kardex CONSUMO_ACTA.
        """
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)

        u1 = str(uuid.uuid4())
        payload = payload_acta_visita(
            id_peticion=peticion.id_peticion,
            id_veterinario=self.vet.id_usuario,
            consumos=[{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 4,
                "client_uuid": u1,
            }],
            offline=False,
        )

        resp = self.client.post("/api/peticiones/seguimiento/crear/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("id_seguimiento", resp.data)
        self.assertNotIn("alertas_farmacia", resp.data)

        id_seguimiento = resp.data["id_seguimiento"]
        self.assertTrue(SeguimientoPeticionesVisita.objects.filter(pk=id_seguimiento).exists())

        # Consumo registrado en estado APLICADO vinculado al acta
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg.id_seguimiento_visita_id, id_seguimiento)
        self.assertEqual(reg.cantidad_descontada, 4)

        # Saldo en kit actualizado a 6 unidades
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 6)

        # Kardex: exactamente 1 fila de CONSUMO_ACTA en KIT_VETERINARIO
        assert_kardex_consistente(self, u1, TIPO_KARDEX_CONSUMO_ACTA, cantidad_esperada=4)
        k_row = InventarioKardex.objects.filter(client_uuid=u1).first()
        self.assertEqual(k_row.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k_row.saldo_resultante, 6)

    def test_cli_03_sin_medicamentos_usados_consulta_y_acta(self):
        """
        TEST-CLI-03: Consulta y Acta sin medicamentos_usados (ausente, null y []) y offline="invalido".
        - Verifica que respondan HTTP 201 Created sin alertas_farmacia.
        - Cero registros en inventario_consumos_registro, cero kardex, kit inalterado.
        - Implementado con subTest.
        """
        animal, historia = crear_animal_con_historia()
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        # Casos para Consulta Clínica
        casos_consulta = [
            ("ausente", {}),
            ("null", {"medicamentos_usados": None}),
            ("lista_vacia", {"medicamentos_usados": []}),
            ("offline_invalido", {"medicamentos_usados": [], "sincronizacion_offline": "invalido"}),
        ]
        for etiqueta, extras in casos_consulta:
            with self.subTest(endpoint="consulta", caso=etiqueta):
                payload = payload_expediente_clinico(id_animal=animal.id_animal)
                if "medicamentos_usados" not in extras:
                    payload.pop("medicamentos_usados", None)
                else:
                    payload["medicamentos_usados"] = extras["medicamentos_usados"]
                if "sincronizacion_offline" in extras:
                    payload["sincronizacion_offline"] = extras["sincronizacion_offline"]

                resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
                self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
                self.assertEqual(resp.data.get("status"), "success")
                self.assertIn("consulta_id", resp.data)
                self.assertIn("historia_id", resp.data)
                self.assertIn("id_animal", resp.data)
                self.assertIn("numero_historia", resp.data)
                self.assertIn("medicamentos_descontados", resp.data)
                self.assertNotIn("alertas_farmacia", resp.data)
                claves_esperadas_consulta = {
                    "status",
                    "id_animal",
                    "historia_id",
                    "consulta_id",
                    "numero_historia",
                    "medicamentos_descontados",
                }
                self.assertEqual(set(resp.data.keys()), claves_esperadas_consulta)

        # Casos para Acta de Visita
        casos_acta = [
            ("ausente", {}),
            ("null", {"medicamentos_usados": None}),
            ("lista_vacia", {"medicamentos_usados": []}),
            ("offline_invalido", {"medicamentos_usados": [], "sincronizacion_offline": "invalido"}),
        ]
        for etiqueta, extras in casos_acta:
            with self.subTest(endpoint="acta", caso=etiqueta):
                payload = payload_acta_visita(id_peticion=peticion.id_peticion, id_veterinario=self.vet.id_usuario)
                if "medicamentos_usados" not in extras:
                    payload.pop("medicamentos_usados", None)
                else:
                    payload["medicamentos_usados"] = extras["medicamentos_usados"]
                if "sincronizacion_offline" in extras:
                    payload["sincronizacion_offline"] = extras["sincronizacion_offline"]

                resp = self.client.post("/api/peticiones/seguimiento/crear/", payload, format="json", **self.headers_vet)
                self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
                self.assertIn("id_seguimiento", resp.data)
                self.assertIn("mensaje", resp.data)
                self.assertNotIn("alertas_farmacia", resp.data)
                claves_esperadas_acta = {
                    "mensaje",
                    "id_seguimiento",
                    "foto",
                    "fotos",
                    "total_fotos",
                    "videos",
                    "total_videos",
                    "multimedia",
                }
                self.assertEqual(set(resp.data.keys()), claves_esperadas_acta)

        # Invariantes globales de BD: cero consumos, cero kardex, stock kit intacto
        self.assertEqual(InventarioConsumosRegistro.objects.count(), 0)
        self.assertEqual(InventarioKardex.objects.count(), 0)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 5)

    def test_cli_04_descuento_multi_lote_fifo_servicio(self):
        """
        TEST-CLI-04: Descuento multi-lote FIFO a nivel servicio (descontar_consumo_kit).
        - 2 lotes del mismo fármaco: L1 (antiguo, 2 uds) y L2 (reciente, 5 uds).
        - Consumo solicitado: 4 unidades.
        - L1 queda en 0, L2 queda en 3.
        - Exactamente 2 filas de kardex con CONSUMO_HISTORIA compartiendo client_uuid.
        """
        consulta = crear_consulta()

        b1 = crear_lote_bodega(
            self.med_a,
            cantidad=10,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra=self.hoy - timedelta(days=40),
        )
        kl1 = crear_lote_kit(
            self.vet,
            b1,
            cantidad=2,
            cantidad_ref=2,
            fecha_compra_origen=self.hoy - timedelta(days=40),
        )

        b2 = crear_lote_bodega(
            self.med_a,
            cantidad=10,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra=self.hoy - timedelta(days=10),
        )
        kl2 = crear_lote_kit(
            self.vet,
            b2,
            cantidad=5,
            cantidad_ref=5,
            fecha_compra_origen=self.hoy - timedelta(days=10),
        )

        u1 = uuid.uuid4()
        resultado = consumo_service.descontar_consumo_kit(
            id_veterinario=self.vet.id_usuario,
            consumos=[{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 4,
                "client_uuid": u1,
            }],
            origen_tipo=CONSUMO_ORIGEN_HISTORIA,
            origen_ids={"id_consulta": consulta.id_consulta},
            es_offline=False,
        )

        self.assertEqual(len(resultado["aplicados"]), 1)
        self.assertEqual(len(resultado["aplicados"][0]["lotes_afectados"]), 2)

        # Saldos en kit reflejan FIFO
        kl1.refresh_from_db()
        self.assertEqual(kl1.cantidad_actual, 0)
        kl2.refresh_from_db()
        self.assertEqual(kl2.cantidad_actual, 3)

        # Kardex: 2 filas con el mismo client_uuid
        k_rows = InventarioKardex.objects.filter(client_uuid=u1).order_by("id_kardex")
        self.assertEqual(k_rows.count(), 2)
        self.assertEqual(k_rows[0].id_inventario_id, b1.id_inventario)
        self.assertEqual(k_rows[0].cantidad, 2)
        self.assertEqual(k_rows[0].saldo_resultante, 0)
        self.assertEqual(k_rows[1].id_inventario_id, b2.id_inventario)
        self.assertEqual(k_rows[1].cantidad, 2)
        self.assertEqual(k_rows[1].saldo_resultante, 3)

        # Registro de consumo APLICADO por 4 unidades
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg.cantidad_descontada, 4)
        self.assertEqual(reg.id_consulta_id, consulta.id_consulta)

    def test_cli_04b_mismo_medicamento_repetido_en_payload(self):
        """
        TEST-CLI-04B: Mismo medicamento repetido en el payload (descuento acumulativo en memoria).
        - Kit con 10 unidades de Med A.
        - Payload con [{Med A, 3 uds, U1}, {Med A, 4 uds, U2}].
        - Stock kit disminuye en 7 unidades (saldo final = 3).
        - 2 filas en InventarioConsumosRegistro (ambas APLICADO).
        - 2 filas en InventarioKardex (CONSUMO_HISTORIA).
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)

        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[
                {"id_medicamento": self.med_a.id_medicamento, "unidades": 3, "client_uuid": u1},
                {"id_medicamento": self.med_a.id_medicamento, "unidades": 4, "client_uuid": u2},
            ],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        consulta_id = resp.data["consulta_id"]

        # Saldo en kit disminuye acumulativamente a 3 unidades
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)

        # 2 registros de consumo creados
        reg1 = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg1)
        self.assertEqual(reg1.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg1.cantidad_descontada, 3)
        self.assertEqual(reg1.id_consulta_id, consulta_id)

        reg2 = InventarioConsumosRegistro.objects.filter(client_uuid=u2).first()
        self.assertIsNotNone(reg2)
        self.assertEqual(reg2.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg2.cantidad_descontada, 4)
        self.assertEqual(reg2.id_consulta_id, consulta_id)

        # 2 filas en kardex
        k1 = InventarioKardex.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(k1)
        self.assertEqual(k1.cantidad, 3)
        self.assertEqual(k1.tipo_movimiento, TIPO_KARDEX_CONSUMO_HISTORIA)

        k2 = InventarioKardex.objects.filter(client_uuid=u2).first()
        self.assertIsNotNone(k2)
        self.assertEqual(k2.cantidad, 4)
        self.assertEqual(k2.tipo_movimiento, TIPO_KARDEX_CONSUMO_HISTORIA)

    def test_cli_05_reintento_idempotente_online_mismo_client_uuid(self):
        """
        TEST-CLI-05: Reintento idempotente online con el mismo client_uuid (Regresión 8b).
        - Envío inicial exitoso: Consulta C1 creada, 2 unidades descontadas (saldo 3).
        - Segundo envío idéntico con el mismo client_uuid:
          * Retorna HTTP 201 Created con el mismo consulta_id.
          * medicamentos_descontados=[] (evita doble reporte).
          * Exactamente 1 Consulta, 1 consumo registrado, sin doble descuento de stock.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u1}],
            offline=False,
        )

        resp1 = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        c1_id = resp1.data["consulta_id"]

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)

        # Reintento idéntico con mismo client_uuid
        resp2 = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data["consulta_id"], c1_id)
        self.assertEqual(resp2.data["medicamentos_descontados"], [])

        # Invariantes en base de datos: sin duplicación
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 1)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 1)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 1)

    def test_cli_06_rollback_total_clinico_animal_nuevo_falta_stock(self):
        """
        TEST-CLI-06: Rollback total clínico ante falta de stock online creando un Animal nuevo.
        - Veterinario con stock insuficiente (1 unidad en kit).
        - Payload con bloque animal nuevo solicitando 3 unidades.
        - Retorna HTTP 400 Bad Request con error='STOCK_INSUFICIENTE_KIT'.
        - Rollback total: NO se crea Animal, NO se crea HistoriaClinica, NO se crea Consulta.
        - Stock de kit intacto (1 ud), cero kardex, cero consumos.
        """
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=1, cantidad_ref=1)

        nombre_animal = f"AnimalNuevo_{uuid.uuid4().hex[:6]}"
        animal_nuevo = {
            "nombre": nombre_animal,
            "sexo": "Macho",
            "color": "Negro",
            "peso": 10.0,
            "especie": "Canino",
            "raza": "Mestizo Canino",
        }
        u_fail = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            animal_nuevo=animal_nuevo,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 3, "client_uuid": u_fail}],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "STOCK_INSUFICIENTE_KIT")

        # Rollback atómico completo
        self.assertFalse(Animal.objects.filter(nombre=nombre_animal).exists())
        self.assertFalse(HistoriaClinica.objects.filter(id_animal__nombre=nombre_animal).exists())
        self.assertFalse(Consulta.objects.filter(id_historia__id_animal__nombre=nombre_animal).exists())

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 1)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_fail).count(), 0)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_fail).count(), 0)

    def test_cli_07_rollback_total_acta_visita_falta_stock(self):
        """
        TEST-CLI-07: Rollback total en acta de visita ante falta de stock online.
        - Veterinario sin stock suficiente (0 unidades en kit).
        - Payload con funcionarios solicitando 2 unidades.
        - Retorna HTTP 400 Bad Request con error='STOCK_INSUFICIENTE_KIT'.
        - Rollback total: NO se crea SeguimientoPeticionesVisita, sin funcionarios ni evidencias.
        """
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=0, cantidad_ref=5)

        u_acta = str(uuid.uuid4())
        payload = payload_acta_visita(
            id_peticion=peticion.id_peticion,
            id_veterinario=self.vet.id_usuario,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u_acta}],
            offline=False,
        )
        payload["funcionarios"] = [{"nombre": "Funcionario Auxiliar CLI07", "cargo": "Inspector", "es_externo": True}]

        resp = self.client.post("/api/peticiones/seguimiento/crear/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "STOCK_INSUFICIENTE_KIT")

        # Rollback atómico completo
        self.assertFalse(SeguimientoPeticionesVisita.objects.filter(id_peticion=peticion.id_peticion).exists())
        self.assertFalse(SeguimientoVisitaFuncionarios.objects.filter(nombre="Funcionario Auxiliar CLI07").exists())
        self.assertFalse(EvidenciaSeguimientoVisita.objects.filter(id_seguimiento__id_peticion=peticion.id_peticion).exists())

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_acta).count(), 0)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_acta).count(), 0)

    def test_cli_08_offline_clinico_conflicto_parcial_stock(self):
        """
        TEST-CLI-08: Offline clínico con conflicto parcial de stock (stock 1, pide 4, offline=True).
        - Retorna HTTP 201 Created con alertas_farmacia.conflictos_stock conteniendo las 6 claves canónicas.
        - Consulta persistida.
        - Consumo registrado en RECHAZADO_CONFLICTO con id_consulta poblado y stock_disponible_al_rechazo=1.
        - Cero filas de kardex generadas.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=1, cantidad_ref=5)

        u_off = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 4, "client_uuid": u_off}],
            offline=True,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data.get("status"), "success")
        consulta_id = resp.data.get("consulta_id")
        self.assertIsNotNone(consulta_id)

        # Alertas de farmacia con las 6 claves canónicas
        self.assertIn("alertas_farmacia", resp.data)
        conflictos = resp.data["alertas_farmacia"].get("conflictos_stock", [])
        self.assertEqual(len(conflictos), 1)
        conf = conflictos[0]
        claves_esperadas = {"client_uuid", "id_medicamento", "nombre_medicamento", "solicitado", "disponible", "motivo_rechazo"}
        self.assertEqual(set(conf.keys()), claves_esperadas)
        self.assertEqual(conf["client_uuid"], u_off)
        self.assertEqual(conf["id_medicamento"], self.med_a.id_medicamento)
        self.assertEqual(conf["nombre_medicamento"], self.med_a.nombre)
        self.assertEqual(conf["solicitado"], 4)
        self.assertEqual(conf["disponible"], 1)
        self.assertIsInstance(conf["motivo_rechazo"], str)
        self.assertTrue(conf["motivo_rechazo"].strip())

        # Persistencia en BD
        self.assertTrue(Consulta.objects.filter(pk=consulta_id).exists())
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u_off).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg.id_consulta_id, consulta_id)
        self.assertEqual(reg.stock_disponible_al_rechazo, 1)
        self.assertEqual(reg.cantidad_descontada, 0)
        self.assertEqual(reg.cantidad_solicitada, 4)

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 1)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_off).count(), 0)

    def test_cli_08b_offline_clinico_mixto_aplicado_y_conflicto(self):
        """
        TEST-CLI-08B: Petición clínica offline con consumos mixtos (aplicado y rechazado por conflicto).
        - Med A con stock 5 pide 2 con U1.
        - Med B sin stock (0 uds) pide 3 con U2.
        - Retorna HTTP 201 Created con alertas_farmacia.conflictos_stock para Med B (6 claves).
        - DB: Consulta creada; U1 en APLICADO y U2 en RECHAZADO_CONFLICTO (ambos con id_consulta).
        - Kardex generado solo para Med A (1 fila CONSUMO_HISTORIA, saldo kit=3). Cero kardex para Med B.
        """
        animal, historia = crear_animal_con_historia()
        b_a = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_a = crear_lote_kit(self.vet, b_a, cantidad=5, cantidad_ref=5)

        b_b = crear_lote_bodega(self.med_b, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_b = crear_lote_kit(self.vet, b_b, cantidad=0, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[
                {"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u1},
                {"id_medicamento": self.med_b.id_medicamento, "unidades": 3, "client_uuid": u2},
            ],
            offline=True,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data.get("status"), "success")
        consulta_id = resp.data.get("consulta_id")
        self.assertIsNotNone(consulta_id)

        # Alertas de farmacia contiene conflicto para Med B
        self.assertIn("alertas_farmacia", resp.data)
        conflictos = resp.data["alertas_farmacia"].get("conflictos_stock", [])
        self.assertEqual(len(conflictos), 1)
        conf = conflictos[0]
        claves_esperadas = {"client_uuid", "id_medicamento", "nombre_medicamento", "solicitado", "disponible", "motivo_rechazo"}
        self.assertEqual(set(conf.keys()), claves_esperadas)
        self.assertEqual(conf["client_uuid"], u2)
        self.assertEqual(conf["id_medicamento"], self.med_b.id_medicamento)
        self.assertEqual(conf["solicitado"], 3)
        self.assertEqual(conf["disponible"], 0)

        # Persistencia en BD
        self.assertTrue(Consulta.objects.filter(pk=consulta_id).exists())

        reg1 = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg1)
        self.assertEqual(reg1.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg1.id_consulta_id, consulta_id)
        self.assertEqual(reg1.cantidad_descontada, 2)

        reg2 = InventarioConsumosRegistro.objects.filter(client_uuid=u2).first()
        self.assertIsNotNone(reg2)
        self.assertEqual(reg2.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg2.id_consulta_id, consulta_id)
        self.assertEqual(reg2.stock_disponible_al_rechazo, 0)
        self.assertEqual(reg2.cantidad_descontada, 0)

        # Kardex solo para Med A
        kl_a.refresh_from_db()
        self.assertEqual(kl_a.cantidad_actual, 3)
        kl_b.refresh_from_db()
        self.assertEqual(kl_b.cantidad_actual, 0)

        k_a = InventarioKardex.objects.filter(client_uuid=u1)
        self.assertEqual(k_a.count(), 1)
        self.assertEqual(k_a.first().tipo_movimiento, TIPO_KARDEX_CONSUMO_HISTORIA)
        self.assertEqual(k_a.first().saldo_resultante, 3)

        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u2).count(), 0)

    def test_cli_09_offline_acta_visita_conflicto_parcial(self):
        """
        TEST-CLI-09: Offline en acta de visita con conflicto parcial (stock 0, pide 2, offline=True).
        - Retorna HTTP 201 Created con alertas_farmacia conteniendo las 6 claves canónicas.
        - SeguimientoPeticionesVisita persistido.
        - Consumo registrado en RECHAZADO_CONFLICTO con id_seguimiento_visita poblado.
        - Cero filas de kardex generadas.
        """
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=0, cantidad_ref=5)

        u_acta_off = str(uuid.uuid4())
        payload = payload_acta_visita(
            id_peticion=peticion.id_peticion,
            id_veterinario=self.vet.id_usuario,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u_acta_off}],
            offline=True,
        )

        resp = self.client.post("/api/peticiones/seguimiento/crear/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        id_seguimiento = resp.data.get("id_seguimiento")
        self.assertIsNotNone(id_seguimiento)

        # Alertas de farmacia con 6 claves
        self.assertIn("alertas_farmacia", resp.data)
        conflictos = resp.data["alertas_farmacia"].get("conflictos_stock", [])
        self.assertEqual(len(conflictos), 1)
        conf = conflictos[0]
        claves_esperadas = {"client_uuid", "id_medicamento", "nombre_medicamento", "solicitado", "disponible", "motivo_rechazo"}
        self.assertEqual(set(conf.keys()), claves_esperadas)
        self.assertEqual(conf["client_uuid"], u_acta_off)
        self.assertEqual(conf["id_medicamento"], self.med_a.id_medicamento)
        self.assertEqual(conf["solicitado"], 2)
        self.assertEqual(conf["disponible"], 0)

        # Persistencia en BD
        self.assertTrue(SeguimientoPeticionesVisita.objects.filter(pk=id_seguimiento).exists())

        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u_acta_off).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg.id_seguimiento_visita_id, id_seguimiento)
        self.assertEqual(reg.stock_disponible_al_rechazo, 0)
        self.assertEqual(reg.cantidad_descontada, 0)
        self.assertEqual(reg.cantidad_solicitada, 2)

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_acta_off).count(), 0)

    def test_cli_10_reintento_offline_tras_rechazado_conflicto(self):
        """
        TEST-CLI-10: Reintento offline tras consumo en RECHAZADO_CONFLICTO.
        - Precondición: Primer POST offline real con stock 1 y unidades=4.
          Queda consumo RECHAZADO_CONFLICTO con id_consulta C1,
          stock_disponible_al_rechazo=1, cantidad_descontada=0, codigo_http=409.
        - Reenvío offline idéntico (mismo client_uuid, sincronizacion_offline=True).
        - Respuesta: HTTP 201 Created, status='success', consulta_id == C1,
          medicamentos_descontados == [], alertas_farmacia.conflictos_stock con 6 claves.
        - BD: 1 Consulta, 1 consumo en RECHAZADO_CONFLICTO vinculado a C1, kit sin descuento, 0 kardex.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=1, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 4, "client_uuid": u1}],
            offline=True,
        )

        resp1 = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp1.data.get("status"), "success")
        c1_id = resp1.data.get("consulta_id")
        self.assertIsNotNone(c1_id)

        # Verificar precondición en BD
        reg1 = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg1)
        self.assertEqual(reg1.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg1.id_consulta_id, c1_id)
        self.assertEqual(reg1.stock_disponible_al_rechazo, 1)
        self.assertEqual(reg1.cantidad_descontada, 0)
        self.assertEqual(reg1.codigo_http, 409)

        # Reenvío idéntico offline
        resp2 = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data.get("status"), "success")
        self.assertEqual(resp2.data.get("consulta_id"), c1_id)
        self.assertEqual(resp2.data.get("medicamentos_descontados"), [])

        self.assertIn("alertas_farmacia", resp2.data)
        conflictos = resp2.data["alertas_farmacia"].get("conflictos_stock", [])
        self.assertEqual(len(conflictos), 1)
        conf = conflictos[0]
        claves_esperadas = {"client_uuid", "id_medicamento", "nombre_medicamento", "solicitado", "disponible", "motivo_rechazo"}
        self.assertEqual(set(conf.keys()), claves_esperadas)
        self.assertEqual(conf["client_uuid"], u1)
        self.assertEqual(conf["id_medicamento"], self.med_a.id_medicamento)
        self.assertEqual(conf["nombre_medicamento"], self.med_a.nombre)
        self.assertEqual(conf["solicitado"], 4)
        self.assertEqual(conf["disponible"], 1)
        self.assertIsInstance(conf["motivo_rechazo"], str)
        self.assertTrue(conf["motivo_rechazo"].strip())

        # Invariantes en base de datos: sin duplicación
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 1)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 1)
        reg_final = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertEqual(reg_final.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg_final.id_consulta_id, c1_id)

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 1)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 0)

    def test_cli_11_conflicto_idempotencia_reuso_client_uuid_discrepante(self):
        """
        TEST-CLI-11: Conflicto de idempotencia por reuso de client_uuid discrepante.
        - Primer POST exitoso: Med A, 2 uds con U1 (queda APLICADO, kit en 3).
        - Segundo POST con mismo client_uuid pero parámetros distintos:
          * SubTest 1: Mismo client_uuid pero Med B.
          * SubTest 2: Mismo client_uuid pero Med A con 4 uds.
        - Retorna HTTP 400 Bad Request, error='CONFLICTO_IDEMPOTENCIA_CONSUMO'.
        - BD inalterada tras los intentos rechazados.
        """
        animal, historia = crear_animal_con_historia()
        b_a = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_a = crear_lote_kit(self.vet, b_a, cantidad=5, cantidad_ref=5)

        b_b = crear_lote_bodega(self.med_b, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_b = crear_lote_kit(self.vet, b_b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload_inicial = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u1}],
            offline=False,
        )

        resp1 = self.client.post("/api/clinica/expediente-completo/", payload_inicial, format="json", **self.headers_vet)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        c1_id = resp1.data["consulta_id"]

        kl_a.refresh_from_db()
        self.assertEqual(kl_a.cantidad_actual, 3)

        casos_discrepantes = [
            ("otro_medicamento", {"id_medicamento": self.med_b.id_medicamento, "unidades": 2, "client_uuid": u1}),
            ("distintas_unidades", {"id_medicamento": self.med_a.id_medicamento, "unidades": 4, "client_uuid": u1}),
        ]

        for etiqueta, consumo_disc in casos_discrepantes:
            with self.subTest(caso=etiqueta):
                payload_disc = payload_expediente_clinico(
                    id_animal=animal.id_animal,
                    consumos=[consumo_disc],
                    offline=False,
                )
                resp_err = self.client.post("/api/clinica/expediente-completo/", payload_disc, format="json", **self.headers_vet)
                self.assertEqual(resp_err.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(resp_err.data.get("error"), "CONFLICTO_IDEMPOTENCIA_CONSUMO")

        # Invariantes globales en BD: ningún cambio tras los rechazos
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 1)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 1)
        kl_a.refresh_from_db()
        self.assertEqual(kl_a.cantidad_actual, 3)
        kl_b.refresh_from_db()
        self.assertEqual(kl_b.cantidad_actual, 5)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 1)

    def test_cli_11b_doble_client_uuid_mismo_payload(self):
        """
        TEST-CLI-11B: Doble client_uuid dentro del mismo payload de consumo.
        - Veterinario con stock suficiente (10 unidades).
        - Payload envía 2 renglones con idéntico client_uuid.
        - Retorna HTTP 400 Bad Request, error='CONSUMO_INVALIDO'.
        - Base de datos inalterada: sin consulta creada, stock kit intacto, cero consumos, cero kardex.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)

        u_dup = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[
                {"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u_dup},
                {"id_medicamento": self.med_a.id_medicamento, "unidades": 3, "client_uuid": u_dup},
            ],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "CONSUMO_INVALIDO")
        self.assertIn("duplicado dentro del mismo payload", str(resp.data.get("detalle", "")))

        # Invariantes en BD
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 0)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_dup).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_dup).count(), 0)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 10)

    def test_cli_12_anti_doble_descuento_mismo_medicamento_bodega_y_kit(self):
        """
        TEST-CLI-12: Anti doble descuento para el mismo medicamento en bodega y kit.
        - Tratamiento con _debe_descontar=True y stock en bodega general.
        - Mismo medicamento presente en medicamentos_usados del kit.
        - Retorna HTTP 400 Bad Request con error='CONSUMO_INVALIDO'.
        - BD inalterada: sin consulta, sin tratamientos, stock bodega y kit intactos, cero consumos, cero kardex.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            hoja2={
                "diagnosticoFinal": "Evaluación con medicación simultánea",
                "tratamientos": [{
                    "productoBase": "Amoxicilina Suspensión",
                    "dosisBasica": "10 mg/kg",
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad_usada": 2,
                    "descontar_inventario": True,
                }],
            },
            consumos=[{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u1,
            }],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "CONSUMO_INVALIDO")
        self.assertIn("doble descuento no permitido", str(resp.data.get("detalle", "")).lower())

        # Invariantes en base de datos
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 0)
        b.refresh_from_db()
        self.assertEqual(b.cantidad_actual, 50)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 5)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 0)

    def test_cli_13a_patch_clinico_online_con_consumo_kit(self):
        """
        TEST-CLI-13A: PATCH clínico online con consumo de kit (Regresión 8b).
        - Consulta C1 preexistente en la historia clínica.
        - Veterinario con stock suficiente en kit (5 unidades).
        - PATCH /api/clinica/expediente-completo/ con campos obligatorios y medicamentos_usados.
        - Retorna HTTP 200 OK con status='success' y consulta_id == C1.
        - BD: consumo registrado en APLICADO con id_consulta C1, kit descontado, kardex CONSUMO_HISTORIA.
        """
        animal, historia = crear_animal_con_historia()
        c1 = crear_consulta(historia=historia, motivo_consulta="Consulta inicial preexistente")
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u_patch = str(uuid.uuid4())
        payload = {
            "id_animal": animal.id_animal,
            "diagnosticoPrincipal": "Control post-tratamiento favorable",
            "evolucion": "El paciente evoluciona favorablemente a la terapia",
            "motivoModificacion": "Ajuste de dosis y registro de consumo de kit",
            "medicamentos_usados": [{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u_patch,
            }],
            "sincronizacion_offline": False,
        }

        resp = self.client.patch("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get("status"), "success")
        self.assertEqual(resp.data.get("consulta_id"), c1.id_consulta)
        self.assertEqual(resp.data.get("historia_id"), historia.id_historia)
        self.assertNotIn("alertas_farmacia", resp.data)

        # Consumo registrado en APLICADO
        reg = assert_consumo_registro(self, u_patch, CONSUMO_ESTADO_APLICADO, origen_tipo=CONSUMO_ORIGEN_HISTORIA)
        self.assertEqual(reg.id_consulta_id, c1.id_consulta)
        self.assertEqual(reg.cantidad_descontada, 2)

        # Stock en kit descontado
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)

        # Kardex generado
        assert_kardex_consistente(self, u_patch, TIPO_KARDEX_CONSUMO_HISTORIA, cantidad_esperada=2)
        k_row = InventarioKardex.objects.filter(client_uuid=u_patch).first()
        self.assertEqual(k_row.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k_row.saldo_resultante, 3)
        self.assertEqual(k_row.id_consulta_id, c1.id_consulta)

    def test_cli_13b_reintento_patch_tras_consulta_mas_nueva(self):
        """
        TEST-CLI-13B: Reintento de PATCH tras registrarse una consulta más nueva en la historia.
        - Consulta C1 actualizada vía PATCH asociando client_uuid.
        - Se crea Consulta C2 más nueva en la misma historia clínica.
        - Reintento del mismo PATCH con el mismo client_uuid detecta que la consulta más reciente es C2.
        - Retorna HTTP 400 Bad Request, error='CONFLICTO_IDEMPOTENCIA_CONSUMO'.
        - C2 intacta, sin duplicar consumo ni realizar doble descuento de stock.
        """
        animal, historia = crear_animal_con_historia()
        c1 = crear_consulta(historia=historia, motivo_consulta="Consulta inicial C1")
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u_patch = str(uuid.uuid4())
        payload = {
            "id_animal": animal.id_animal,
            "diagnosticoPrincipal": "Control inicial",
            "evolucion": "Evolución inicial estable",
            "motivoModificacion": "Actualización inicial con medicación de kit",
            "medicamentos_usados": [{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u_patch,
            }],
            "sincronizacion_offline": False,
        }

        resp1 = self.client.patch("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.data.get("consulta_id"), c1.id_consulta)

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)

        # Asegurar que C1 sea anterior y crear consulta C2 más nueva en la historia
        Consulta.objects.filter(pk=c1.pk).update(fecha_hora=timezone.now() - timedelta(minutes=15))
        c2 = crear_consulta(historia=historia, motivo_consulta="Nueva consulta C2 posterior")

        # Reintento idéntico del PATCH
        resp2 = self.client.patch("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.data.get("error"), "CONFLICTO_IDEMPOTENCIA_CONSUMO")

        # Invariantes en BD: C2 intacta, consumo único en C1, saldo kit sin cambios
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 2)
        c2.refresh_from_db()
        self.assertEqual(c2.motivo_consulta, "Nueva consulta C2 posterior")
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_patch).count(), 1)
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u_patch).first()
        self.assertEqual(reg.id_consulta_id, c1.id_consulta)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 3)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_patch).count(), 1)

    def test_cli_14_usuario_no_veterinario_con_consumos_kit(self):
        """
        TEST-CLI-14: Usuario no veterinario (Rol 4 Peticionario) con medicamentos_usados no vacío.
        - El endpoint /api/clinica/expediente-completo/ permite IsAuthenticated.
        - Al enviar medicamentos_usados, validar_payload_consumo_kit rechaza con UsuarioNoVeterinarioError.
        - Retorna HTTP 400 Bad Request con error='USUARIO_NO_VETERINARIO'.
        - BD inalterada: cero consultas creadas, cero consumos, cero kardex, kit intacto.
        """
        peticionario = crear_usuario(rol_id=4, email="peticionario_cli14@cba.gov.co")
        headers_pet = obtener_headers_jwt(peticionario)

        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u1}],
            offline=False,
        )

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **headers_pet)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "USUARIO_NO_VETERINARIO")

        # Invariantes en base de datos
        self.assertEqual(Consulta.objects.filter(id_historia=historia).count(), 0)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 0)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 5)

    def test_cli_15_suplantacion_id_veterinario_en_body(self):
        """
        TEST-CLI-15: Suplantación de id_veterinario en el body de consulta clínica.
        - Vet A (self.vet) autenticado envía en el body id_veterinario de Vet B.
        - Ambos veterinarios poseen stock distinguible en sus kits.
        - Retorna HTTP 201 Created.
        - El consumo se descuenta del kit de Vet A (request.user), NUNCA del de Vet B.
        - DB: registro de consumo y kardex vinculados a Vet A; kit de Vet B permanece intacto.
        """
        vet_b = crear_usuario(rol_id=3, email="vet_b_cli15@cba.gov.co")
        animal, historia = crear_animal_con_historia()

        b_a = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_a = crear_lote_kit(self.vet, b_a, cantidad=5, cantidad_ref=5)

        b_b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_b = crear_lote_kit(vet_b, b_b, cantidad=10, cantidad_ref=10)

        u_sup = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u_sup}],
            offline=False,
        )
        payload["id_veterinario"] = vet_b.id_usuario

        resp = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        consulta_id = resp.data["consulta_id"]

        # Kit de Vet A descontado (5 -> 3)
        kl_a.refresh_from_db()
        self.assertEqual(kl_a.cantidad_actual, 3)

        # Kit de Vet B completamente intacto (10)
        kl_b.refresh_from_db()
        self.assertEqual(kl_b.cantidad_actual, 10)

        # Registro de consumo asociado a Vet A
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u_sup).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.id_veterinario_id, self.vet.id_usuario)
        self.assertNotEqual(reg.id_veterinario_id, vet_b.id_usuario)
        self.assertEqual(reg.id_consulta_id, consulta_id)

        # Kardex asociado a Vet A y a su lote de kit
        k_row = InventarioKardex.objects.filter(client_uuid=u_sup).first()
        self.assertIsNotNone(k_row)
        self.assertEqual(k_row.id_veterinario_id, self.vet.id_usuario)
        self.assertNotEqual(k_row.id_veterinario_id, vet_b.id_usuario)
        self.assertEqual(k_row.id_inventario_id, b_a.id_inventario)

    def test_cli_16_delete_consulta_con_consumos_retorna_409_restrict(self):
        """
        TEST-CLI-16: DELETE de Consulta con dependencias de consumo clínico.
        - Consulta creada con consumo asociado en inventario_consumos_registro e inventario_kardex.
        - DELETE /api/clinica/consultas/{id}/ interceptado por RestrictDeleteProtectionMixin.
        - Retorna HTTP 409 Conflict con codigo='REGISTRO_CON_DEPENDENCIAS'.
        - La Consulta sigue existiendo en BD; no se genera HTTP 500.
        """
        animal, historia = crear_animal_con_historia()
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = str(uuid.uuid4())
        payload = payload_expediente_clinico(
            id_animal=animal.id_animal,
            consumos=[{"id_medicamento": self.med_a.id_medicamento, "unidades": 2, "client_uuid": u1}],
            offline=False,
        )

        resp_post = self.client.post("/api/clinica/expediente-completo/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp_post.status_code, status.HTTP_201_CREATED)
        consulta_id = resp_post.data["consulta_id"]
        self.assertTrue(Consulta.objects.filter(pk=consulta_id).exists())

        # Intentar eliminar la consulta con dependencias
        resp_del = self.client.delete(f"/api/clinica/consultas/{consulta_id}/", **self.headers_vet)
        self.assertEqual(resp_del.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp_del.data.get("codigo"), "REGISTRO_CON_DEPENDENCIAS")
        self.assertEqual(
            resp_del.data.get("error"),
            "No se puede eliminar este registro porque tiene otros registros asociados que dependen de él."
        )

        # Consulta sigue existiendo
        self.assertTrue(Consulta.objects.filter(pk=consulta_id).exists())

    def test_cli_17_consumo_directo_sin_origen_ids_lanza_consumo_invalido(self):
        """
        TEST-CLI-17: descontar_consumo_kit con origen_tipo='DIRECTO' y origen_ids=None.
        - Lanza ConsumoInvalidoError con codigo='CONSUMO_INVALIDO'.
        - Detalle menciona identificadores de origen.
        - Base de datos inalterada: saldo kit intacto, cero consumos, cero kardex.
        """
        b = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=5, cantidad_ref=5)

        u1 = uuid.uuid4()
        with self.assertRaises(ConsumoInvalidoError) as cm:
            consumo_service.descontar_consumo_kit(
                id_veterinario=self.vet.id_usuario,
                consumos=[{
                    "id_medicamento": self.med_a.id_medicamento,
                    "unidades": 2,
                    "client_uuid": u1,
                }],
                origen_tipo=CONSUMO_ORIGEN_DIRECTO,
                origen_ids=None,
                es_offline=False,
            )

        self.assertEqual(cm.exception.codigo, "CONSUMO_INVALIDO")
        self.assertIn("identificadores de origen", str(cm.exception).lower())
        self.assertIsInstance(str(cm.exception), str)

        # Base de datos inalterada
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 5)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 0)
