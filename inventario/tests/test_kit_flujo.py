"""
Tests de integración para el flujo del Kit de Veterinario (Asignación, Retiro, Referencias, Consumo y Sincronización).
Fase C2 (Tarea 10): 17 tests (TEST-KIT-23, 24A, 24B, 33A, 33B, 33C, 33D, 33E, 34, 35, 36, 37, 38, 40, 41B, 41B-alias, 41B-sin-cantidad).

Especificación:
- plan/DISENO_TAREA10.md (§4.5, §5.3: Bloque C).
- plan/DECISIONES.md (§1, §4, §5, §7, §8, §11, §12).
"""

import unittest
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

    @unittest.expectedFailure
    def test_kit_24b_retiro_directo_endpoint_h_c2(self):
        """
        TEST-KIT-24B: Retiro directo de stock vía POST /api/inventario/kit/retiro-directo/.
        Contrato esperado: HTTP 200 OK con:
        - cantidad_total_retirada = 7
        - unidades_baja_vencimiento = 5
        - unidades_reintegradas = 2

        HALLAZGO H-C2: kit_views.py:273 accede a resultado['lotes_afectados'], clave que no existe
        en el retorno de retirar_stock_de_kit (kit_service.py:754-763 devuelve lotes_reintegrados_bodega
        y bajas_vencimiento); lotes_afectados solo existe en procesar_devolucion_aprobada.
        Se decora con @unittest.expectedFailure para documentar el hallazgo de la vista sin enmascararlo.
        """
        # Crear lotes de bodega base
        b1 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=10), estado="Agotado")
        b2 = crear_lote_bodega(self.med_a, cantidad=0, fecha_vencimiento=self.hoy - timedelta(days=5), estado="Agotado")
        b3 = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=90), estado="Disponible")

        # Asignar lotes al kit del veterinario
        crear_lote_kit(self.vet_a, b1, cantidad=2, cantidad_ref=2, fecha_vencimiento=b1.fecha_vencimiento)
        crear_lote_kit(self.vet_a, b2, cantidad=3, cantidad_ref=3, fecha_vencimiento=b2.fecha_vencimiento)
        crear_lote_kit(self.vet_a, b3, cantidad=10, cantidad_ref=10, fecha_vencimiento=b3.fecha_vencimiento)

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
