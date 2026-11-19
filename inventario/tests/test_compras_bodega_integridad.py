"""
Pruebas integrales de compras, detalles de compra, proteccion de lotes de bodega,
bloqueo de salidas legacy y concurrencia transaccional real en PostgreSQL.
"""

import threading
import uuid
from datetime import date, timedelta
from unittest import mock

from django.db import DatabaseError, connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from inventario.api.serializers import InventariosSerializer
from inventario.constants import (
    INVENTARIO_LECTURA_LEGACY_ROLES,
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_PETICIONARIO,
    ROL_VETERINARIO,
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_CONSUMO_HISTORIA,
    TIPO_KARDEX_ENTRADA_COMPRA,
    UBICACION_KARDEX_GENERAL,
)
from inventario.models import (
    Compra,
    DetalleCompra,
    DetalleSalida,
    InventarioKardex,
    Inventarios,
    KitVeterinarioLotes,
    Proveedores,
)
from inventario.services import compras_service
from inventario.services.exceptions import ConflictoOperacionInventarioError
from inventario.tests.base import VetericanoTestCase, VetericanoTransactionTestCase
from inventario.tests.factories import (
    crear_animal_con_historia,
    crear_lote_bodega,
    crear_medicamento,
    crear_usuario,
)
from inventario.tests.helpers import assert_kardex_consistente, obtener_headers_jwt, payload_expediente_clinico


class ComprasYBodegaIntegridadTests(VetericanoTestCase):
    """Pruebas unitarias/integracion de contratos de Compras, Lotes y Salidas Legacy."""

    def setUp(self):
        self.client = APIClient()
        self.admin = crear_usuario(rol_id=ROL_ADMINISTRADOR, email=f"admin_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.juridico = crear_usuario(rol_id=ROL_JURIDICO, email=f"jur_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.vet = crear_usuario(rol_id=ROL_VETERINARIO, email=f"vet_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.peticionario = crear_usuario(rol_id=ROL_PETICIONARIO, email=f"pet_{uuid.uuid4().hex[:6]}@cba.gov.co")

        self.headers_admin = obtener_headers_jwt(self.admin)
        self.headers_jur = obtener_headers_jwt(self.juridico)
        self.headers_vet = obtener_headers_jwt(self.vet)
        self.headers_pet = obtener_headers_jwt(self.peticionario)

        self.prov1 = Proveedores.objects.create(nombre="Prov Integridad 1", activo=True)
        self.prov2 = Proveedores.objects.create(nombre="Prov Integridad 2", activo=True)
        self.med1 = crear_medicamento(nombre="Meloxicam 15mg")
        self.med2 = crear_medicamento(nombre="Amoxicilina 500mg")
        self.med_inactivo = crear_medicamento(nombre="Descontinuado", activo=False)

    # ------------------------------------------------------------------
    # 1. POST /api/inventario/compras/
    # ------------------------------------------------------------------

    def test_post_compra_crea_lotes_vencimiento_y_kardex_entrada(self):
        fv_1 = (date.today() + timedelta(days=180)).isoformat()
        payload = {
            "id_proveedor": self.prov1.id_proveedor,
            "detalles": [
                {"id_medicamento": self.med1.id_medicamento, "cantidad": 20, "fecha_vencimiento": fv_1},
                {"id_medicamento": self.med2.id_medicamento, "cantidad": 12},
            ],
        }
        detalles_antes = DetalleCompra.objects.count()
        lotes_antes = Inventarios.objects.count()
        kardex_antes = InventarioKardex.objects.count()
        entradas_antes = InventarioKardex.objects.filter(tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA).count()

        resp = self.client.post("/api/inventario/compras/", payload, format="json", **self.headers_admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        # Exactamente 1 detalle, 1 lote y 1 ENTRADA_COMPRA por renglón (2 renglones), sin otros movimientos.
        self.assertEqual(DetalleCompra.objects.count(), detalles_antes + 2)
        self.assertEqual(Inventarios.objects.count(), lotes_antes + 2)
        self.assertEqual(InventarioKardex.objects.count(), kardex_antes + 2)
        self.assertEqual(
            InventarioKardex.objects.filter(tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA).count(),
            entradas_antes + 2,
        )

        compra_id = resp.data["id_compra"]
        detalles = list(DetalleCompra.objects.filter(id_compra_id=compra_id).order_by("id_detalle_compra"))
        self.assertEqual(len(detalles), 2)

        lote1 = Inventarios.objects.get(id_detalle_compra=detalles[0])
        lote2 = Inventarios.objects.get(id_detalle_compra=detalles[1])
        self.assertEqual(lote1.cantidad_actual, 20)
        self.assertEqual(lote1.fecha_vencimiento.isoformat(), fv_1)
        self.assertEqual(lote1.estado, "Disponible")
        self.assertEqual(lote2.cantidad_actual, 12)
        self.assertIsNone(lote2.fecha_vencimiento)

        k1 = InventarioKardex.objects.get(id_inventario=lote1, tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA)
        self.assertEqual(k1.ubicacion, UBICACION_KARDEX_GENERAL)
        self.assertEqual(k1.id_usuario_ejecuta_id, self.admin.id_usuario)
        self.assertIsNone(k1.id_veterinario_id)
        self.assertEqual(k1.id_medicamento_id, self.med1.id_medicamento)
        self.assertEqual(k1.cantidad, 20)
        self.assertEqual(k1.saldo_anterior, 0)
        self.assertEqual(k1.saldo_resultante, 20)

        k2 = InventarioKardex.objects.get(id_inventario=lote2, tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA)
        self.assertEqual(k2.saldo_anterior, 0)
        self.assertEqual(k2.saldo_resultante, 12)

    def test_post_compra_rechaza_payloads_invalidos_sin_dejar_residuos(self):
        fv_valida = (date.today() + timedelta(days=90)).isoformat()
        casos_invalidos = [
            {"id_proveedor": self.prov1.id_proveedor},  # sin detalles
            {"id_proveedor": self.prov1.id_proveedor, "detalles": []},  # vacio
            {"id_proveedor": self.prov1.id_proveedor, "detalles": "no-es-lista"},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": ["no-es-dict"]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": 0}]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": -4}]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": True}]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": self.med_inactivo.id_medicamento, "cantidad": 5}]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": 999999, "cantidad": 5}]},
            {"id_proveedor": self.prov1.id_proveedor, "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": 5, "fecha_vencimiento": "2026-99-99"}]},
            # Segundo renglon invalido -> rollback completo del primer renglon
            {
                "id_proveedor": self.prov1.id_proveedor,
                "detalles": [
                    {"id_medicamento": self.med1.id_medicamento, "cantidad": 10, "fecha_vencimiento": fv_valida},
                    {"id_medicamento": self.med_inactivo.id_medicamento, "cantidad": 3},
                ],
            },
        ]

        compras_antes = Compra.objects.count()
        detalles_antes = DetalleCompra.objects.count()
        lotes_antes = Inventarios.objects.count()
        kardex_antes = InventarioKardex.objects.count()

        for payload in casos_invalidos:
            with self.subTest(payload=payload):
                resp = self.client.post("/api/inventario/compras/", payload, format="json", **self.headers_admin)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(Compra.objects.count(), compras_antes)
        self.assertEqual(DetalleCompra.objects.count(), detalles_antes)
        self.assertEqual(Inventarios.objects.count(), lotes_antes)
        self.assertEqual(InventarioKardex.objects.count(), kardex_antes)

    # ------------------------------------------------------------------
    # 2. /api/inventario/detalles-compra/ y compras/{id}/
    # ------------------------------------------------------------------

    def test_post_detalle_compra_requiere_compra_y_crea_lote_y_kardex(self):
        resp_sin_compra = self.client.post(
            "/api/inventario/detalles-compra/",
            {"id_medicamento": self.med1.id_medicamento, "cantidad": 5},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_sin_compra.status_code, status.HTTP_400_BAD_REQUEST)

        resp_bool = self.client.post(
            "/api/inventario/detalles-compra/",
            {"id_compra": 1, "id_medicamento": self.med1.id_medicamento, "cantidad": True},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_bool.status_code, status.HTTP_400_BAD_REQUEST)

        compra = Compra.objects.create(id_proveedor=self.prov1)
        fv = (date.today() + timedelta(days=120)).isoformat()
        resp_ok = self.client.post(
            "/api/inventario/detalles-compra/",
            {
                "id_compra": compra.id_compra,
                "id_medicamento": self.med1.id_medicamento,
                "cantidad": 8,
                "fecha_vencimiento": fv,
            },
            format="json",
            **self.headers_jur,
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_201_CREATED)
        det = DetalleCompra.objects.get(pk=resp_ok.data["id_detalle_compra"])
        lote = Inventarios.objects.get(id_detalle_compra=det)
        self.assertEqual(lote.cantidad_actual, 8)
        self.assertEqual(lote.fecha_vencimiento.isoformat(), fv)

        kardex = InventarioKardex.objects.get(id_inventario=lote, tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA)
        self.assertEqual(kardex.id_usuario_ejecuta_id, self.juridico.id_usuario)
        self.assertEqual(kardex.saldo_resultante, 8)

    def test_compra_y_detalle_protegen_edicion_y_eliminacion_con_409(self):
        resp = self.client.post(
            "/api/inventario/compras/",
            {
                "id_proveedor": self.prov1.id_proveedor,
                "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": 15}],
            },
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        compra_id = resp.data["id_compra"]
        det_id = resp.data["detalles"][0]["id_detalle_compra"]

        # Rechazar campos simulados de anulacion
        resp_anular_fake = self.client.patch(
            f"/api/inventario/compras/{compra_id}/",
            {"estado": "ANULADA", "motivo_anulacion": "test"},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_anular_fake.status_code, status.HTTP_400_BAD_REQUEST)

        # Rechazar cambio de proveedor si ya tiene detalles/lotes
        resp_prov = self.client.patch(
            f"/api/inventario/compras/{compra_id}/",
            {"id_proveedor": self.prov2.id_proveedor},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_prov.status_code, status.HTTP_409_CONFLICT)

        # Rechazar DELETE compra con detalles
        resp_del_compra = self.client.delete(f"/api/inventario/compras/{compra_id}/", **self.headers_admin)
        self.assertEqual(resp_del_compra.status_code, status.HTTP_409_CONFLICT)

        # Rechazar PATCH/PUT/DELETE detalle-compra con lote vinculado
        resp_patch_det = self.client.patch(
            f"/api/inventario/detalles-compra/{det_id}/",
            {"cantidad": 99},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_det.status_code, status.HTTP_409_CONFLICT)

        resp_put_det = self.client.put(
            f"/api/inventario/detalles-compra/{det_id}/",
            {"id_compra": compra_id, "id_medicamento": self.med2.id_medicamento, "cantidad": 15},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_put_det.status_code, status.HTTP_409_CONFLICT)

        resp_del_det = self.client.delete(f"/api/inventario/detalles-compra/{det_id}/", **self.headers_admin)
        self.assertEqual(resp_del_det.status_code, status.HTTP_409_CONFLICT)

    # ------------------------------------------------------------------
    # 3. Proteccion de lotes /api/inventario/inventarios/{id}/
    # ------------------------------------------------------------------

    def test_inventarios_bloquea_put_campos_stock_y_delete_con_dependencias(self):
        resp_c = self.client.post(
            "/api/inventario/compras/",
            {
                "id_proveedor": self.prov1.id_proveedor,
                "detalles": [{"id_medicamento": self.med1.id_medicamento, "cantidad": 10}],
            },
            format="json",
            **self.headers_admin,
        )
        det_id = resp_c.data["detalles"][0]["id_detalle_compra"]
        lote = Inventarios.objects.get(id_detalle_compra_id=det_id)

        # PUT rechazado con 405
        resp_put = self.client.put(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"estado": "Cuarentena"},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_put.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

        # Gestor no puede cambiar campos protegidos ni desconocidos
        for bad_payload in (
            {"cantidad_actual": 5},
            {"id_detalle_compra": None},
            {"id_detalle_salida": None},
            {"id_medicamento": self.med1.id_medicamento},
            {"estado": "Inventado"},
            {},
        ):
            resp = self.client.patch(
                f"/api/inventario/inventarios/{lote.id_inventario}/",
                bad_payload,
                format="json",
                **self.headers_admin,
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, msg=f"Falló para {bad_payload}")

        # No puede marcarse Agotado mientras cantidad_actual > 0
        resp_agotado = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"estado": "Agotado"},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_agotado.status_code, status.HTTP_409_CONFLICT)

        # Antes de asignarse a kit, puede ajustar fecha_vencimiento (solo ENTRADA_COMPRA existe)
        nueva_fv = (date.today() + timedelta(days=200)).isoformat()
        resp_fv = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"fecha_vencimiento": nueva_fv},
            format="json",
            **self.headers_jur,
        )
        self.assertEqual(resp_fv.status_code, status.HTTP_200_OK)

        # Asignar parte del lote a kit -> genera ASIGNACION_KIT y KitVeterinarioLotes
        resp_asig = self.client.post(
            "/api/inventario/kit/asignar/",
            {
                "id_veterinario": self.vet.id_usuario,
                "items": [{"id_medicamento": self.med1.id_medicamento, "cantidad": 10}],
                "motivo": "Dotacion test",
            },
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_asig.status_code, status.HTTP_201_CREATED)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 0)

        # Ya tiene historial en kit -> editar fecha_vencimiento debe dar 409
        otra_fv = (date.today() + timedelta(days=300)).isoformat()
        resp_fv_post = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"fecha_vencimiento": otra_fv},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_fv_post.status_code, status.HTTP_409_CONFLICT)

        # Stock en 0 -> no puede volver a Disponible
        resp_disp = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"estado": "Disponible"},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_disp.status_code, status.HTTP_409_CONFLICT)

        # Aun con cantidad_actual == 0, DELETE debe dar 409 por Kardex / compra / kit
        resp_del = self.client.delete(f"/api/inventario/inventarios/{lote.id_inventario}/", **self.headers_admin)
        self.assertEqual(resp_del.status_code, status.HTTP_409_CONFLICT)

        # Solo lote huerfano sin stock ni referencias puede borrarse
        lote_huerfano = Inventarios.objects.create(cantidad_actual=0, estado="Agotado")
        resp_del_ok = self.client.delete(
            f"/api/inventario/inventarios/{lote_huerfano.id_inventario}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_del_ok.status_code, status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # 4. Salidas legacy bloqueadas y paridad RBAC
    # ------------------------------------------------------------------

    def test_detalles_salida_conserva_get_y_bloquea_mutaciones_con_409_o_403(self):
        lote = crear_lote_bodega(self.med1, cantidad=30)

        # GET permitido para roles gestores
        self.assertEqual(self.client.get("/api/inventario/detalles-salida/", **self.headers_admin).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get("/api/inventario/detalles-salida/", **self.headers_jur).status_code, status.HTTP_200_OK)

        # Mutaciones bloqueadas con 409 para Admin y Juridico
        for headers in (self.headers_admin, self.headers_jur):
            resp_post = self.client.post("/api/inventario/detalles-salida/", {"cantidad": 1}, format="json", **headers)
            self.assertEqual(resp_post.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(resp_post.data.get("error"), "SALIDA_LEGACY_BLOQUEADA")

            self.assertEqual(
                self.client.put("/api/inventario/detalles-salida/1/", {"cantidad": 1}, format="json", **headers).status_code,
                status.HTTP_409_CONFLICT,
            )
            self.assertEqual(
                self.client.patch("/api/inventario/detalles-salida/1/", {"cantidad": 1}, format="json", **headers).status_code,
                status.HTTP_409_CONFLICT,
            )
            self.assertEqual(
                self.client.delete("/api/inventario/detalles-salida/1/", **headers).status_code,
                status.HTTP_409_CONFLICT,
            )

        # Roles no autorizados siguen recibiendo 403
        for headers in (self.headers_vet, self.headers_pet):
            self.assertEqual(
                self.client.post("/api/inventario/detalles-salida/", {"cantidad": 1}, format="json", **headers).status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                self.client.get("/api/inventario/compras/", **headers).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 30)
        self.assertEqual(DetalleSalida.objects.count(), 0)

    # ------------------------------------------------------------------
    # 5. Alta directa de lotes bloqueada en POST /api/inventario/inventarios/
    # ------------------------------------------------------------------

    def _conteos_bodega(self):
        return {
            "compras": Compra.objects.count(),
            "detalles": DetalleCompra.objects.count(),
            "lotes": Inventarios.objects.count(),
            "kardex": InventarioKardex.objects.count(),
        }

    def _payloads_alta_directa_lote(self, lote_existente):
        return [
            # 2.º lote para un detalle de compra que ya tiene lote
            {
                "id_detalle_compra": lote_existente.id_detalle_compra_id,
                "cantidad_actual": 100,
                "estado": "Disponible",
            },
            # Lote sin detalle de compra y con stock arbitrario
            {"cantidad_actual": 9999, "estado": "Disponible"},
        ]

    def test_post_inventarios_gestores_responde_409_sin_inserciones(self):
        lote_existente = crear_lote_bodega(self.med1, cantidad=25)
        antes = self._conteos_bodega()

        for nombre_rol, headers in (("Administrador", self.headers_admin), ("Juridico", self.headers_jur)):
            for payload in self._payloads_alta_directa_lote(lote_existente):
                with self.subTest(rol=nombre_rol, payload=payload):
                    resp = self.client.post("/api/inventario/inventarios/", payload, format="json", **headers)
                    self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
                    self.assertEqual(resp.data.get("error"), "CREACION_DIRECTA_LOTE_NO_PERMITIDA")
                    self.assertIn("/api/inventario/compras/", resp.data.get("detalle", ""))
                    self.assertIn("/api/inventario/detalles-compra/", resp.data.get("detalle", ""))
                    self.assertEqual(self._conteos_bodega(), antes)

        # El detalle de compra conserva un único lote y su stock queda intacto.
        self.assertEqual(
            Inventarios.objects.filter(id_detalle_compra_id=lote_existente.id_detalle_compra_id).count(), 1
        )
        lote_existente.refresh_from_db()
        self.assertEqual(lote_existente.cantidad_actual, 25)

    def test_post_inventarios_no_gestores_403_y_sin_token_401_sin_inserciones(self):
        lote_existente = crear_lote_bodega(self.med1, cantidad=25)
        antes = self._conteos_bodega()

        for nombre_rol, headers in (("Veterinario", self.headers_vet), ("Peticionario", self.headers_pet)):
            for payload in self._payloads_alta_directa_lote(lote_existente):
                with self.subTest(rol=nombre_rol, payload=payload):
                    resp = self.client.post("/api/inventario/inventarios/", payload, format="json", **headers)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                    self.assertEqual(self._conteos_bodega(), antes)

        for payload in self._payloads_alta_directa_lote(lote_existente):
            with self.subTest(rol="sin_token", payload=payload):
                resp = APIClient().post("/api/inventario/inventarios/", payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(self._conteos_bodega(), antes)

    def test_inventarios_serializer_create_bloqueado_sin_inserciones(self):
        antes = self._conteos_bodega()
        with self.assertRaises(ConflictoOperacionInventarioError) as ctx:
            InventariosSerializer().create({"cantidad_actual": 10, "estado": "Disponible"})
        self.assertEqual(ctx.exception.codigo, "CREACION_DIRECTA_LOTE_NO_PERMITIDA")
        self.assertEqual(self._conteos_bodega(), antes)

    def test_get_inventarios_sigue_200_para_roles_de_lectura(self):
        lote = crear_lote_bodega(self.med1, cantidad=7)
        headers_por_rol = {
            ROL_ADMINISTRADOR: self.headers_admin,
            ROL_JURIDICO: self.headers_jur,
            ROL_VETERINARIO: self.headers_vet,
            ROL_PETICIONARIO: self.headers_pet,
        }
        self.assertIn(ROL_ADMINISTRADOR, INVENTARIO_LECTURA_LEGACY_ROLES)

        for rol_id, headers in headers_por_rol.items():
            esperado = status.HTTP_200_OK if rol_id in INVENTARIO_LECTURA_LEGACY_ROLES else status.HTTP_403_FORBIDDEN
            with self.subTest(rol=rol_id):
                resp_list = self.client.get("/api/inventario/inventarios/", **headers)
                self.assertEqual(resp_list.status_code, esperado)
                resp_det = self.client.get(f"/api/inventario/inventarios/{lote.id_inventario}/", **headers)
                self.assertEqual(resp_det.status_code, esperado)
                if esperado == status.HTTP_200_OK:
                    self.assertIn(lote.id_inventario, [fila["id_inventario"] for fila in resp_list.data])
                    self.assertEqual(resp_det.data["cantidad_actual"], 7)

    def test_registrar_compra_fallo_kardex_segundo_renglon_revierte_todo(self):
        antes = self._conteos_bodega()
        create_real = InventarioKardex.objects.create
        estado_en_fallo = {}

        def _create_falla_en_segunda_llamada(*args, **kwargs):
            estado_en_fallo["llamadas"] = estado_en_fallo.get("llamadas", 0) + 1
            if estado_en_fallo["llamadas"] == 2:
                estado_en_fallo["conteos"] = self._conteos_bodega()
                raise DatabaseError("Fallo simulado al insertar Kardex del 2.º renglón")
            return create_real(*args, **kwargs)

        detalles = [
            {"id_medicamento": self.med1.id_medicamento, "cantidad": 10},
            {"id_medicamento": self.med2.id_medicamento, "cantidad": 4},
        ]
        with mock.patch.object(
            InventarioKardex.objects, "create", side_effect=_create_falla_en_segunda_llamada
        ):
            with self.assertRaises(DatabaseError):
                compras_service.registrar_compra(
                    id_usuario=self.admin.id_usuario,
                    id_proveedor=self.prov1.id_proveedor,
                    detalles=detalles,
                )

        # El fallo ocurrió en el 2.º Kardex, con el 1.er renglón completo y el 2.º lote ya escritos...
        self.assertEqual(estado_en_fallo["llamadas"], 2)
        self.assertEqual(
            estado_en_fallo["conteos"],
            {
                "compras": antes["compras"] + 1,
                "detalles": antes["detalles"] + 2,
                "lotes": antes["lotes"] + 2,
                "kardex": antes["kardex"] + 1,
            },
        )
        # ...y la transacción revirtió Compra, DetalleCompra, Inventarios e InventarioKardex por completo.
        self.assertEqual(self._conteos_bodega(), antes)


class ComprasYBodegaConcurrenciaTests(VetericanoTransactionTestCase):
    """Pruebas reales multihilo sobre PostgreSQL para compras, bodega, asignacion y consumo."""

    def setUp(self):
        self.client = APIClient()
        self.admin = crear_usuario(rol_id=ROL_ADMINISTRADOR, email=f"adm_c_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.vet = crear_usuario(rol_id=ROL_VETERINARIO, email=f"vet_c_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.prov = Proveedores.objects.create(nombre="Prov Concurrencia", activo=True)
        self.med = crear_medicamento(nombre="Ketamina Conc")
        self.headers_admin = obtener_headers_jwt(self.admin)
        self.headers_vet = obtener_headers_jwt(self.vet)

    def _correr_en_paralelo(self, tareas):
        barrier = threading.Barrier(len(tareas))
        resultados = {}
        errores = {}

        def _runner(nombre, fn):
            connection.close()
            try:
                barrier.wait(timeout=10)
                resultados[nombre] = fn()
            except Exception as exc:  # pragma: no cover - diagnostico de test
                errores[nombre] = exc
            finally:
                connection.close()

        hilos = [threading.Thread(target=_runner, args=(nombre, fn)) for nombre, fn in tareas.items()]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=20)

        self.assertEqual(errores, {}, f"Excepciones inesperadas en hilos: {errores}")
        return resultados

    def test_compras_concurrentes_conservan_stock_y_kardex_entrada(self):
        def _post_compra(cant):
            client = APIClient()
            return client.post(
                "/api/inventario/compras/",
                {
                    "id_proveedor": self.prov.id_proveedor,
                    "detalles": [{"id_medicamento": self.med.id_medicamento, "cantidad": cant}],
                },
                format="json",
                **self.headers_admin,
            )

        res = self._correr_en_paralelo({
            "c1": lambda: _post_compra(12),
            "c2": lambda: _post_compra(18),
        })

        self.assertEqual(res["c1"].status_code, status.HTTP_201_CREATED)
        self.assertEqual(res["c2"].status_code, status.HTTP_201_CREATED)

        lotes = list(Inventarios.objects.filter(id_detalle_compra__id_medicamento=self.med).order_by("id_inventario"))
        self.assertEqual(len(lotes), 2)
        self.assertEqual(sum(l.cantidad_actual for l in lotes), 30)

        entradas = list(InventarioKardex.objects.filter(tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA, id_medicamento=self.med))
        self.assertEqual(len(entradas), 2)
        self.assertEqual(sum(k.cantidad for k in entradas), 30)

    def test_compra_vs_asignacion_vs_consumo_y_patch_estado_concurrentes(self):
        # Lote inicial comprado formalmente
        resp_init = self.client.post(
            "/api/inventario/compras/",
            {
                "id_proveedor": self.prov.id_proveedor,
                "detalles": [{"id_medicamento": self.med.id_medicamento, "cantidad": 20}],
            },
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_init.status_code, status.HTTP_201_CREATED)
        det_init = resp_init.data["detalles"][0]["id_detalle_compra"]
        lote_init = Inventarios.objects.get(id_detalle_compra_id=det_init)

        # Dotar 5 al kit para probar asignacion vs consumo vs retiro + compra + patch metadata
        self.assertEqual(
            self.client.post(
                "/api/inventario/kit/asignar/",
                {
                    "id_veterinario": self.vet.id_usuario,
                    "items": [{"id_medicamento": self.med.id_medicamento, "cantidad": 5}],
                    "motivo": "Base concurrencia",
                },
                format="json",
                **self.headers_admin,
            ).status_code,
            status.HTTP_201_CREATED,
        )

        animal, _ = crear_animal_con_historia()
        client_uuid = str(uuid.uuid4())

        def _nueva_compra():
            return APIClient().post(
                "/api/inventario/compras/",
                {
                    "id_proveedor": self.prov.id_proveedor,
                    "detalles": [{"id_medicamento": self.med.id_medicamento, "cantidad": 10}],
                },
                format="json",
                **self.headers_admin,
            )

        def _asignar():
            return APIClient().post(
                "/api/inventario/kit/asignar/",
                {
                    "id_veterinario": self.vet.id_usuario,
                    "items": [{"id_medicamento": self.med.id_medicamento, "cantidad": 4}],
                    "motivo": "Asignacion paralela",
                },
                format="json",
                **self.headers_admin,
            )

        def _consumir():
            payload = payload_expediente_clinico(
                id_animal=animal.id_animal,
                consumos=[{
                    "id_medicamento": self.med.id_medicamento,
                    "unidades": 2,
                    "client_uuid": client_uuid,
                }],
                offline=False,
            )
            return APIClient().post(
                "/api/clinica/expediente-completo/",
                payload,
                format="json",
                **self.headers_vet,
            )

        def _patch_estado():
            return APIClient().patch(
                f"/api/inventario/inventarios/{lote_init.id_inventario}/",
                {"estado": "Cuarentena"},
                format="json",
                **self.headers_admin,
            )

        res = self._correr_en_paralelo({
            "compra": _nueva_compra,
            "asignar": _asignar,
            "consumir": _consumir,
            "patch_estado": _patch_estado,
        })

        self.assertEqual(res["compra"].status_code, status.HTTP_201_CREATED)
        self.assertIn(res["asignar"].status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST))
        self.assertEqual(res["consumir"].status_code, status.HTTP_201_CREATED)
        self.assertEqual(res["patch_estado"].status_code, status.HTTP_200_OK)

        # Invariante global de stock: comprado(30) - consumido(2) == stock_bodega + stock_kit
        stock_bodega = sum(
            l.cantidad_actual for l in Inventarios.objects.filter(id_detalle_compra__id_medicamento=self.med)
        )
        stock_kit = sum(
            k.cantidad_actual for k in KitVeterinarioLotes.objects.filter(id_veterinario=self.vet, id_medicamento=self.med)
        )
        self.assertEqual(stock_bodega + stock_kit, 28)

        # Kardex consistente para el consumo clinico y cada lote fisico en bodega
        assert_kardex_consistente(self, client_uuid, TIPO_KARDEX_CONSUMO_HISTORIA, cantidad_esperada=2)
        for lote in Inventarios.objects.filter(id_detalle_compra__id_medicamento=self.med):
            movs = list(
                InventarioKardex.objects.filter(
                    id_inventario=lote,
                    ubicacion=UBICACION_KARDEX_GENERAL,
                ).order_by("id_kardex")
            )
            self.assertGreaterEqual(len(movs), 1)
            self.assertEqual(movs[0].tipo_movimiento, TIPO_KARDEX_ENTRADA_COMPRA)
            self.assertEqual(movs[-1].saldo_resultante, lote.cantidad_actual)
