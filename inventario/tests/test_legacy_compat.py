"""
Tests de compatibilidad hacia atras para viewsets heredados de inventario (Fase 11B).
Fija el comportamiento de los endpoints tradicionales previo al endurecimiento RBAC (Fase 11C).

Endpoints cubiertos:
- /api/inventario/proveedores/
- /api/inventario/compras/ y /api/inventario/compras/estadisticas/
- /api/inventario/detalles-compra/
- /api/inventario/salidas/
- /api/inventario/detalles-salida/
- /api/inventario/inventarios/

Referencias cruzadas con el frontend Angular (adminVetericano):
- adminVetericano/src/app/proveedores/proveedores.ts (lineas 80, 123, 136, 167)
- adminVetericano/src/app/services/compras.service.ts (lineas 98, 109, 127-181, 181-208, 330-340, 396-421, 458-508, 720-727)
- adminVetericano/src/app/inventario/inventario.ts (lineas 434-453)
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from inventario.models import (
    Proveedores,
    Compra,
    DetalleCompra,
    Inventarios,
    Salidas,
    DetalleSalida,
    InventarioKardex,
)
from medicamentos.models import Medicamentos, AdministracionMedicamento
from clinica.models import (
    Consulta,
    HospitalizacionSeresSintientes,
    SeguimientoHospitalario,
)
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_consulta,
)
from inventario.tests.helpers import obtener_headers_jwt


class LegacyCompatAdminTests(TestCase):
    """
    Suite de compatibilidad para el Administrador (rol 1).
    Valida que los flujos historicos de administracion funcionen sin alteraciones.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = crear_usuario(rol_id=1, email="admin_legacy_compat@cba.gov.co")
        self.headers_admin = obtener_headers_jwt(self.admin)
        self.med = crear_medicamento(nombre="Amoxicilina 500mg Admin")
        self.prov = Proveedores.objects.create(
            nombre="Proveedor Inicial CBA",
            telefono="3001234567",
            email="prov_inicial@cba.gov.co",
            direccion="Calle 100 # 20-30",
            activo=True,
        )

    def test_l01_get_lista_seis_recursos_y_estadisticas_compras(self):
        """
        L-01: GET 200 en los seis recursos antiguos de inventario (lista) y GET /compras/estadisticas/ 200.
        Verifica respuesta 200 y presencia de claves 'facturas_registradas', 'medicamentos_sistema',
        'unidades_ingresadas' en estadisticas de compras.
        Payload: Ninguno (GET).
        Referencias web:
        - adminVetericano/src/app/proveedores/proveedores.ts:80 (GET /api/inventario/proveedores/)
        - adminVetericano/src/app/services/compras.service.ts:109 (GET /api/inventario/compras/)
        - adminVetericano/src/app/services/compras.service.ts:140 (GET /api/inventario/detalles-compra/)
        - adminVetericano/src/app/services/compras.service.ts:173 (GET /api/inventario/inventarios/)
        - inventario/api/views.py:49-58 (GET /api/inventario/salidas/, GET /api/inventario/detalles-salida/)
        - adminVetericano/src/app/services/compras.service.ts:98 (GET /api/inventario/compras/estadisticas/)
        """
        recursos = [
            "/api/inventario/proveedores/",
            "/api/inventario/compras/",
            "/api/inventario/detalles-compra/",
            "/api/inventario/salidas/",
            "/api/inventario/detalles-salida/",
            "/api/inventario/inventarios/",
        ]
        for url in recursos:
            with self.subTest(endpoint=url):
                resp = self.client.get(url, **self.headers_admin)
                self.assertEqual(resp.status_code, status.HTTP_200_OK, f"GET {url} retorno {resp.status_code}")

        # Estadisticas compras
        resp_est = self.client.get("/api/inventario/compras/estadisticas/", **self.headers_admin)
        self.assertEqual(resp_est.status_code, status.HTTP_200_OK)
        self.assertIn("facturas_registradas", resp_est.data)
        self.assertIn("medicamentos_sistema", resp_est.data)
        self.assertIn("unidades_ingresadas", resp_est.data)

    def test_l02_proveedores_post_put_patch(self):
        """
        L-02: proveedores: POST 201, PUT completo 200, PATCH activo:false 200.
        Payloads:
        - POST: {"nombre": "Distribuidora Farmavet", "telefono": "3001234567", "email": "farmavet@example.com", "direccion": "Calle 10 # 20-30", "activo": True}
        - PUT: {"nombre": "Distribuidora Farmavet SAS", "telefono": "3007654321", "email": "contacto@farmavet.com", "direccion": "Carrera 15 # 45-60", "activo": True}
        - PATCH: {"activo": False}
        Referencias web:
        - adminVetericano/src/app/proveedores/proveedores.ts:136 (POST)
        - adminVetericano/src/app/proveedores/proveedores.ts:123 (PUT)
        - adminVetericano/src/app/proveedores/proveedores.ts:167 (PATCH)
        """
        payload_post = {
            "nombre": "Distribuidora Farmavet",
            "telefono": "3001234567",
            "email": "farmavet@example.com",
            "direccion": "Calle 10 # 20-30",
            "activo": True,
        }
        resp_post = self.client.post("/api/inventario/proveedores/", payload_post, format="json", **self.headers_admin)
        self.assertEqual(resp_post.status_code, status.HTTP_201_CREATED)
        prov_id = resp_post.data["id_proveedor"]
        self.assertTrue(Proveedores.objects.filter(pk=prov_id, nombre="Distribuidora Farmavet", activo=True).exists())

        payload_put = {
            "nombre": "Distribuidora Farmavet SAS",
            "telefono": "3007654321",
            "email": "contacto@farmavet.com",
            "direccion": "Carrera 15 # 45-60",
            "activo": True,
        }
        resp_put = self.client.put(f"/api/inventario/proveedores/{prov_id}/", payload_put, format="json", **self.headers_admin)
        self.assertEqual(resp_put.status_code, status.HTTP_200_OK)
        prov_obj = Proveedores.objects.get(pk=prov_id)
        self.assertEqual(prov_obj.nombre, "Distribuidora Farmavet SAS")
        self.assertEqual(prov_obj.telefono, "3007654321")

        payload_patch = {"activo": False}
        resp_patch = self.client.patch(f"/api/inventario/proveedores/{prov_id}/", payload_patch, format="json", **self.headers_admin)
        self.assertEqual(resp_patch.status_code, status.HTTP_200_OK)
        prov_obj.refresh_from_db()
        self.assertFalse(prov_obj.activo)

    def test_l03_compras_post_con_detalles_crea_lote_bodega(self):
        """
        L-03: POST /compras/ con id_proveedor y array de detalles -> 201.
        Verifica en BD que se crearon Compra, DetalleCompra y lote en Inventarios
        con cantidad_actual igual a cantidad y estado 'Disponible'.
        Verifica que la respuesta contiene 'id_compra' y 'detalles'.
        Payload: {"id_proveedor": <id>, "detalles": [{"id_medicamento": <id_med>, "cantidad": 50}]}
        Referencias web:
        - adminVetericano/src/app/services/compras.service.ts:127-181
        - adminVetericano/src/app/services/compras.service.ts:330-340
        """
        payload = {
            "id_proveedor": self.prov.id_proveedor,
            "detalles": [
                {
                    "id_medicamento": self.med.id_medicamento,
                    "cantidad": 50,
                }
            ],
        }
        resp = self.client.post("/api/inventario/compras/", payload, format="json", **self.headers_admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("id_compra", resp.data)
        self.assertIn("detalles", resp.data)

        id_compra = resp.data["id_compra"]
        self.assertTrue(Compra.objects.filter(pk=id_compra).exists())

        detalles_bd = DetalleCompra.objects.filter(id_compra=id_compra)
        self.assertEqual(detalles_bd.count(), 1)
        det = detalles_bd.first()
        self.assertEqual(det.id_medicamento_id, self.med.id_medicamento)
        self.assertEqual(det.cantidad, 50)

        lote = Inventarios.objects.filter(id_detalle_compra=det).first()
        self.assertIsNotNone(lote)
        self.assertEqual(lote.cantidad_actual, 50)
        self.assertEqual(lote.estado, "Disponible")

    def test_l04_compras_y_detalles_flujo_crud_y_desvinculacion(self):
        """
        L-04: PATCH /compras/{id}/ {id_proveedor} 200;
        POST /detalles-compra/ {id_compra, id_medicamento, cantidad, precio_unitario} 201;
        PATCH /detalles-compra/{id}/ 200;
        DELETE /detalles-compra/{id}/ 204 (detalle sin lote vinculado);
        DELETE /compras/{id}/ 204 (flujo web con desvinculacion previa de lotes en inventarios).
        Payloads:
        - PATCH /compras/{id}/: {"id_proveedor": <id_prov2>}
        - POST /detalles-compra/: {"id_compra": <id_compra>, "id_medicamento": <id_med>, "cantidad": 25, "precio_unitario": 15000}
        - PATCH /detalles-compra/{id}/: {"cantidad": 30}
        - PATCH /inventarios/{id}/: {"id_detalle_compra": null}
        Referencias web:
        - adminVetericano/src/app/services/compras.service.ts:130-169
        - adminVetericano/src/app/services/compras.service.ts:181-208
        - adminVetericano/src/app/services/compras.service.ts:396-421
        """
        # 1. Compra base para actualizar proveedor
        prov2 = Proveedores.objects.create(nombre="Proveedor 2 CBA", activo=True)
        compra = Compra.objects.create(id_proveedor=self.prov)

        # PATCH /compras/{id}/
        resp_patch_compra = self.client.patch(
            f"/api/inventario/compras/{compra.id_compra}/",
            {"id_proveedor": prov2.id_proveedor},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_compra.status_code, status.HTTP_200_OK)
        compra.refresh_from_db()
        self.assertEqual(compra.id_proveedor_id, prov2.id_proveedor)

        # POST /detalles-compra/
        # Nota: precio_unitario es enviado por la web compras.service.ts:150 pero el modelo DetalleCompra no tiene ese campo
        payload_detalle = {
            "id_compra": compra.id_compra,
            "id_medicamento": self.med.id_medicamento,
            "cantidad": 25,
            "precio_unitario": 15000,
        }
        resp_post_det = self.client.post(
            "/api/inventario/detalles-compra/",
            payload_detalle,
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_post_det.status_code, status.HTTP_201_CREATED)
        id_det = resp_post_det.data["id_detalle_compra"]
        det_obj = DetalleCompra.objects.get(pk=id_det)
        self.assertEqual(det_obj.cantidad, 25)

        # PATCH /detalles-compra/{id}/
        resp_patch_det = self.client.patch(
            f"/api/inventario/detalles-compra/{id_det}/",
            {"cantidad": 30},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_det.status_code, status.HTTP_200_OK)
        det_obj.refresh_from_db()
        self.assertEqual(det_obj.cantidad, 30)

        # DELETE /detalles-compra/{id}/ (en detalle sin lote vinculado)
        resp_del_det = self.client.delete(
            f"/api/inventario/detalles-compra/{id_det}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_del_det.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DetalleCompra.objects.filter(pk=id_det).exists())

        # DELETE /compras/{id}/ (en compra sin detalles)
        resp_del_compra = self.client.delete(
            f"/api/inventario/compras/{compra.id_compra}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_del_compra.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Compra.objects.filter(pk=compra.id_compra).exists())

        # Flujo replicado de compras.service.ts:181-208 con desvinculacion de lotes
        compra_web = Compra.objects.create(id_proveedor=self.prov)
        det_web = DetalleCompra.objects.create(
            id_compra=compra_web,
            id_medicamento=self.med,
            cantidad=10,
        )
        lote_web = Inventarios.objects.create(
            id_detalle_compra=det_web,
            cantidad_actual=10,
            estado="Disponible",
        )

        # Desvincular lote (PATCH inventarios id_detalle_compra: null)
        resp_unlink = self.client.patch(
            f"/api/inventario/inventarios/{lote_web.id_inventario}/",
            {"id_detalle_compra": None},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_unlink.status_code, status.HTTP_200_OK)
        lote_web.refresh_from_db()
        self.assertIsNone(lote_web.id_detalle_compra)

        # Eliminar detalle de compra
        resp_del_det_web = self.client.delete(
            f"/api/inventario/detalles-compra/{det_web.id_detalle_compra}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_del_det_web.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DetalleCompra.objects.filter(pk=det_web.id_detalle_compra).exists())

        # Eliminar compra
        resp_del_compra_web = self.client.delete(
            f"/api/inventario/compras/{compra_web.id_compra}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_del_compra_web.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Compra.objects.filter(pk=compra_web.id_compra).exists())

    def test_l05_inventarios_post_patch_desvincular_y_ajustes_stock(self):
        """
        L-05: inventarios: POST {id_detalle_compra, cantidad_actual, estado:'Disponible'} 201;
        PATCH {id_detalle_compra: null} 200;
        PATCH {cantidad_actual: MAYOR} 200 (aumenta);
        PATCH {cantidad_actual: menor, estado} 200;
        verifica BD tras cada uno.
        Payloads:
        - POST: {"id_detalle_compra": <id_det>, "cantidad_actual": 100, "estado": "Disponible"}
        - PATCH desvincular: {"id_detalle_compra": None}
        - PATCH aumentar: {"cantidad_actual": 150}
        - PATCH reducir: {"cantidad_actual": 20, "estado": "Disponible"}
        Referencias web:
        - adminVetericano/src/app/services/compras.service.ts:181-186
        - adminVetericano/src/app/services/compras.service.ts:458-508
        """
        compra = Compra.objects.create(id_proveedor=self.prov)
        detalle = DetalleCompra.objects.create(
            id_compra=compra,
            id_medicamento=self.med,
            cantidad=100,
        )

        # POST /inventarios/
        payload_post = {
            "id_detalle_compra": detalle.id_detalle_compra,
            "cantidad_actual": 100,
            "estado": "Disponible",
        }
        resp_post = self.client.post("/api/inventario/inventarios/", payload_post, format="json", **self.headers_admin)
        self.assertEqual(resp_post.status_code, status.HTTP_201_CREATED)
        id_inv = resp_post.data["id_inventario"]
        lote = Inventarios.objects.get(pk=id_inv)
        self.assertEqual(lote.id_detalle_compra_id, detalle.id_detalle_compra)
        self.assertEqual(lote.cantidad_actual, 100)
        self.assertEqual(lote.estado, "Disponible")

        # PATCH desvincular id_detalle_compra: null
        resp_patch_null = self.client.patch(
            f"/api/inventario/inventarios/{id_inv}/",
            {"id_detalle_compra": None},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_null.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertIsNone(lote.id_detalle_compra)

        # PATCH cantidad_actual mayor
        resp_patch_mayor = self.client.patch(
            f"/api/inventario/inventarios/{id_inv}/",
            {"cantidad_actual": 150},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_mayor.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 150)

        # PATCH cantidad_actual menor y estado
        resp_patch_menor = self.client.patch(
            f"/api/inventario/inventarios/{id_inv}/",
            {"cantidad_actual": 20, "estado": "Disponible"},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_patch_menor.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 20)
        self.assertEqual(lote.estado, "Disponible")


class LegacyCompatVeterinarioTests(TestCase):
    """
    Suite de compatibilidad para Veterinario (rol 3) y Juridico (rol 2).
    Verifica que las lecturas y actas web de rebaja de inventario sigan operativas.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.vet = crear_usuario(rol_id=3, email="vet_legacy_compat@cba.gov.co")
        self.juridico = crear_usuario(rol_id=2, email="juridico_legacy_compat@cba.gov.co")
        self.headers_vet = obtener_headers_jwt(self.vet)
        self.headers_juridico = obtener_headers_jwt(self.juridico)
        self.roles_test = [
            ("Veterinario", 3, self.headers_vet),
            ("Juridico", 2, self.headers_juridico),
        ]
        self.med = crear_medicamento(nombre="Meloxicam 20mg Test")

    def test_l06_get_recursos_inventario_veterinario_y_juridico(self):
        """
        L-06: GET en inventarios, detalles-compra, salidas, detalles-salida:
        - Rol 2 (Jurídico): HTTP 200 OK (gestor de inventario en INVENTARIO_LECTURA_LEGACY_ROLES=[1, 2]).
        - Rol 3 (Veterinario): HTTP 403 Forbidden (bloqueado de bodega general tras separación de roles).
        """
        recursos = [
            "/api/inventario/inventarios/",
            "/api/inventario/detalles-compra/",
            "/api/inventario/salidas/",
            "/api/inventario/detalles-salida/",
        ]
        esperado_por_rol = {
            2: status.HTTP_200_OK,
            3: status.HTTP_403_FORBIDDEN,
        }
        for rol_nombre, rol_id, headers in self.roles_test:
            codigo_esperado = esperado_por_rol[rol_id]
            for url in recursos:
                with self.subTest(rol=rol_nombre, endpoint=url):
                    resp = self.client.get(url, **headers)
                    self.assertEqual(
                        resp.status_code,
                        codigo_esperado,
                        f"Rol {rol_nombre} recibio {resp.status_code} en {url}",
                    )

    def test_l07_patch_inventario_acta_web_veterinario_y_juridico(self):
        """
        L-07: PATCH /inventarios/{id}/ con payload del acta web:
        - Rol 2 (Jurídico): HTTP 200 OK y BD actualizada (disminuir a 30 Disponible y agotar a 0 Agotado).
        - Rol 3 (Veterinario): HTTP 403 Forbidden y BD inalterada (solo consume su kit personal).
        """
        for rol_nombre, rol_id, headers in self.roles_test:
            with self.subTest(rol=rol_nombre):
                lote_test = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")

                # Disminucion ordinaria
                payload_menor = {"cantidad_actual": 30, "estado": "Disponible"}
                resp_menor = self.client.patch(
                    f"/api/inventario/inventarios/{lote_test.id_inventario}/",
                    payload_menor,
                    format="json",
                    **headers,
                )
                if rol_id == 3:
                    self.assertEqual(resp_menor.status_code, status.HTTP_403_FORBIDDEN)
                    lote_test.refresh_from_db()
                    self.assertEqual(lote_test.cantidad_actual, 50)
                    self.assertEqual(lote_test.estado, "Disponible")
                    continue

                self.assertEqual(resp_menor.status_code, status.HTTP_200_OK)
                lote_test.refresh_from_db()
                self.assertEqual(lote_test.cantidad_actual, 30)
                self.assertEqual(lote_test.estado, "Disponible")

                # Agotar stock a 0
                payload_agotado = {"cantidad_actual": 0, "estado": "Agotado"}
                resp_agotado = self.client.patch(
                    f"/api/inventario/inventarios/{lote_test.id_inventario}/",
                    payload_agotado,
                    format="json",
                    **headers,
                )
                self.assertEqual(resp_agotado.status_code, status.HTTP_200_OK)
                lote_test.refresh_from_db()
                self.assertEqual(lote_test.cantidad_actual, 0)
                self.assertEqual(lote_test.estado, "Agotado")

    def test_l08_sin_token_get_inventarios_401(self):
        """
        L-08: sin token: GET /inventarios/ responde 401 Unauthorized.
        Payload: Ninguno (GET sin header Authorization).
        Referencias backend:
        - inventario/api/views.py:77 (permission_classes = [IsAuthenticated])
        """
        resp = self.client.get("/api/inventario/inventarios/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


def _extraer_codigos_error(data):
    """Auxiliar para extraer codigos de error de ValidationError de DRF."""
    codigos = []
    if isinstance(data, list):
        for item in data:
            if hasattr(item, 'code') and item.code:
                codigos.append(item.code)
    elif isinstance(data, dict):
        for val in data.values():
            if isinstance(val, list):
                for item in val:
                    if hasattr(item, 'code') and item.code:
                        codigos.append(item.code)
            elif hasattr(val, 'code') and val.code:
                codigos.append(val.code)
    return codigos


class LegacyEndurecidoTests(TestCase):
    """Comportamiento tras el endurecimiento 11C (inversion de la caracterizacion H-01..H-09)"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = crear_usuario(rol_id=1, email="admin_endurecido@cba.gov.co")
        self.juridico = crear_usuario(rol_id=2, email="juridico_endurecido@cba.gov.co")
        self.vet = crear_usuario(rol_id=3, email="vet_endurecido@cba.gov.co")
        self.peticionario = crear_usuario(rol_id=4, email="pet_endurecido@cba.gov.co")
        self.headers_admin = obtener_headers_jwt(self.admin)
        self.headers_juridico = obtener_headers_jwt(self.juridico)
        self.headers_vet = obtener_headers_jwt(self.vet)
        self.headers_pet = obtener_headers_jwt(self.peticionario)
        self.med = crear_medicamento(nombre="Ketamina 100mg Test")
        self.prov = Proveedores.objects.create(nombre="Proveedor Endurecido CBA", activo=True)

    def test_h01_veterinario_post_proveedor_bloqueado_403(self):
        """
        H-01: veterinario (rol 3) POST /proveedores/ -> 403 Forbidden (valor anterior: 201 Created).
        Payload: {"nombre": "Proveedor Vet H01", "telefono": "3001112233", "email": "vet_h01@prov.com", "direccion": "Calle 1 # 2-3", "activo": True}
        Por que existe: Confirma que la creacion de proveedores queda restringida a gestores (rol 1) y el veterinario no puede crear entidades comerciales.
        """
        payload = {
            "nombre": "Proveedor Vet H01",
            "telefono": "3001112233",
            "email": "vet_h01@prov.com",
            "direccion": "Calle 1 # 2-3",
            "activo": True,
        }
        resp = self.client.post("/api/inventario/proveedores/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Proveedores.objects.filter(nombre="Proveedor Vet H01").exists())

    def test_h02_veterinario_delete_proveedor_bloqueado_403(self):
        """
        H-02: veterinario DELETE /proveedores/{id}/ -> 403 Forbidden (valor anterior: 204 No Content).
        Payload: Ninguno (DELETE).
        Por que existe: Impide que roles clinicos eliminen proveedores institucionales; la destruccion sigue reservada al gestor.
        """
        prov_temp = Proveedores.objects.create(nombre="Proveedor a Borrar H02", activo=True)
        resp = self.client.delete(f"/api/inventario/proveedores/{prov_temp.id_proveedor}/", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Proveedores.objects.filter(pk=prov_temp.id_proveedor).exists())

    def test_h03_veterinario_post_compras_bloqueado_403(self):
        """
        H-03: veterinario POST /compras/ con detalles -> 403 Forbidden (valor anterior: 201 Created).
        Payload: {"id_proveedor": <id>, "detalles": [{"id_medicamento": <id_med>, "cantidad": 40}]}
        Por que existe: La recepcion de facturas y dotacion de bodega es funcion exclusiva del gestor; no debe crearse Compra, DetalleCompra ni lote.
        """
        compras_antes = Compra.objects.count()
        detalles_antes = DetalleCompra.objects.count()
        lotes_antes = Inventarios.objects.count()

        payload = {
            "id_proveedor": self.prov.id_proveedor,
            "detalles": [
                {
                    "id_medicamento": self.med.id_medicamento,
                    "cantidad": 40,
                }
            ],
        }
        resp = self.client.post("/api/inventario/compras/", payload, format="json", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Compra.objects.count(), compras_antes)
        self.assertEqual(DetalleCompra.objects.count(), detalles_antes)
        self.assertEqual(Inventarios.objects.count(), lotes_antes)

    def test_h04_veterinario_patch_inventario_inflar_stock_rechazado_400(self):
        """
        H-04: veterinario (rol 3) PATCH /inventarios/{id}/ -> 403 Forbidden (bloqueado en capa de permisos).
        Tras la separación de roles (INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES=[1, 2]), el veterinario no puede
        alterar ni consultar lotes de bodega general; sus consumos operan únicamente sobre su kit.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")
        resp = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 100},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 50)

        # Incluso enviando el mismo valor, el rol Veterinario permanece bloqueado con 403
        resp_igual = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 50},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_igual.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 50)

    def test_h05_veterinario_delete_inventario_bloqueado_403(self):
        """
        H-05: veterinario DELETE /inventarios/{id}/ -> 403 Forbidden (valor anterior: 204 No Content).
        Payload: Ninguno (DELETE).
        Por que existe: Eliminar lotes fisicos de bodega es potestad administrativa exclusiva del gestor; el lote debe seguir existiendo en BD.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=10, estado="Disponible")
        resp = self.client.delete(f"/api/inventario/inventarios/{lote.id_inventario}/", **self.headers_vet)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Inventarios.objects.filter(pk=lote.id_inventario).exists())

    def test_h06_peticionario_post_proveedor_bloqueado_403(self):
        """
        H-06: peticionario (rol 4) POST /proveedores/ -> 403 Forbidden (valor anterior: 201 Created).
        Payload: {"nombre": "Proveedor Peticionario H06", "telefono": "3004445566", "email": "pet_h06@prov.com", "direccion": "Carrera 4 # 5-6", "activo": True}
        Por que existe: Un usuario ciudadano/peticionario no debe tener acceso de escritura comercial.
        """
        payload = {
            "nombre": "Proveedor Peticionario H06",
            "telefono": "3004445566",
            "email": "pet_h06@prov.com",
            "direccion": "Carrera 4 # 5-6",
            "activo": True,
        }
        resp = self.client.post("/api/inventario/proveedores/", payload, format="json", **self.headers_pet)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Proveedores.objects.filter(nombre="Proveedor Peticionario H06").exists())

    def test_h07_peticionario_patch_inventario_bloqueado_403(self):
        """
        H-07: peticionario (rol 4) PATCH /inventarios/{id}/ -> 403 Forbidden (valor anterior: 200 OK).
        Payload: {"cantidad_actual": 15}
        Por que existe: Los peticionarios no tienen rol clinico ni administrativo; no pueden alterar existencias de bodega.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")
        resp = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 15},
            format="json",
            **self.headers_pet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 50)

    def test_h08_veterinario_patch_inventario_desvincular_rechazado_400(self):
        """
        H-08: veterinario (rol 3) PATCH /inventarios/{id}/ {id_detalle_compra: null} -> 403 Forbidden.
        Tras la separación de roles (INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES=[1, 2]), el veterinario queda
        bloqueado por PermisoInventariosLegacy antes de alcanzar el serializer; BD intacta.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=20, estado="Disponible")
        id_detalle_orig = lote.id_detalle_compra_id
        self.assertIsNotNone(id_detalle_orig)

        # Intento de desvincular solo id_detalle_compra
        resp = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"id_detalle_compra": None},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.id_detalle_compra_id, id_detalle_orig)

        # Caso mixto {cantidad_actual: menor, id_detalle_compra: null}
        resp_mixto = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 10, "id_detalle_compra": None},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_mixto.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 20)
        self.assertEqual(lote.id_detalle_compra_id, id_detalle_orig)

    def test_h09_veterinario_post_detalles_salida_bloqueado_403(self):
        """
        H-09: veterinario POST /detalles-salida/ valido -> 403 Forbidden (valor anterior: 201 Created sin kardex).
        Payload: {"id_usuario": <vet.id_usuario>, "id_administracion": <admin_med.id_administracion>, "cantidad": 5}
        Por que existe: Cierra el hueco arquitectonico legacy donde salidas directas descontaban bodega sin auditoria de kardex.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=20, estado="Disponible")
        consulta = crear_consulta()
        hosp = HospitalizacionSeresSintientes.objects.create(
            id_consulta=consulta,
            responsable_clinico="Dr. Veterinario Test",
        )
        seg = SeguimientoHospitalario.objects.create(
            id_hospitalizacion=hosp,
            evolucion="Paciente estable con tratamiento",
        )
        admin_med = AdministracionMedicamento.objects.create(
            id_medicamento=self.med,
            id_seguimiento_hosp=seg,
            dosis_administrada="5ml",
        )

        kardex_conteo_inicial = InventarioKardex.objects.count()

        payload = {
            "id_usuario": self.vet.id_usuario,
            "id_administracion": admin_med.id_administracion,
            "cantidad": 5,
        }
        resp = self.client.post(
            "/api/inventario/detalles-salida/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Lote conserva cantidad original
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 20)

        # Conteo de kardex sin cambio
        kardex_conteo_final = InventarioKardex.objects.count()
        self.assertEqual(kardex_conteo_final, kardex_conteo_inicial)

    def test_n01_peticionario_get_seis_recursos_y_estadisticas_403(self):
        """
        N-01: peticionario (rol 4) GET en los seis recursos y en compras/estadisticas -> 403 Forbidden.
        Payload: Ninguno (GET).
        Por que existe: Previene divulgacion no autorizada de stock, proveedores y compras a ciudadanos/peticionarios sin perfil de staff.
        """
        urls = [
            "/api/inventario/proveedores/",
            "/api/inventario/compras/",
            "/api/inventario/detalles-compra/",
            "/api/inventario/salidas/",
            "/api/inventario/detalles-salida/",
            "/api/inventario/inventarios/",
            "/api/inventario/compras/estadisticas/",
        ]
        for url in urls:
            with self.subTest(endpoint=url):
                resp = self.client.get(url, **self.headers_pet)
                self.assertEqual(
                    resp.status_code,
                    status.HTTP_403_FORBIDDEN,
                    f"Peticionario debio recibir 403 en {url}, obtuvo {resp.status_code}",
                )

    def test_n02_veterinario_y_juridico_mutaciones_salidas_y_detalles_compra_403(self):
        """
        N-02: control de mutaciones sobre salidas y detalles-compra (Opción A, INVENTARIO_GESTOR_ROLES=[1, 2]):
        - Veterinario (rol 3) y Peticionario (rol 4): POST/PATCH/DELETE -> 403 Forbidden.
        - Jurídico (rol 2, gestor de inventario): POST (201), PATCH (200), DELETE (204) permitidos.
        """
        compra = Compra.objects.create(id_proveedor=self.prov)
        det_compra = DetalleCompra.objects.create(
            id_compra=compra,
            id_medicamento=self.med,
            cantidad=10,
        )
        salida = Salidas.objects.create(motivo="Salida inicial base")

        roles_bloqueados = [
            ("Veterinario", self.headers_vet),
            ("Peticionario", self.headers_pet),
        ]

        for rol_nombre, headers in roles_bloqueados:
            with self.subTest(rol=rol_nombre, metodo="POST", recurso="salidas"):
                resp = self.client.post("/api/inventario/salidas/", {"motivo": "Prueba"}, format="json", **headers)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=rol_nombre, metodo="PATCH", recurso="salidas"):
                resp = self.client.patch(f"/api/inventario/salidas/{salida.id_salida}/", {"motivo": "Modificado"}, format="json", **headers)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=rol_nombre, metodo="DELETE", recurso="salidas"):
                resp = self.client.delete(f"/api/inventario/salidas/{salida.id_salida}/", **headers)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=rol_nombre, metodo="POST", recurso="detalles-compra"):
                resp = self.client.post(
                    "/api/inventario/detalles-compra/",
                    {"id_compra": compra.id_compra, "id_medicamento": self.med.id_medicamento, "cantidad": 5},
                    format="json",
                    **headers,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=rol_nombre, metodo="PATCH", recurso="detalles-compra"):
                resp = self.client.patch(
                    f"/api/inventario/detalles-compra/{det_compra.id_detalle_compra}/",
                    {"cantidad": 20},
                    format="json",
                    **headers,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=rol_nombre, metodo="DELETE", recurso="detalles-compra"):
                resp = self.client.delete(f"/api/inventario/detalles-compra/{det_compra.id_detalle_compra}/", **headers)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Jurídico (Rol 2) como gestor autorizado de inventario general
        with self.subTest(rol="Juridico", metodo="POST", recurso="salidas"):
            resp = self.client.post("/api/inventario/salidas/", {"motivo": "Prueba Juridico"}, format="json", **self.headers_juridico)
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        with self.subTest(rol="Juridico", metodo="PATCH", recurso="salidas"):
            resp = self.client.patch(f"/api/inventario/salidas/{salida.id_salida}/", {"motivo": "Modificado Juridico"}, format="json", **self.headers_juridico)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        with self.subTest(rol="Juridico", metodo="DELETE", recurso="salidas"):
            resp = self.client.delete(f"/api/inventario/salidas/{salida.id_salida}/", **self.headers_juridico)
            self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        with self.subTest(rol="Juridico", metodo="POST", recurso="detalles-compra"):
            resp = self.client.post(
                "/api/inventario/detalles-compra/",
                {"id_compra": compra.id_compra, "id_medicamento": self.med.id_medicamento, "cantidad": 5},
                format="json",
                **self.headers_juridico,
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        with self.subTest(rol="Juridico", metodo="PATCH", recurso="detalles-compra"):
            resp = self.client.patch(
                f"/api/inventario/detalles-compra/{det_compra.id_detalle_compra}/",
                {"cantidad": 20},
                format="json",
                **self.headers_juridico,
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        with self.subTest(rol="Juridico", metodo="DELETE", recurso="detalles-compra"):
            resp = self.client.delete(f"/api/inventario/detalles-compra/{det_compra.id_detalle_compra}/", **self.headers_juridico)
            self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_n03_veterinario_patch_cantidad_negativa_400(self):
        """
        N-03: veterinario (rol 3) PATCH cantidad_actual negativa -> 403 Forbidden (bloqueado en capa de permisos).
        Tras la separación de roles, el rol Veterinario no tiene acceso de escritura ni lectura a /inventarios/.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=30, estado="Disponible")
        resp = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": -5},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 30)

    def test_n04_veterinario_put_inventarios_403(self):
        """
        N-04: veterinario PUT /inventarios/{id}/ -> 403 Forbidden.
        Payload: {"cantidad_actual": 20, "estado": "Disponible"}
        Por que existe: El metodo PUT reemplaza la entidad completa; no esta permitido a staff clinico, solo el metodo PATCH de ajuste parcial.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=30, estado="Disponible")
        payload = {"cantidad_actual": 20, "estado": "Disponible"}
        resp = self.client.put(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 30)

    def test_n05_forma_respuesta_patch_acta_web_paridad_con_admin(self):
        """
        N-05: FORMA DE RESPUESTA: el PATCH de ajuste sobre /inventarios/{id}/ por Jurídico (rol 2)
        responde 200 con el MISMO conjunto de claves que el PATCH equivalente del Administrador (rol 1),
        mientras Veterinario (rol 3) recibe 403 Forbidden sin alterar el lote.
        """
        lote_jur = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")
        lote_admin = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")
        lote_vet = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")

        payload = {"cantidad_actual": 25, "estado": "Disponible"}

        resp_jur = self.client.patch(
            f"/api/inventario/inventarios/{lote_jur.id_inventario}/",
            payload,
            format="json",
            **self.headers_juridico,
        )
        self.assertEqual(resp_jur.status_code, status.HTTP_200_OK)

        resp_admin = self.client.patch(
            f"/api/inventario/inventarios/{lote_admin.id_inventario}/",
            payload,
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_admin.status_code, status.HTTP_200_OK)

        self.assertEqual(set(resp_jur.data.keys()), set(resp_admin.data.keys()))

        # Veterinario (rol 3) permanece bloqueado en bodega general
        resp_vet = self.client.patch(
            f"/api/inventario/inventarios/{lote_vet.id_inventario}/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_403_FORBIDDEN)
        lote_vet.refresh_from_db()
        self.assertEqual(lote_vet.cantidad_actual, 50)

    def test_n06_descuentos_sucesivos_y_obsoleto_400(self):
        """
        N-06: DESCUENTOS SUCESIVOS Y BLOQUEO DE VETERINARIO:
        - Jurídico (gestor) realiza PATCH 50->40 -> 200 OK y BD queda en 40.
        - Veterinario (rol 3) intentando PATCH sobre el mismo lote recibe 403 Forbidden y la BD queda en 40.
        """
        lote = crear_lote_bodega(medicamento=self.med, cantidad=50, estado="Disponible")

        # 1. Veterinario bloqueado antes de cualquier mutación -> 403
        resp_vet_pre = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 40},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_vet_pre.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 50)

        # 2. Jurídico (gestor) aplica descuento 50 -> 40 -> 200 OK
        resp1 = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 40},
            format="json",
            **self.headers_juridico,
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 40)

        # 3. Veterinario intentando PATCH posterior -> 403 Forbidden, BD intacta en 40
        resp2 = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 45},
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp2.status_code, status.HTTP_403_FORBIDDEN)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 40)

    def test_n07_administrador_gestor_control_aumentar_desvincular_borrar(self):
        """
        N-07: administrador (gestor) sigue pudiendo aumentar, desvincular y borrar con 200/204.
        Payloads:
        - Aumentar: {"cantidad_actual": 80}
        - Desvincular: {"id_detalle_compra": None}
        - Borrar: DELETE
        Por que existe: Test de control en la suite endurecida para verificar que el gestor conserva todas sus capacidades operativas.
        """
        compra = Compra.objects.create(id_proveedor=self.prov)
        detalle = DetalleCompra.objects.create(
            id_compra=compra,
            id_medicamento=self.med,
            cantidad=50,
        )
        lote = Inventarios.objects.create(
            id_detalle_compra=detalle,
            cantidad_actual=50,
            estado="Disponible",
        )

        # 1. Aumentar stock a 80 -> 200 OK
        resp_aumento = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"cantidad_actual": 80},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_aumento.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 80)

        # 2. Desvincular id_detalle_compra -> 200 OK
        resp_desvincular = self.client.patch(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            {"id_detalle_compra": None},
            format="json",
            **self.headers_admin,
        )
        self.assertEqual(resp_desvincular.status_code, status.HTTP_200_OK)
        lote.refresh_from_db()
        self.assertIsNone(lote.id_detalle_compra)

        # 3. Borrar lote -> 204 No Content
        resp_borrar = self.client.delete(
            f"/api/inventario/inventarios/{lote.id_inventario}/",
            **self.headers_admin,
        )
        self.assertEqual(resp_borrar.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Inventarios.objects.filter(pk=lote.id_inventario).exists())

