"""
Tests de integración para el flujo del Kit de Veterinario (Asignación, Retiro, Referencias, Consumo y Sincronización).
Fase C2 (Tarea 10): 17 tests (TEST-KIT-23, 24A, 24B, 33A, 33B, 33C, 33D, 33E, 34, 35, 36, 37, 38, 40, 41B, 41B-alias, 41B-sin-cantidad).

Especificación:
- plan/DISENO_TAREA10.md (§4.5, §5.3: Bloque C).
- plan/DECISIONES.md (§1, §4, §5, §7, §8, §11, §12).
"""

import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from inventario.constants import (
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_RETIRO_GESTOR,
    TIPO_KARDEX_BAJA_VENCIMIENTO,
    TIPO_KARDEX_CONSUMO_HISTORIA,
    UBICACION_KARDEX_GENERAL,
    UBICACION_KARDEX_KIT,
    CONSUMO_ESTADO_APLICADO,
)
from inventario.models import (
    Compra,
    Inventarios,
    KitVeterinarioLotes,
    InventarioKardex,
    InventarioConsumosRegistro,
)
from inventario.services import kit_service, consumo_service
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_lote_kit,
    crear_consulta,
)
from inventario.tests.helpers import obtener_headers_jwt


class KitFlujoTestCase(TestCase):
    """
    Suite de integración del ciclo operativo del kit veterinario.
    Ejecutada sobre transacciones aisladas con rollback (django.test.TestCase).
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.hoy = timezone.now().date()

        # Actores institucionales
        self.gestor = crear_usuario(rol_id=1, email="gestor_kit@cba.gov.co")
        self.vet_a = crear_usuario(rol_id=3, email="vet_a_kit@cba.gov.co")
        self.vet_b = crear_usuario(rol_id=3, email="vet_b_kit@cba.gov.co")

        # Headers JWT
        self.headers_gestor = obtener_headers_jwt(self.gestor)
        self.headers_vet_a = obtener_headers_jwt(self.vet_a)
        self.headers_vet_b = obtener_headers_jwt(self.vet_b)

        # Catálogo farmacéutico
        self.med_a = crear_medicamento(nombre="Amoxicilina Kit Flujo")
        self.med_b = crear_medicamento(nombre="Meloxicam Kit Flujo")

    def test_kit_23_asignar_stock_bodega_a_kit(self):
        """
        TEST-KIT-23: Asignar 20 unidades desde un lote de bodega de 100 unidades al kit del veterinario.
        Verifica respuesta HTTP 201 Created y estado íntegro en BD:
        - Fila en KitVeterinarioLotes con cantidad_actual=20 y cantidad_referencia=20.
        - Lote en bodega baja a 80 unidades (estado Disponible).
        - Kardex ASIGNACION_KIT con exactamente 2 filas:
          1. GENERAL (salida de 20 uds, saldo_anterior=100, saldo_resultante=80).
          2. KIT_VETERINARIO (entrada de 20 uds, saldo_anterior=0, saldo_resultante=20).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=100,
            fecha_vencimiento=self.hoy + timedelta(days=180)
        )

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Dotación inicial de insumos para jornada médica",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 20
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("mensaje"), "Stock asignado exitosamente")
        self.assertIn("asignaciones", response.data)
        self.assertEqual(len(response.data["asignaciones"]), 1)

        # Verificación en BD: Bodega General
        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 80)
        self.assertEqual(lote_bodega.estado, "Disponible")

        # Verificación en BD: Kit Veterinario
        kit_lotes = KitVeterinarioLotes.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a
        )
        self.assertEqual(kit_lotes.count(), 1)
        kl = kit_lotes.first()
        self.assertEqual(kl.cantidad_actual, 20)
        self.assertEqual(kl.cantidad_referencia, 20)
        self.assertEqual(kl.id_inventario_id, lote_bodega.id_inventario)

        # Verificación en BD: Kardex (exactamente 2 filas de ASIGNACION_KIT)
        kardex_rows = InventarioKardex.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a,
            tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT
        ).order_by("id_kardex")
        self.assertEqual(kardex_rows.count(), 2)

        # Fila 1: GENERAL (Descuento de bodega)
        k_gen = kardex_rows.filter(ubicacion=UBICACION_KARDEX_GENERAL).first()
        self.assertIsNotNone(k_gen)
        self.assertEqual(k_gen.cantidad, 20)
        self.assertEqual(k_gen.saldo_anterior, 100)
        self.assertEqual(k_gen.saldo_resultante, 80)
        self.assertEqual(k_gen.id_usuario_ejecuta_id, self.gestor.id_usuario)
        self.assertEqual(k_gen.id_inventario_id, lote_bodega.id_inventario)

        # Fila 2: KIT_VETERINARIO (Ingreso al kit)
        k_kit = kardex_rows.filter(ubicacion=UBICACION_KARDEX_KIT).first()
        self.assertIsNotNone(k_kit)
        self.assertEqual(k_kit.cantidad, 20)
        self.assertEqual(k_kit.saldo_anterior, 0)
        self.assertEqual(k_kit.saldo_resultante, 20)
        self.assertEqual(k_kit.id_usuario_ejecuta_id, self.gestor.id_usuario)
        self.assertEqual(k_kit.id_inventario_id, lote_bodega.id_inventario)

    def test_kit_24a_retiro_directo_servicio(self):
        """
        TEST-KIT-24A: Retiro directo de stock por el Gestor llamando directamente a kit_service.retirar_stock_de_kit.
        Configuración: Vet con L1 (vencido, 2 uds), L2 (vencido, 3 uds), L3 (vigente, 10 uds).
        Retiro de 7 unidades:
        - 5 unidades vencidas se dan de baja (2 de L1, 3 de L2).
        - 2 unidades vigentes se reintegran a bodega (de L3).
        
        Kardex esperado: exactamente 4 filas
        - 2 filas BAJA_VENCIMIENTO en KIT_VETERINARIO (L1: 2 uds, L2: 3 uds; no reingresan a bodega).
        - 2 filas RETIRO_GESTOR (L3: 2 uds, una en KIT_VETERINARIO y otra en GENERAL).
        """
        # Crear lotes de bodega base
        b1 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=10), estado="Agotado")
        b2 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=5), estado="Agotado")
        b3 = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Disponible")

        # Asignar lotes al kit del veterinario
        l1 = crear_lote_kit(self.vet_a, b1, cantidad=2, cantidad_ref=2, fecha_vencimiento=b1.fecha_vencimiento)
        l2 = crear_lote_kit(self.vet_a, b2, cantidad=3, cantidad_ref=3, fecha_vencimiento=b2.fecha_vencimiento)
        l3 = crear_lote_kit(self.vet_a, b3, cantidad=10, cantidad_ref=10, fecha_vencimiento=b3.fecha_vencimiento)

        resultado = kit_service.retirar_stock_de_kit(
            id_gestor=self.gestor.id_usuario,
            id_veterinario=self.vet_a.id_usuario,
            id_medicamento=self.med_a.id_medicamento,
            cantidad=7,
            motivo="Retiro directo de unidades vencidas y ajuste de dotación",
        )

        self.assertEqual(resultado.get("cantidad_total_retirada"), 7)
        self.assertEqual(resultado.get("unidades_baja_vencimiento"), 5)
        self.assertEqual(resultado.get("unidades_reintegradas_bodega"), 2)

        # Verificación de BD: Kit Veterinario
        l1.refresh_from_db()
        l2.refresh_from_db()
        l3.refresh_from_db()
        self.assertEqual(l1.cantidad_actual, 0)
        self.assertEqual(l2.cantidad_actual, 0)
        self.assertEqual(l3.cantidad_actual, 8)

        # Verificación de BD: Bodega General (solo L3 se reintegra)
        b1.refresh_from_db()
        b2.refresh_from_db()
        b3.refresh_from_db()
        self.assertEqual(b1.cantidad_actual, 0)
        self.assertEqual(b2.cantidad_actual, 0)
        self.assertEqual(b3.cantidad_actual, 22)
        self.assertEqual(b3.estado, "Disponible")

        # Verificación de BD: Kardex (exactamente 4 filas)
        kardex_rows = InventarioKardex.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a
        ).order_by("id_kardex")
        self.assertEqual(kardex_rows.count(), 4)

        # 2 filas BAJA_VENCIMIENTO en KIT_VETERINARIO
        bajas = kardex_rows.filter(tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO)
        self.assertEqual(bajas.count(), 2)
        baja_l1 = bajas.filter(id_inventario=b1).first()
        self.assertIsNotNone(baja_l1)
        self.assertEqual(baja_l1.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(baja_l1.cantidad, 2)
        self.assertEqual(baja_l1.saldo_anterior, 2)
        self.assertEqual(baja_l1.saldo_resultante, 0)

        baja_l2 = bajas.filter(id_inventario=b2).first()
        self.assertIsNotNone(baja_l2)
        self.assertEqual(baja_l2.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(baja_l2.cantidad, 3)
        self.assertEqual(baja_l2.saldo_anterior, 3)
        self.assertEqual(baja_l2.saldo_resultante, 0)

        # 2 filas RETIRO_GESTOR (L3: KIT_VETERINARIO y GENERAL)
        retiros = kardex_rows.filter(tipo_movimiento=TIPO_KARDEX_RETIRO_GESTOR)
        self.assertEqual(retiros.count(), 2)
        ret_kit = retiros.filter(ubicacion=UBICACION_KARDEX_KIT).first()
        self.assertIsNotNone(ret_kit)
        self.assertEqual(ret_kit.cantidad, 2)
        self.assertEqual(ret_kit.saldo_anterior, 10)
        self.assertEqual(ret_kit.saldo_resultante, 8)

        ret_gen = retiros.filter(ubicacion=UBICACION_KARDEX_GENERAL).first()
        self.assertIsNotNone(ret_gen)
        self.assertEqual(ret_gen.cantidad, 2)
        self.assertEqual(ret_gen.saldo_anterior, 20)
        self.assertEqual(ret_gen.saldo_resultante, 22)

    def test_kit_24b_retiro_directo_endpoint_h_c2(self):
        """
        TEST-KIT-24B: Retiro directo de stock vía POST /api/inventario/kit/retiro-directo/.
        Contrato esperado: HTTP 200 OK con:
        - cantidad_total_retirada = 7
        - unidades_baja_vencimiento = 5
        - unidades_reintegradas = 2
        - lotes_reintegrados_bodega y bajas_vencimiento

        HALLAZGO H-C2 (CORREGIDO): kit_views.py accedía a resultado['lotes_afectados'],
        clave inexistente en el retorno de retirar_stock_de_kit (kit_service.py:754-763).
        Corregido en kit_views.py para exponer lotes_reintegrados_bodega y bajas_vencimiento.
        Ya no es fallo esperado; test regular de regresión.
        """
        # Crear lotes de bodega base
        b1 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=10), estado="Agotado")
        b2 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=5), estado="Agotado")
        b3 = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Disponible")

        # Asignar lotes al kit del veterinario
        kl1 = crear_lote_kit(self.vet_a, b1, cantidad=2, cantidad_ref=2, fecha_vencimiento=b1.fecha_vencimiento)
        kl2 = crear_lote_kit(self.vet_a, b2, cantidad=3, cantidad_ref=3, fecha_vencimiento=b2.fecha_vencimiento)
        kl3 = crear_lote_kit(self.vet_a, b3, cantidad=10, cantidad_ref=10, fecha_vencimiento=b3.fecha_vencimiento)

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "id_medicamento": self.med_a.id_medicamento,
            "cantidad": 7,
            "motivo": "Retiro directo de unidades vencidas y ajuste de dotación"
        }

        response = self.client.post(
            "/api/inventario/kit/retiro-directo/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("cantidad_total_retirada"), 7)
        self.assertEqual(response.data.get("unidades_baja_vencimiento"), 5)
        self.assertEqual(response.data.get("unidades_reintegradas"), 2)

        # Aserciones sobre claves nuevas de lotes en la respuesta
        self.assertEqual(len(response.data.get("bajas_vencimiento", [])), 2)
        self.assertEqual(len(response.data.get("lotes_reintegrados_bodega", [])), 1)
        self.assertEqual(response.data["lotes_reintegrados_bodega"][0]["cantidad_retirada"], 2)
        self.assertEqual(response.data["lotes_reintegrados_bodega"][0]["saldo_resultante_bodega"], 22)
        self.assertEqual(response.data["lotes_reintegrados_bodega"][0]["saldo_restante_kit"], 8)

        # Estado en BD
        b3.refresh_from_db()
        kl1.refresh_from_db()
        kl2.refresh_from_db()
        kl3.refresh_from_db()
        self.assertEqual(b3.cantidad_actual, 22)
        self.assertEqual(kl1.cantidad_actual, 0)
        self.assertEqual(kl2.cantidad_actual, 0)
        self.assertEqual(kl3.cantidad_actual, 8)

    def test_kit_33a_semantica_referencia_lote_nuevo(self):
        """
        TEST-KIT-33A: Semántica de cantidad_referencia para lote nuevo en kit (DECISIONES §7 y §12.4).
        Al dotar un lote nuevo sin parámetros especiales, cantidad_referencia == cantidad_asignada.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_b,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=120)
        )

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Asignación simple de lote nuevo",
            "items": [
                {
                    "id_medicamento": self.med_b.id_medicamento,
                    "cantidad": 10
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        kl = KitVeterinarioLotes.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_b
        ).first()
        self.assertIsNotNone(kl)
        self.assertEqual(kl.cantidad_actual, 10)
        self.assertEqual(kl.cantidad_referencia, 10)

        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 40)

    def test_kit_33b_semantica_referencia_lote_existente_max(self):
        """
        TEST-KIT-33B: Semántica de cantidad_referencia para lote existente en kit.
        Regla por defecto: cantidad_referencia = max(referencia_previa, nueva_cantidad_actual).
        Lote con stock=2 y ref=10; asignación de 5 uds (nuevo stock=7) conserva ref=10.
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega,
            cantidad=2,
            cantidad_ref=10
        )

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Asignación adicional a lote existente",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 5
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        lote_kit.refresh_from_db()
        self.assertEqual(lote_kit.cantidad_actual, 7)
        self.assertEqual(lote_kit.cantidad_referencia, 10)

        lote_bodega.refresh_from_db()
        self.assertEqual(lote_bodega.cantidad_actual, 45)

    def test_kit_33c_semantica_referencia_modo_incremento(self):
        """
        TEST-KIT-33C: Semántica de cantidad_referencia en modo incremento.
        Parámetro incrementar_cantidad_referencia=True suma la cantidad asignada a la referencia previa.
        Lote con stock=5 y ref=10; asignación de 5 uds pasa a stock=10 y ref=15 (10 + 5).
        """
        lote_bodega = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90)
        )
        lote_kit = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=lote_bodega,
            cantidad=5,
            cantidad_ref=10
        )

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Incremento explícito de referencia",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 5,
                    "incrementar_cantidad_referencia": True
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        lote_kit.refresh_from_db()
        self.assertEqual(lote_kit.cantidad_actual, 10)
        self.assertEqual(lote_kit.cantidad_referencia, 15)

    def test_kit_33d_semantica_referencia_modo_fijacion_total(self):
        """
        TEST-KIT-33D: Semántica de cantidad_referencia en modo fijación total (fijar_cantidad_referencia=N).
        Lotes vigentes con stock total=10 (L1=6, L2=4).
        Asignación de 5 uds con fijar_cantidad_referencia=25 (nuevo stock=15):
        - Suma total de referencias queda en exactamente 25.
        - Remanente (25 - 15 = 10) es absorbido por el lote más reciente tocado.
        - Si N < 15, rechaza con HTTP 400 Bad Request, error='CANTIDAD_INVALIDA'.
        """
        b1 = crear_lote_bodega(self.med_a, cantidad=0, fecha_compra=self.hoy - timedelta(days=60), fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        b2 = crear_lote_bodega(self.med_a, cantidad=0, fecha_compra=self.hoy - timedelta(days=30), fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        b3 = crear_lote_bodega(self.med_a, cantidad=30, fecha_compra=self.hoy - timedelta(days=10), fecha_vencimiento=self.hoy + timedelta(days=90), estado="Disponible")

        l1 = crear_lote_kit(self.vet_a, b1, cantidad=6, cantidad_ref=6, fecha_compra_origen=b1.id_detalle_compra.id_compra.fecha)
        l2 = crear_lote_kit(self.vet_a, b2, cantidad=4, cantidad_ref=4, fecha_compra_origen=b2.id_detalle_compra.id_compra.fecha)

        # 1. Caso inválido: N menor a la suma de stock resultante (15)
        payload_invalido = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Fijación inválida por debajo de stock",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 5,
                    "fijar_cantidad_referencia": 14
                }
            ]
        }
        res_inv = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_invalido,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_inv.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_inv.data.get("error"), "CANTIDAD_INVALIDA")

        # 2. Caso válido: N = 25 >= 15
        payload_valido = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Fijación total válida a 25 unidades",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 5,
                    "fijar_cantidad_referencia": 25
                }
            ]
        }
        res_val = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_valido,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_val.status_code, status.HTTP_201_CREATED)

        l1.refresh_from_db()
        l2.refresh_from_db()
        l3 = KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a, id_inventario=b3).first()
        self.assertIsNotNone(l3)

        self.assertEqual(l1.cantidad_actual, 6)
        self.assertEqual(l1.cantidad_referencia, 6)
        self.assertEqual(l2.cantidad_actual, 4)
        self.assertEqual(l2.cantidad_referencia, 4)
        self.assertEqual(l3.cantidad_actual, 5)
        # Remanente = 25 - 15 = 10 asignado a L3 (lote tocado): ref = 5 + 10 = 15
        self.assertEqual(l3.cantidad_referencia, 15)

        suma_ref = l1.cantidad_referencia + l2.cantidad_referencia + l3.cantidad_referencia
        self.assertEqual(suma_ref, 25)

    def test_kit_33e_semantica_referencia_lotes_vencidos_cero(self):
        """
        TEST-KIT-33E: Decisión 3.A: Lotes vencidos quedan con cantidad_referencia = 0 ante fijación total.
        Vet con Lote A (vencido, stock=3, ref=5) y Lote B (vigente, stock=5, ref=10).
        Stock total=8 (5 vigente, 3 vencido).
        Gestor asigna 2 uds de nuevo Lote C vigente con fijar_cantidad_referencia=15:
        1. Lote A (vencido) queda estrictamente con ref=0 y conserva cantidad_actual=3.
        2. Lote B (vigente) queda con ref=5 (cantidad_actual).
        3. Lote C (vigente recién tocado) absorbe el remanente (15 - 7 = 8), quedando con ref 2 + 8 = 10.
        4. Suma de referencias vigentes es exactamente 5 + 10 = 15.
        5. Piso de N es el stock vigente (7): fijar N=6 da HTTP 400 CANTIDAD_INVALIDA.
        """
        b_vencido = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=10), estado="Agotado")
        b_vigente = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        b_nuevo = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=120), estado="Disponible")

        l_vencido = crear_lote_kit(self.vet_a, b_vencido, cantidad=3, cantidad_ref=5, fecha_vencimiento=b_vencido.fecha_vencimiento)
        l_vigente = crear_lote_kit(self.vet_a, b_vigente, cantidad=5, cantidad_ref=10, fecha_vencimiento=b_vigente.fecha_vencimiento)

        # 1. Comprobar piso de stock vigente: tras asignar 2 uds, el stock vigente es 5 + 2 = 7.
        # Si se intenta fijar N=6 (< 7), debe fallar con CANTIDAD_INVALIDA.
        payload_piso = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Intento de fijar por debajo de stock vigente",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 2,
                    "fijar_cantidad_referencia": 6
                }
            ]
        }
        res_piso = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_piso,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_piso.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_piso.data.get("error"), "CANTIDAD_INVALIDA")

        # 2. Asignación válida fijando N=15
        payload_valido = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Fijación total con lotes vencidos en kit",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 2,
                    "fijar_cantidad_referencia": 15
                }
            ]
        }
        res_val = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_valido,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_val.status_code, status.HTTP_201_CREATED)

        l_vencido.refresh_from_db()
        l_vigente.refresh_from_db()
        l_nuevo = KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a, id_inventario=b_nuevo).first()
        self.assertIsNotNone(l_nuevo)

        # Lote vencido queda estrictamente con ref=0 y conserva stock=3
        self.assertEqual(l_vencido.cantidad_actual, 3)
        self.assertEqual(l_vencido.cantidad_referencia, 0)

        # Lote vigente previo queda con ref=5
        self.assertEqual(l_vigente.cantidad_actual, 5)
        self.assertEqual(l_vigente.cantidad_referencia, 5)

        # Lote nuevo tocado absorbe remanente (15 - 7 = 8): ref = 2 + 8 = 10
        self.assertEqual(l_nuevo.cantidad_actual, 2)
        self.assertEqual(l_nuevo.cantidad_referencia, 10)

        # Suma de referencias vigentes es exactamente 15
        suma_vigentes = l_vigente.cantidad_referencia + l_nuevo.cantidad_referencia
        self.assertEqual(suma_vigentes, 15)

    def test_kit_34_reintegro_lote_agotado_pasa_a_disponible(self):
        """
        TEST-KIT-34: Reintegro a lote agotado en bodega pasa a 'Disponible' (Decisión §5).
        Lote en bodega con cantidad_actual=0 y estado='Agotado'.
        Retiro de unidades del kit hacia ese lote en bodega:
        - inventarios.cantidad_actual > 0
        - inventarios.estado == 'Disponible' (visibilizado para v_inventario_general_lotes).
        """
        b_agotado = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=0,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            estado="Agotado"
        )
        l_kit = crear_lote_kit(
            veterinario=self.vet_a,
            lote_bodega=b_agotado,
            cantidad=5,
            cantidad_ref=5,
            fecha_vencimiento=b_agotado.fecha_vencimiento
        )

        resultado = kit_service.retirar_stock_de_kit(
            id_gestor=self.gestor.id_usuario,
            id_veterinario=self.vet_a.id_usuario,
            id_medicamento=self.med_a.id_medicamento,
            cantidad=3,
            motivo="Reintegro de stock vigente a lote agotado de bodega"
        )

        self.assertEqual(resultado["unidades_reintegradas_bodega"], 3)
        self.assertEqual(resultado["unidades_baja_vencimiento"], 0)

        b_agotado.refresh_from_db()
        self.assertEqual(b_agotado.cantidad_actual, 3)
        self.assertEqual(b_agotado.estado, "Disponible")

        l_kit.refresh_from_db()
        self.assertEqual(l_kit.cantidad_actual, 2)

        # Kardex: 2 filas RETIRO_GESTOR
        k_rows = InventarioKardex.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a,
            tipo_movimiento=TIPO_KARDEX_RETIRO_GESTOR
        )
        self.assertEqual(k_rows.count(), 2)

    def test_kit_35_medicamento_inactivo_no_asignable_pero_operable(self):
        """
        TEST-KIT-35: Medicamento inactivo (activo=False): no asignable pero retirable y consumible (Decisión §8).
        - Intento de asignación falla con HTTP 400 Bad Request, error='MEDICAMENTO_INVALIDO'.
        - Retiro y consumo de insumos ya entregados al veterinario proceden exitosamente.
        """
        med_inactivo = crear_medicamento(nombre="Fármaco Descontinuado CBA", activo=True)
        b_lote = crear_lote_bodega(med_inactivo, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=90))
        l_kit = crear_lote_kit(self.vet_a, b_lote, cantidad=10, cantidad_ref=10)

        # Desactivar medicamento en catálogo institucional
        med_inactivo.activo = False
        med_inactivo.save()

        # 1. Asignación rechazada: HTTP 400 MEDICAMENTO_INVALIDO
        payload_asig = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Intento de dotar fármaco descontinuado",
            "items": [
                {
                    "id_medicamento": med_inactivo.id_medicamento,
                    "cantidad": 5
                }
            ]
        }
        res_asig = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_asig,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(res_asig.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_asig.data.get("error"), "MEDICAMENTO_INVALIDO")

        # Bodega y kit permanecen inalterados
        b_lote.refresh_from_db()
        self.assertEqual(b_lote.cantidad_actual, 20)
        l_kit.refresh_from_db()
        self.assertEqual(l_kit.cantidad_actual, 10)

        # 2. Retiro directo procede a nivel de servicio sin bloqueo
        res_retiro = kit_service.retirar_stock_de_kit(
            id_gestor=self.gestor.id_usuario,
            id_veterinario=self.vet_a.id_usuario,
            id_medicamento=med_inactivo.id_medicamento,
            cantidad=3,
            motivo="Retiro de medicamento inactivo del kit"
        )
        self.assertEqual(res_retiro["unidades_reintegradas_bodega"], 3)
        l_kit.refresh_from_db()
        self.assertEqual(l_kit.cantidad_actual, 7)

        # 3. Consumo clínico procede exitosamente sin bloqueo
        consulta = crear_consulta()
        consumo_payload = [
            {
                "id_medicamento": med_inactivo.id_medicamento,
                "unidades": 2,
                "client_uuid": uuid.uuid4()
            }
        ]
        res_consumo = consumo_service.descontar_consumo_kit(
            id_veterinario=self.vet_a.id_usuario,
            consumos=consumo_payload,
            origen_tipo="HISTORIA_CLINICA",
            origen_ids={"id_consulta": consulta.id_consulta},
            es_offline=False
        )
        self.assertEqual(len(res_consumo["aplicados"]), 1)
        self.assertEqual(res_consumo["aplicados"][0]["unidades_descontadas"], 2)

        l_kit.refresh_from_db()
        self.assertEqual(l_kit.cantidad_actual, 5)

    def test_kit_36_frontera_vigencia_hoy_vs_ayer(self):
        """
        TEST-KIT-36: Frontera de vigencia: fecha_vencimiento == hoy es vigente y consumible;
        fecha_vencimiento == hoy - 1 es ignorado por vencido (Decisión 10, §11.7).
        Lote A vence hoy (5 uds); Lote B venció ayer (5 uds).
        Consumo de 3 uds toma exclusivamente de Lote A; Lote B queda intacto.
        """
        b_hoy = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy, estado="Agotado")
        b_ayer = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=1), estado="Agotado")

        kl_hoy = crear_lote_kit(self.vet_a, b_hoy, cantidad=5, cantidad_ref=5, fecha_vencimiento=self.hoy)
        kl_ayer = crear_lote_kit(self.vet_a, b_ayer, cantidad=5, cantidad_ref=5, fecha_vencimiento=self.hoy - timedelta(days=1))

        consulta = crear_consulta()
        u_consumo = uuid.uuid4()

        resultado = consumo_service.descontar_consumo_kit(
            id_veterinario=self.vet_a.id_usuario,
            consumos=[
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "unidades": 3,
                    "client_uuid": u_consumo
                }
            ],
            origen_tipo="HISTORIA_CLINICA",
            origen_ids={"id_consulta": consulta.id_consulta},
            es_offline=False
        )

        self.assertEqual(len(resultado["aplicados"]), 1)
        self.assertEqual(resultado["aplicados"][0]["unidades_descontadas"], 3)

        kl_hoy.refresh_from_db()
        self.assertEqual(kl_hoy.cantidad_actual, 2)

        kl_ayer.refresh_from_db()
        self.assertEqual(kl_ayer.cantidad_actual, 5)

        # Kardex: exactamente 1 fila sobre lote de hoy
        k_rows = InventarioKardex.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a,
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA
        )
        self.assertEqual(k_rows.count(), 1)
        k = k_rows.first()
        self.assertEqual(k.id_inventario_id, b_hoy.id_inventario)
        self.assertEqual(k.cantidad, 3)

    def test_kit_37_fifo_nulls_first_sin_fecha_compra(self):
        """
        TEST-KIT-37: FIFO NULLS FIRST en bodega general (Decisión §1).
        Bodega con Lote A (fecha_compra=None, 10 uds) y Lote B (con fecha_compra, 10 uds).
        Asignación de 5 uds descuenta prioritariamente de Lote A; Lote B queda intacto.
        """
        b_sin_fecha = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=10,
            fecha_compra=None,
            fecha_vencimiento=self.hoy + timedelta(days=180)
        )
        # Asegurar explícitamente que fecha de compra sea NULL en BD (auto_now_add la crea con now)
        Compra.objects.filter(pk=b_sin_fecha.id_detalle_compra.id_compra_id).update(fecha=None)

        b_con_fecha = crear_lote_bodega(
            medicamento=self.med_a,
            cantidad=10,
            fecha_compra=self.hoy - timedelta(days=30),
            fecha_vencimiento=self.hoy + timedelta(days=180)
        )

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Asignación FIFO prioritaria sin fecha de compra",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 5
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        b_sin_fecha.refresh_from_db()
        self.assertEqual(b_sin_fecha.cantidad_actual, 5)

        b_con_fecha.refresh_from_db()
        self.assertEqual(b_con_fecha.cantidad_actual, 10)

        # Kit recibe lote asociado a b_sin_fecha
        kl = KitVeterinarioLotes.objects.filter(
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a
        ).first()
        self.assertIsNotNone(kl)
        self.assertEqual(kl.id_inventario_id, b_sin_fecha.id_inventario)
        self.assertEqual(kl.cantidad_actual, 5)

    def test_kit_38_validaciones_asignar_duplicados_bool_contradictorios(self):
        """
        TEST-KIT-38: Validaciones en /api/inventario/kit/asignar/ (DISENO §5.3, §4.5).
        Casos evaluados mediante subTest:
        1. Medicamento duplicado en items: error de dominio kit_service ('CANTIDAD_INVALIDA').
        2. Fijar e incrementar simultáneamente: error de dominio kit_service ('CANTIDAD_INVALIDA').
        3. Cantidad cero (0): validación de serializador DRF (ItemAsignarSerializer), error raíz es None.
        4. Cantidad negativa (-5): validación de serializador DRF (ItemAsignarSerializer), error raíz es None.
        5. Cantidad booleana (True): validación de serializador DRF (ItemAsignarSerializer), error raíz es None.
        Todos retornan HTTP 400 Bad Request sin cambios en BD.
        """
        casos = [
            (
                "duplicado",
                [
                    {"id_medicamento": self.med_a.id_medicamento, "cantidad": 5},
                    {"id_medicamento": self.med_a.id_medicamento, "cantidad": 3},
                ],
                "CANTIDAD_INVALIDA"
            ),
            (
                "fijar_e_incrementar",
                [
                    {
                        "id_medicamento": self.med_a.id_medicamento,
                        "cantidad": 5,
                        "fijar_cantidad_referencia": 10,
                        "incrementar_cantidad_referencia": True,
                    }
                ],
                "CANTIDAD_INVALIDA"
            ),
            (
                "cantidad_cero",
                [
                    {"id_medicamento": self.med_a.id_medicamento, "cantidad": 0}
                ],
                None
            ),
            (
                "cantidad_negativa",
                [
                    {"id_medicamento": self.med_a.id_medicamento, "cantidad": -5}
                ],
                None
            ),
            (
                "cantidad_booleana",
                [
                    {"id_medicamento": self.med_a.id_medicamento, "cantidad": True}
                ],
                None
            ),
        ]

        for nombre_caso, items_payload, error_esperado in casos:
            with self.subTest(caso=nombre_caso):
                payload = {
                    "id_veterinario": self.vet_a.id_usuario,
                    "motivo": f"Validación caso {nombre_caso}",
                    "items": items_payload,
                }
                response = self.client.post(
                    "/api/inventario/kit/asignar/",
                    payload,
                    format="json",
                    **self.headers_gestor
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data.get("error"), error_esperado)

                # Invariantes en BD: sin filas creadas
                self.assertEqual(
                    KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
                    0
                )
                self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_40_rollback_multi_item_stock_insuficiente(self):
        """
        TEST-KIT-40: Rollback multi-ítem ante stock insuficiente en bodega.
        Ítem 1 (Med A) cuenta con stock suficiente (50 uds).
        Ítem 2 (Med B) NO cuenta con stock suficiente (solicita 20, hay 5).
        HTTP 400 Bad Request, error='STOCK_INSUFICIENTE_BODEGA'.
        Rollback integral: Ítem 1 no se descuenta de bodega ni se asigna al kit; cero kardex.
        """
        b_med_a = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=90))
        b_med_b = crear_lote_bodega(self.med_b, cantidad=5, fecha_vencimiento=self.hoy + timedelta(days=90))

        payload = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Asignación multi-ítem con fallo parcial",
            "items": [
                {
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 10
                },
                {
                    "id_medicamento": self.med_b.id_medicamento,
                    "cantidad": 20
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/asignar/",
            payload,
            format="json",
            **self.headers_gestor
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("error"), "STOCK_INSUFICIENTE_BODEGA")

        # Verificación de Rollback en BD: Bodega
        b_med_a.refresh_from_db()
        self.assertEqual(b_med_a.cantidad_actual, 50)

        b_med_b.refresh_from_db()
        self.assertEqual(b_med_b.cantidad_actual, 5)

        # Verificación de Rollback en BD: Kit y Kardex
        self.assertEqual(
            KitVeterinarioLotes.objects.filter(id_veterinario=self.vet_a).count(),
            0
        )
        self.assertEqual(InventarioKardex.objects.count(), 0)

    def test_kit_41b_sincronizar_offline_exitoso_sin_conflictos(self):
        """
        TEST-KIT-41B: Sincronización offline dedicada 100% exitosa sin conflictos (DISENO §5.3).
        Vet con stock suficiente para Med A (5 uds) y Med B (5 uds).
        Envío de lote de consumos vía POST /api/inventario/kit/sincronizar-offline/:
        - HTTP 200 OK, {"aplicados": [...], "conflictos": []}. Sin clave error.
        - Cada ítem en aplicados incluye client_uuid, id_medicamento, unidades_descontadas y lotes_afectados[].cantidad_descontada.
        - Saldo en kit: Med A pasa a 3 uds, Med B pasa a 2 uds.
        - 2 consumos en InventarioConsumosRegistro con estado='APLICADO' y codigo_http=200.
        - 2 filas en InventarioKardex con CONSUMO_HISTORIA en KIT_VETERINARIO.
        """
        b_med_a = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        b_med_b = crear_lote_bodega(self.med_b, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")

        l_med_a = crear_lote_kit(self.vet_a, b_med_a, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_med_a.fecha_vencimiento)
        l_med_b = crear_lote_kit(self.vet_a, b_med_b, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_med_b.fecha_vencimiento)

        consulta = crear_consulta()
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()

        payload = {
            "consumos": [
                {
                    "client_uuid": str(u1),
                    "id_medicamento": self.med_a.id_medicamento,
                    "unidades": 2,
                    "id_consulta": consulta.id_consulta
                },
                {
                    "client_uuid": str(u2),
                    "id_medicamento": self.med_b.id_medicamento,
                    "unidades": 3,
                    "id_consulta": consulta.id_consulta
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/sincronizar-offline/",
            payload,
            format="json",
            **self.headers_vet_a
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("error", response.data)
        self.assertEqual(response.data.get("conflictos"), [])
        aplicados = response.data.get("aplicados", [])
        self.assertEqual(len(aplicados), 2)

        # Verificar estructura de aplicados (consumo_service.py:776-787)
        for ap in aplicados:
            self.assertIn("client_uuid", ap)
            self.assertIn("id_medicamento", ap)
            self.assertIn("unidades_descontadas", ap)
            self.assertIn("lotes_afectados", ap)
            self.assertGreater(len(ap["lotes_afectados"]), 0)
            self.assertIn("cantidad_descontada", ap["lotes_afectados"][0])

        # Verificación en BD: Kit
        l_med_a.refresh_from_db()
        self.assertEqual(l_med_a.cantidad_actual, 3)

        l_med_b.refresh_from_db()
        self.assertEqual(l_med_b.cantidad_actual, 2)

        # Verificación en BD: InventarioConsumosRegistro
        reg1 = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg1)
        self.assertEqual(reg1.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg1.codigo_http, 200)
        self.assertEqual(reg1.cantidad_descontada, 2)

        reg2 = InventarioConsumosRegistro.objects.filter(client_uuid=u2).first()
        self.assertIsNotNone(reg2)
        self.assertEqual(reg2.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg2.codigo_http, 200)
        self.assertEqual(reg2.cantidad_descontada, 3)

        # Verificación en BD: InventarioKardex (2 filas en KIT_VETERINARIO)
        k_rows = InventarioKardex.objects.filter(
            id_veterinario=self.vet_a,
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA
        ).order_by("id_kardex")
        self.assertEqual(k_rows.count(), 2)

        k1 = k_rows.filter(client_uuid=u1).first()
        self.assertIsNotNone(k1)
        self.assertEqual(k1.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k1.cantidad, 2)
        self.assertEqual(k1.saldo_anterior, 5)
        self.assertEqual(k1.saldo_resultante, 3)

        k2 = k_rows.filter(client_uuid=u2).first()
        self.assertIsNotNone(k2)
        self.assertEqual(k2.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k2.cantidad, 3)
        self.assertEqual(k2.saldo_anterior, 5)
        self.assertEqual(k2.saldo_resultante, 2)

    def test_kit_41b_alias_cantidad(self):
        """
        TEST-KIT-41B (Alias 'cantidad'): Sincronización offline usando la clave 'cantidad'
        en lugar de 'unidades' (kit_serializers.py:187-213 normaliza a 'unidades').
        Verifica HTTP 200 OK, descuento correcto en kit, registro APLICADO y kardex CONSUMO_HISTORIA.
        """
        b_med_a = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        l_med_a = crear_lote_kit(self.vet_a, b_med_a, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_med_a.fecha_vencimiento)

        consulta = crear_consulta()
        u_alias = uuid.uuid4()

        payload = {
            "consumos": [
                {
                    "client_uuid": str(u_alias),
                    "id_medicamento": self.med_a.id_medicamento,
                    "cantidad": 2,
                    "id_consulta": consulta.id_consulta,
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/sincronizar-offline/",
            payload,
            format="json",
            **self.headers_vet_a
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("error", response.data)
        self.assertEqual(response.data.get("conflictos"), [])
        aplicados = response.data.get("aplicados", [])
        self.assertEqual(len(aplicados), 1)

        ap = aplicados[0]
        self.assertEqual(ap.get("client_uuid"), str(u_alias))
        self.assertEqual(ap.get("unidades_descontadas"), 2)
        self.assertGreater(len(ap.get("lotes_afectados", [])), 0)
        self.assertEqual(ap["lotes_afectados"][0].get("cantidad_descontada"), 2)

        # Verificación en BD: Kit (descontado de 5 a 3)
        l_med_a.refresh_from_db()
        self.assertEqual(l_med_a.cantidad_actual, 3)

        # Verificación en BD: InventarioConsumosRegistro (APLICADO, 200, cantidad_descontada=2)
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u_alias).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg.codigo_http, 200)
        self.assertEqual(reg.cantidad_descontada, 2)

        # Verificación en BD: InventarioKardex (1 fila CONSUMO_HISTORIA en KIT_VETERINARIO)
        k_row = InventarioKardex.objects.filter(
            client_uuid=u_alias,
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA
        ).first()
        self.assertIsNotNone(k_row)
        self.assertEqual(k_row.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k_row.cantidad, 2)
        self.assertEqual(k_row.saldo_anterior, 5)
        self.assertEqual(k_row.saldo_resultante, 3)

    def test_kit_41b_sin_cantidad_ni_unidades(self):
        """
        TEST-KIT-41B (Sin cantidad ni unidades): Sincronización offline omitiendo ambas claves
        en el ítem de consumo (kit_serializers.py:204-205).
        Verifica respuesta HTTP 400 Bad Request por error de validación de DRF
        ('Debe especificar \'cantidad\' o \'unidades\'.') y cero impacto en BD.
        """
        b_med_a = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        l_med_a = crear_lote_kit(self.vet_a, b_med_a, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_med_a.fecha_vencimiento)

        consulta = crear_consulta()
        u_invalido = uuid.uuid4()

        payload = {
            "consumos": [
                {
                    "client_uuid": str(u_invalido),
                    "id_medicamento": self.med_a.id_medicamento,
                    "id_consulta": consulta.id_consulta,
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/sincronizar-offline/",
            payload,
            format="json",
            **self.headers_vet_a
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("consumos", response.data)
        item_errors = response.data["consumos"][0]
        self.assertIn("non_field_errors", item_errors)
        self.assertIn("Debe especificar 'cantidad' o 'unidades'.", item_errors["non_field_errors"])

        # Invariantes en BD: sin cambios en kit, sin registros de consumo, sin kardex
        l_med_a.refresh_from_db()
        self.assertEqual(l_med_a.cantidad_actual, 5)
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_invalido).count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_invalido).count(), 0)

    def test_kit_36b_caracterizacion_normalizar_fecha_compra(self):
        """
        TEST-KIT-36B: Caracterización de _normalizar_fecha_compra y desplazamiento FIFO entre fechas naive y aware.
        Invocación con None, date(2026, 5, 1), datetime(2026, 5, 1, 10, 0) naive
        y datetime(2026, 5, 1, 10, 0, tzinfo=ZoneInfo('America/Bogota')) aware (UTC-5).
        Retorna respectivamente datetime.min, datetime(2026, 5, 1, 0, 0) naive,
        datetime(2026, 5, 1, 10, 0) naive y datetime(2026, 5, 1, 15, 0) naive.
        Caracteriza contraste con DECISIONES §11.8: dos compras locales a las 10:00 difieren
        en 5 horas en la ordenación Python/SQL (10:00 vs 15:00), alterando la prioridad FIFO de asignación.
        El test documenta el comportamiento actual sin modificarlo.
        """
        from datetime import date, datetime
        from zoneinfo import ZoneInfo
        from inventario.services.kit_service import _normalizar_fecha_compra

        # 1. Caso None -> datetime.min naive
        res_none = _normalizar_fecha_compra(None)
        self.assertEqual(res_none, datetime.min)
        self.assertIsNone(res_none.tzinfo)

        # 2. Caso date -> datetime(year, month, day, 0, 0) naive
        d = date(2026, 5, 1)
        res_date = _normalizar_fecha_compra(d)
        self.assertEqual(res_date, datetime(2026, 5, 1, 0, 0))
        self.assertIsNone(res_date.tzinfo)

        # 3. Caso datetime naive -> datetime identico naive
        dt_naive = datetime(2026, 5, 1, 10, 0)
        res_naive = _normalizar_fecha_compra(dt_naive)
        self.assertEqual(res_naive, datetime(2026, 5, 1, 10, 0))
        self.assertIsNone(res_naive.tzinfo)

        # 4. Caso datetime aware (America/Bogota UTC-5) -> conversion a UTC (15:00) y remocion de tzinfo (naive)
        dt_aware = datetime(2026, 5, 1, 10, 0, tzinfo=ZoneInfo("America/Bogota"))
        res_aware = _normalizar_fecha_compra(dt_aware)
        self.assertEqual(res_aware, datetime(2026, 5, 1, 15, 0))
        self.assertIsNone(res_aware.tzinfo)

        # Caracterizacion del desplazamiento relativo de 5 horas entre compras locales a las 10:00
        # aware (15:00) vs naive (10:00): en ordenamiento FIFO la naive parece 5 horas mas antigua
        delta = res_aware - res_naive
        self.assertEqual(delta, timedelta(hours=5))

    def test_kit_39_asignar_medicamento_invalido_o_sin_lotes(self):
        """
        TEST-KIT-39: Validaciones de kit_service al asignar: Medicamento inexistente o sin lotes.
        Gestor autenticado intenta asignar:
        1. Medicamento inexistente en catalogo institucional -> HTTP 400 Bad Request, error='MEDICAMENTO_INVALIDO'.
        2. Medicamento existente pero sin lotes disponibles/vigentes en bodega CBA -> HTTP 400 Bad Request, error='MEDICAMENTO_SIN_LOTES_ASIGNABLES'.
        Verifica cero modificaciones en tablas de inventario (KitVeterinarioLotes, Inventarios, InventarioKardex).
        """
        count_kit_ini = KitVeterinarioLotes.objects.count()
        count_inv_ini = Inventarios.objects.count()
        count_kardex_ini = InventarioKardex.objects.count()

        # Escenario 1: Medicamento inexistente (id_medicamento=999999)
        payload_inexistente = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Dotacion medicamento inexistente",
            "items": [
                {
                    "id_medicamento": 999999,
                    "cantidad": 10
                }
            ]
        }
        resp1 = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_inexistente,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(resp1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp1.data.get("error"), "MEDICAMENTO_INVALIDO")

        # Escenario 2: Medicamento existente pero sin lotes en bodega
        med_sin_lotes = crear_medicamento(nombre="Med Sin Lotes CBA 39")
        payload_sin_lotes = {
            "id_veterinario": self.vet_a.id_usuario,
            "motivo": "Dotacion medicamento sin lotes",
            "items": [
                {
                    "id_medicamento": med_sin_lotes.id_medicamento,
                    "cantidad": 5
                }
            ]
        }
        resp2 = self.client.post(
            "/api/inventario/kit/asignar/",
            payload_sin_lotes,
            format="json",
            **self.headers_gestor
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.data.get("error"), "MEDICAMENTO_SIN_LOTES_ASIGNABLES")

        # Invariantes en base de datos: cero filas modificadas / anadidas
        self.assertEqual(KitVeterinarioLotes.objects.count(), count_kit_ini)
        self.assertEqual(Inventarios.objects.count(), count_inv_ini)
        self.assertEqual(InventarioKardex.objects.count(), count_kardex_ini)

    def test_kit_41_sincronizar_offline_mixto_con_conflictos(self):
        """
        TEST-KIT-41: Sincronización offline dedicada con payload mixto y conflictos (DISENO §5.3).
        Veterinario con stock suficiente para Med A (5 unidades) pero sin stock para Med B (0 unidades).
        POST /api/inventario/kit/sincronizar-offline/ con lote [Med A (2 uds), Med B (3 uds)]:
        - HTTP 409 Conflict, error='CONFLICTO_STOCK_KIT', con aplicados y conflictos.
        - Med A: descontado exitosamente en kit (saldo pasa de 5 a 3), registro en InventarioConsumosRegistro con estado='APLICADO' y fila kardex CONSUMO_HISTORIA.
        - Med B: registrado en InventarioConsumosRegistro con estado='RECHAZADO_CONFLICTO' y codigo_http=409, sin kardex.
        """
        from inventario.constants import CONSUMO_ESTADO_RECHAZADO_CONFLICTO

        # Setup: Med A con 5 unidades en kit; Med B sin stock en kit para vet_a
        b_med_a = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        l_med_a = crear_lote_kit(self.vet_a, b_med_a, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_med_a.fecha_vencimiento)

        consulta = crear_consulta()
        u_aplica = uuid.uuid4()
        u_conflicto = uuid.uuid4()

        payload = {
            "consumos": [
                {
                    "client_uuid": str(u_aplica),
                    "id_medicamento": self.med_a.id_medicamento,
                    "unidades": 2,
                    "id_consulta": consulta.id_consulta,
                },
                {
                    "client_uuid": str(u_conflicto),
                    "id_medicamento": self.med_b.id_medicamento,
                    "unidades": 3,
                    "id_consulta": consulta.id_consulta,
                }
            ]
        }

        response = self.client.post(
            "/api/inventario/kit/sincronizar-offline/",
            payload,
            format="json",
            **self.headers_vet_a
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("error"), "CONFLICTO_STOCK_KIT")

        aplicados = response.data.get("aplicados", [])
        conflictos = response.data.get("conflictos", [])
        self.assertEqual(len(aplicados), 1)
        self.assertEqual(len(conflictos), 1)

        # Verificacion item aplicado
        ap = aplicados[0]
        self.assertEqual(ap.get("client_uuid"), str(u_aplica))
        self.assertEqual(ap.get("id_medicamento"), self.med_a.id_medicamento)
        self.assertEqual(ap.get("unidades_descontadas"), 2)
        self.assertEqual(ap.get("estado"), CONSUMO_ESTADO_APLICADO)
        self.assertEqual(ap.get("codigo_http"), 200)

        # Verificacion item en conflicto
        conf = conflictos[0]
        self.assertEqual(conf.get("client_uuid"), str(u_conflicto))
        self.assertEqual(conf.get("id_medicamento"), self.med_b.id_medicamento)
        self.assertEqual(conf.get("unidades"), 3)
        self.assertEqual(conf.get("stock_disponible_actual"), 0)
        self.assertEqual(conf.get("estado"), CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(conf.get("codigo_http"), 409)
        self.assertIn("Stock insuficiente en el kit", conf.get("motivo_rechazo", ""))

        # Verificacion en BD: Med A descontado de 5 a 3
        l_med_a.refresh_from_db()
        self.assertEqual(l_med_a.cantidad_actual, 3)

        # Verificacion en BD: Registro APLICADO para u_aplica
        reg_ap = InventarioConsumosRegistro.objects.filter(client_uuid=u_aplica).first()
        self.assertIsNotNone(reg_ap)
        self.assertEqual(reg_ap.estado, CONSUMO_ESTADO_APLICADO)
        self.assertEqual(reg_ap.codigo_http, 200)
        self.assertEqual(reg_ap.cantidad_descontada, 2)

        # Verificacion en BD: Registro RECHAZADO_CONFLICTO para u_conflicto
        reg_conf = InventarioConsumosRegistro.objects.filter(client_uuid=u_conflicto).first()
        self.assertIsNotNone(reg_conf)
        self.assertEqual(reg_conf.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
        self.assertEqual(reg_conf.codigo_http, 409)
        self.assertEqual(reg_conf.cantidad_descontada, 0)
        self.assertEqual(reg_conf.cantidad_solicitada, 3)
        self.assertEqual(reg_conf.stock_disponible_al_rechazo, 0)

        # Verificacion en BD: Kardex (1 fila para Med A, 0 filas para Med B)
        k_ap = InventarioKardex.objects.filter(client_uuid=u_aplica).first()
        self.assertIsNotNone(k_ap)
        self.assertEqual(k_ap.tipo_movimiento, TIPO_KARDEX_CONSUMO_HISTORIA)
        self.assertEqual(k_ap.ubicacion, UBICACION_KARDEX_KIT)
        self.assertEqual(k_ap.cantidad, 2)
        self.assertEqual(k_ap.saldo_anterior, 5)
        self.assertEqual(k_ap.saldo_resultante, 3)

        k_conf_count = InventarioKardex.objects.filter(client_uuid=u_conflicto).count()
        self.assertEqual(k_conf_count, 0)

    def test_kit_42a_consulta_mi_kit(self):
        """
        TEST-KIT-42A: Consulta del Kit Propio (/api/inventario/kit/mi-kit/).
        Veterinario A autenticado con 2 lotes en kit (1 vigente de 10 uds, 1 vencido de 5 uds).
        Verifica:
        - Vet A: HTTP 200 OK con estructura real {'total_unidades': 15, 'items': [...]},
          con saldos disponible=10 y vencido=5.
        - Gestor (Rol 1): HTTP 403 Forbidden (EsVeterinario).
        - Jurídico (Rol 2): HTTP 403 Forbidden (EsVeterinario).
        - Peticionario (Rol 4): HTTP 403 Forbidden (EsVeterinario).
        - Base de datos inalterada (solo lectura).
        """
        b_vig = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=60), estado="Agotado")
        b_ven = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=15), estado="Agotado")

        crear_lote_kit(self.vet_a, b_vig, cantidad=10, cantidad_ref=10, fecha_vencimiento=b_vig.fecha_vencimiento)
        crear_lote_kit(self.vet_a, b_ven, cantidad=5, cantidad_ref=5, fecha_vencimiento=b_ven.fecha_vencimiento)

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42a@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42a@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Consulta autorizada como Vet A -> 200 OK
        resp_vet = self.client.get(
            "/api/inventario/kit/mi-kit/",
            **self.headers_vet_a
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_200_OK)
        self.assertIn("total_unidades", resp_vet.data)
        self.assertIn("items", resp_vet.data)
        self.assertEqual(resp_vet.data["total_unidades"], 15)

        items = resp_vet.data["items"]
        item_med = next((it for it in items if it["id_medicamento"] == self.med_a.id_medicamento), None)
        self.assertIsNotNone(item_med)
        self.assertEqual(item_med["saldo_disponible"], 10)
        self.assertEqual(item_med["saldo_vencido"], 5)
        self.assertEqual(len(item_med["lotes"]), 2)

        for lote_dict in item_med["lotes"]:
            self.assertIn("id_kit_lote", lote_dict)
            self.assertIn("id_inventario", lote_dict)
            self.assertIn("cantidad_actual", lote_dict)
            self.assertIn("cantidad_referencia", lote_dict)
            self.assertIn("fecha_vencimiento", lote_dict)
            self.assertIn("vencido", lote_dict)

        # 2. Roles no autorizados -> 403 Forbidden (EsVeterinario)
        resp_gestor = self.client.get("/api/inventario/kit/mi-kit/", **self.headers_gestor)
        self.assertEqual(resp_gestor.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol2 = self.client.get("/api/inventario/kit/mi-kit/", **headers_rol2)
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get("/api/inventario/kit/mi-kit/", **headers_rol4)
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

    def test_kit_42b_consulta_kit_veterinario_gestor(self):
        """
        TEST-KIT-42B: Consulta de Kit por Gestor (/api/inventario/kit/veterinario/{id}/).
        Gestor autenticado consulta kit de Vet A con stock y veterinario inexistente #99999.
        Verifica:
        - Gestor consultando Vet A: HTTP 200 OK con estructura {'total_unidades': 12, 'items': [...]}.
        - Gestor con id=99999 inexistente: HTTP 404 Not Found, error='VETERINARIO_NO_ENCONTRADO'.
        - Vet A (Rol 3): HTTP 403 Forbidden (EsGestorInventario).
        - Jurídico (Rol 2): HTTP 403 Forbidden (EsGestorInventario).
        - Peticionario (Rol 4): HTTP 403 Forbidden (EsGestorInventario).
        - Base de datos inalterada (solo lectura).
        """
        b = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=120), estado="Agotado")
        crear_lote_kit(self.vet_a, b, cantidad=12, cantidad_ref=12, fecha_vencimiento=b.fecha_vencimiento)

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42b@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42b@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Gestor consultando Vet A existente -> 200 OK
        resp_gestor = self.client.get(
            f"/api/inventario/kit/veterinario/{self.vet_a.id_usuario}/",
            **self.headers_gestor
        )
        self.assertEqual(resp_gestor.status_code, status.HTTP_200_OK)
        self.assertIn("total_unidades", resp_gestor.data)
        self.assertIn("items", resp_gestor.data)
        self.assertEqual(resp_gestor.data["total_unidades"], 12)
        self.assertGreater(len(resp_gestor.data["items"]), 0)

        # 2. Gestor consultando veterinario inexistente -> 404 Not Found
        resp_404 = self.client.get(
            "/api/inventario/kit/veterinario/99999/",
            **self.headers_gestor
        )
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp_404.data.get("error"), "VETERINARIO_NO_ENCONTRADO")
        self.assertIn("El veterinario #99999 no existe o no está activo.", resp_404.data.get("detalle", ""))

        # 3. Roles no autorizados -> 403 Forbidden (EsGestorInventario)
        resp_vet = self.client.get(
            f"/api/inventario/kit/veterinario/{self.vet_a.id_usuario}/",
            **self.headers_vet_a
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol2 = self.client.get(
            f"/api/inventario/kit/veterinario/{self.vet_a.id_usuario}/",
            **headers_rol2
        )
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get(
            f"/api/inventario/kit/veterinario/{self.vet_a.id_usuario}/",
            **headers_rol4
        )
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

    def test_kit_42c_snapshot_movil_offline(self):
        """
        TEST-KIT-42C: Snapshot Móvil para persistencia offline (/api/inventario/kit/snapshot-movil/).
        Veterinario A con stock vigente en kit.
        Verifica:
        - Vet A: HTTP 200 OK con estructura real {'timestamp': int, 'stock': [{'id_medicamento': ..., 'nombre': ..., 'saldo_disponible': ...}]}.
        - Filtro opcional ?id_medicamento= restringe el snapshot al farmaco indicado.
        - Gestor (Rol 1): HTTP 403 Forbidden (EsVeterinario).
        - Jurídico (Rol 2): HTTP 403 Forbidden (EsVeterinario).
        - Peticionario (Rol 4): HTTP 403 Forbidden (EsVeterinario).
        - Base de datos inalterada (solo lectura).
        """
        b_a = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        b_b = crear_lote_bodega(self.med_b, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        crear_lote_kit(self.vet_a, b_a, cantidad=8, cantidad_ref=8, fecha_vencimiento=b_a.fecha_vencimiento)
        crear_lote_kit(self.vet_a, b_b, cantidad=6, cantidad_ref=6, fecha_vencimiento=b_b.fecha_vencimiento)

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42c@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42c@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Consulta autorizada como Vet A -> 200 OK con estructura real
        resp_vet = self.client.get(
            "/api/inventario/kit/snapshot-movil/",
            **self.headers_vet_a
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_200_OK)
        self.assertIn("timestamp", resp_vet.data)
        self.assertIsInstance(resp_vet.data["timestamp"], int)
        self.assertIn("stock", resp_vet.data)
        self.assertIsInstance(resp_vet.data["stock"], list)

        stock_items = resp_vet.data["stock"]
        item_a = next((s for s in stock_items if s["id_medicamento"] == self.med_a.id_medicamento), None)
        self.assertIsNotNone(item_a)
        self.assertEqual(item_a["saldo_disponible"], 8)
        self.assertEqual(item_a["nombre"], self.med_a.nombre)

        # Filtro por ?id_medicamento=
        resp_filtro = self.client.get(
            f"/api/inventario/kit/snapshot-movil/?id_medicamento={self.med_a.id_medicamento}",
            **self.headers_vet_a
        )
        self.assertEqual(resp_filtro.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_filtro.data["stock"]), 1)
        self.assertEqual(resp_filtro.data["stock"][0]["id_medicamento"], self.med_a.id_medicamento)

        # 2. Roles no autorizados -> 403 Forbidden (EsVeterinario)
        resp_gestor = self.client.get("/api/inventario/kit/snapshot-movil/", **self.headers_gestor)
        self.assertEqual(resp_gestor.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol2 = self.client.get("/api/inventario/kit/snapshot-movil/", **headers_rol2)
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get("/api/inventario/kit/snapshot-movil/", **headers_rol4)
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

    def test_kit_42d_consulta_alertas_filtros(self):
        """
        TEST-KIT-42D: Consulta y Filtros de Alertas (/api/inventario/kit/alertas/).
        Gestor y Veterinario autenticados. Kit con lote vencido y lote en stock critico (< 5 unidades).
        Verifica:
        - Gestor y Vet: HTTP 200 OK (EsGestorOVeterinario) con estructura {'agotados': [], 'criticos': [...], 'bajos': [], 'vencidos': [...], 'proximos_a_vencer': [...]}.
        - Gestor puede filtrar por ?id_veterinario={id_vet}; si no existe -> 404 VETERINARIO_NO_ENCONTRADO.
        - Jurídico (Rol 2) y Peticionario (Rol 4): HTTP 403 Forbidden.
        - Base de datos inalterada (solo lectura).
        """
        b_ven = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=5), estado="Agotado")
        crear_lote_kit(self.vet_a, b_ven, cantidad=4, cantidad_ref=10, fecha_vencimiento=b_ven.fecha_vencimiento)

        b_crit = crear_lote_bodega(self.med_b, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Agotado")
        crear_lote_kit(self.vet_a, b_crit, cantidad=3, cantidad_ref=10, fecha_vencimiento=b_crit.fecha_vencimiento)

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42d@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42d@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Vet consulta sus alertas -> 200 OK
        resp_vet = self.client.get(
            "/api/inventario/kit/alertas/",
            **self.headers_vet_a
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_200_OK)
        for k in ["agotados", "criticos", "bajos", "vencidos", "proximos_a_vencer"]:
            self.assertIn(k, resp_vet.data)

        criticos_vet = [c for c in resp_vet.data["criticos"] if c["id_medicamento"] == self.med_b.id_medicamento]
        self.assertEqual(len(criticos_vet), 1)

        vencidos_vet = [v for v in resp_vet.data["vencidos"] if v["id_medicamento"] == self.med_a.id_medicamento]
        self.assertEqual(len(vencidos_vet), 1)

        # 2. Gestor consulta alertas globales y con filtro por veterinario -> 200 OK
        resp_gestor = self.client.get(
            f"/api/inventario/kit/alertas/?id_veterinario={self.vet_a.id_usuario}",
            **self.headers_gestor
        )
        self.assertEqual(resp_gestor.status_code, status.HTTP_200_OK)
        self.assertIn("criticos", resp_gestor.data)
        self.assertIn("vencidos", resp_gestor.data)

        # Gestor con id_veterinario inexistente -> 404 VETERINARIO_NO_ENCONTRADO
        resp_gestor_404 = self.client.get(
            "/api/inventario/kit/alertas/?id_veterinario=99999",
            **self.headers_gestor
        )
        self.assertEqual(resp_gestor_404.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp_gestor_404.data.get("error"), "VETERINARIO_NO_ENCONTRADO")

        # 3. Roles no autorizados -> 403 Forbidden (EsGestorOVeterinario)
        resp_rol2 = self.client.get("/api/inventario/kit/alertas/", **headers_rol2)
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get("/api/inventario/kit/alertas/", **headers_rol4)
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

    def test_kit_42e_consulta_kardex_filtros(self):
        """
        TEST-KIT-42E: Consulta y Filtros de Kardex del Kit (/api/inventario/kit/kardex/).
        Gestor y Veterinario autenticados. Movimientos de kardex existentes en BD.
        Verifica:
        - Gestor y Vet: HTTP 200 OK (EsGestorOVeterinario) con respuesta paginada ('count', 'results').
        - Vet solo observa sus propios movimientos; Gestor puede filtrar por ?id_veterinario=.
        - Filtros de fecha (?desde=&hasta=) y tipo (?tipo_movimiento=) aplicados correctamente.
        - Jurídico (Rol 2) y Peticionario (Rol 4): HTTP 403 Forbidden.
        - Base de datos inalterada (solo lectura).
        """
        b_a = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=180))
        k1 = InventarioKardex.objects.create(
            tipo_movimiento=TIPO_KARDEX_CONSUMO_HISTORIA,
            ubicacion=UBICACION_KARDEX_KIT,
            id_usuario_ejecuta=self.vet_a,
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a,
            id_inventario=b_a,
            cantidad=3,
            saldo_anterior=10,
            saldo_resultante=7,
            motivo="Tratamiento canino",
            fecha_hora=timezone.now()
        )
        k2 = InventarioKardex.objects.create(
            tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT,
            ubicacion=UBICACION_KARDEX_GENERAL,
            id_usuario_ejecuta=self.gestor,
            id_veterinario=self.vet_b,
            id_medicamento=self.med_a,
            id_inventario=b_a,
            cantidad=5,
            saldo_anterior=50,
            saldo_resultante=45,
            motivo="Dotacion a Vet B",
            fecha_hora=timezone.now()
        )

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42e@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42e@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Vet A consulta kardex -> 200 OK paginado, solo ve k1 (su propio id_veterinario)
        resp_vet = self.client.get(
            "/api/inventario/kit/kardex/",
            **self.headers_vet_a
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_200_OK)
        self.assertIn("count", resp_vet.data)
        self.assertIn("results", resp_vet.data)
        ids_encontrados = [r["id_kardex"] for r in resp_vet.data["results"]]
        self.assertIn(k1.id_kardex, ids_encontrados)
        self.assertNotIn(k2.id_kardex, ids_encontrados)

        # 2. Gestor consulta kardex con filtros de fecha y tipo_movimiento -> 200 OK
        hoy_str = self.hoy.isoformat()
        resp_gestor = self.client.get(
            f"/api/inventario/kit/kardex/?desde={hoy_str}&hasta={hoy_str}&tipo_movimiento={TIPO_KARDEX_CONSUMO_HISTORIA}",
            **self.headers_gestor
        )
        self.assertEqual(resp_gestor.status_code, status.HTTP_200_OK)
        self.assertIn("results", resp_gestor.data)
        for r in resp_gestor.data["results"]:
            self.assertEqual(r["tipo_movimiento"], TIPO_KARDEX_CONSUMO_HISTORIA)

        # Estructura del registro retornado
        r0 = resp_gestor.data["results"][0]
        for campo in ["id_kardex", "tipo_movimiento", "ubicacion", "id_veterinario", "nombre_medicamento", "cantidad", "saldo_anterior", "saldo_resultante"]:
            self.assertIn(campo, r0)

        # 3. Roles no autorizados -> 403 Forbidden (EsGestorOVeterinario)
        resp_rol2 = self.client.get("/api/inventario/kit/kardex/", **headers_rol2)
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get("/api/inventario/kit/kardex/", **headers_rol4)
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

    def test_kit_42f_bandeja_conflictos_gestor(self):
        """
        TEST-KIT-42F: Bandeja de Conflictos Offline para Gestor (/api/inventario/kit/conflictos/).
        Consumos previos en InventarioConsumosRegistro con estado='RECHAZADO_CONFLICTO'.
        Verifica:
        - Gestor: HTTP 200 OK (EsGestorInventario) con respuesta paginada y estructura de ConflictoOutputSerializer
          ('id_consumo', 'client_uuid', 'id_veterinario', 'id_medicamento', 'stock_disponible_al_rechazo', 'motivo_rechazo', 'fecha_consumo').
        - Filtros opcionales (?resuelto=false, ?id_veterinario=) aplicados en el QuerySet.
        - Vet A (Rol 3): HTTP 403 Forbidden (EsGestorInventario).
        - Jurídico (Rol 2): HTTP 403 Forbidden (EsGestorInventario).
        - Peticionario (Rol 4): HTTP 403 Forbidden (EsGestorInventario).
        - Base de datos inalterada (solo lectura).
        """
        from inventario.constants import CONSUMO_ESTADO_RECHAZADO_CONFLICTO

        u_conf = uuid.uuid4()
        conflicto_reg = InventarioConsumosRegistro.objects.create(
            client_uuid=u_conf,
            id_veterinario=self.vet_a,
            id_medicamento=self.med_a,
            cantidad_solicitada=4,
            cantidad_descontada=0,
            estado=CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
            codigo_http=409,
            origen_tipo="DIRECTO",
            stock_disponible_al_rechazo=0,
            motivo_rechazo="Stock insuficiente en el kit de prueba"
        )

        user_rol2 = crear_usuario(rol_id=2, email="juridico_42f@cba.gov.co")
        user_rol4 = crear_usuario(rol_id=4, email="peticionario_42f@cba.gov.co")
        headers_rol2 = obtener_headers_jwt(user_rol2)
        headers_rol4 = obtener_headers_jwt(user_rol4)

        counts_pre = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )

        # 1. Gestor consulta la bandeja de conflictos -> 200 OK paginado
        resp_gestor = self.client.get(
            "/api/inventario/kit/conflictos/",
            **self.headers_gestor
        )
        self.assertEqual(resp_gestor.status_code, status.HTTP_200_OK)
        self.assertIn("count", resp_gestor.data)
        self.assertIn("results", resp_gestor.data)
        self.assertGreaterEqual(resp_gestor.data["count"], 1)

        encontrado = next((c for c in resp_gestor.data["results"] if c["client_uuid"] == str(u_conf)), None)
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado["id_consumo"], conflicto_reg.id_consumo)
        self.assertEqual(encontrado["id_veterinario"], self.vet_a.id_usuario)
        self.assertEqual(encontrado["id_medicamento"], self.med_a.id_medicamento)
        self.assertEqual(encontrado["cantidad_solicitada"], 4)
        self.assertEqual(encontrado["stock_disponible_al_rechazo"], 0)
        self.assertIn("Stock insuficiente", encontrado["motivo_rechazo"])
        self.assertIn("fecha_consumo", encontrado)

        # Filtro por resuelto=false y por id_veterinario
        resp_filtro = self.client.get(
            f"/api/inventario/kit/conflictos/?resuelto=false&id_veterinario={self.vet_a.id_usuario}",
            **self.headers_gestor
        )
        self.assertEqual(resp_filtro.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp_filtro.data["count"], 1)

        # 2. Roles no autorizados -> 403 Forbidden (EsGestorInventario)
        resp_vet = self.client.get("/api/inventario/kit/conflictos/", **self.headers_vet_a)
        self.assertEqual(resp_vet.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol2 = self.client.get("/api/inventario/kit/conflictos/", **headers_rol2)
        self.assertEqual(resp_rol2.status_code, status.HTTP_403_FORBIDDEN)

        resp_rol4 = self.client.get("/api/inventario/kit/conflictos/", **headers_rol4)
        self.assertEqual(resp_rol4.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Base de datos inalterada
        counts_post = (
            KitVeterinarioLotes.objects.count(),
            Inventarios.objects.count(),
            InventarioKardex.objects.count(),
            InventarioConsumosRegistro.objects.count(),
        )
        self.assertEqual(counts_pre, counts_post)

