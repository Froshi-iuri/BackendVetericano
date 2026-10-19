"""
Tests de integración para los enganches de Actas de Visita de Campo con el Kit de Veterinario (Fase D2 - Tarea 10).
Cubre los escenarios TEST-PET-18 a TEST-PET-22 (5 tests).

Especificación y Contratos:
- plan/DISENO_TAREA10.md (§1.5, §3.5, §4.5, §4.7, §5.2: Bloque B).
- peticiones/api/urls.py (rutas reales: /api/peticiones/seguimiento/crear/ y /api/peticiones/seguimiento/<id>/).
- peticiones/api/views.py (SeguimientoPeticionesVisitaCreateView, SeguimientoPeticionesVisitaDetailView).
- peticiones/api/serializers.py (SeguimientoPeticionesVisitaSerializer).
- inventario/services/consumo_service.py (descontar_consumo_kit).
- inventario/services/kit_service.py (_es_entero_valido).
- inventario/services/exceptions.py (CantidadInvalidaError, ConsumoInvalidoError, StockInsuficienteKitError).
- inventario/api/kit_serializers.py (validar_payload_consumo_kit, verificar_idempotencia_consumo_clinico).
- inventario/constants.py.
"""

import json
import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from peticiones.models import SeguimientoPeticionesVisita
from inventario.constants import (
    TIPO_KARDEX_CONSUMO_ACTA,
    UBICACION_KARDEX_KIT,
    CONSUMO_ESTADO_APLICADO,
    CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
)
from inventario.models import (
    InventarioKardex,
    InventarioConsumosRegistro,
    KitVeterinarioLotes,
)
from inventario.services.kit_service import _es_entero_valido
from inventario.services.exceptions import CantidadInvalidaError
from inventario.api.kit_serializers import validar_payload_consumo_kit
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_lote_kit,
    crear_peticion_visita,
)
from inventario.tests.helpers import (
    obtener_headers_jwt,
    payload_acta_visita,
    assert_kardex_consistente,
)


class KitPeticionesHookTestCase(TestCase):
    """
    Suite de integración para los enganches entre las Actas de Visita de Campo y el Kit de Veterinario.
    Valida multipart con cadenas JSON, bloqueo de consumo en PATCH, reintento idempotente,
    inmunidad ante suplantación de veterinario en el body, y tipado estricto en validaciones.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.hoy = timezone.now().date()

        # Veterinario titular autenticado (Rol 3)
        self.vet = crear_usuario(rol_id=3, email="vet_peticiones_hook@cba.gov.co")
        self.headers_vet = obtener_headers_jwt(self.vet)

        # Insumos farmacéuticos para las pruebas del hook de peticiones
        self.med_a = crear_medicamento(nombre="Amoxicilina Peticiones Hook")
        self.med_b = crear_medicamento(nombre="Meloxicam Peticiones Hook")

    def test_pet_18_acta_multipart_cadenas_json_y_booleanos_string(self):
        """
        TEST-PET-18: Multipart del acta con cadenas JSON y booleanos string.
        - POST multipart/form-data a /api/peticiones/seguimiento/crear/.
        - medicamentos_usados enviado como cadena JSON serializada.
        - SubTest 1: sincronizacion_offline="true" con stock insuficiente (0 uds):
          * Interpreta booleano string "true" como True (modo offline).
          * Responde HTTP 201 Created con alertas_farmacia.conflictos_stock (6 claves canónicas).
          * Acta persistida, consumo en RECHAZADO_CONFLICTO, 0 kardex.
        - SubTest 2: sincronizacion_offline="false" con stock insuficiente (0 uds):
          * Interpreta booleano string "false" como False (modo online).
          * Responde HTTP 400 Bad Request con error='STOCK_INSUFICIENTE_KIT'.
          * Rollback total: NO se crea acta, 0 consumos, 0 kardex.
        - SubTest 3: sincronizacion_offline="false" con stock suficiente (10 uds, pide 3):
          * Responde HTTP 201 Created sin alertas_farmacia.
          * Consumo en APLICADO, kit disminuye a 7, 1 kardex CONSUMO_ACTA.
        - Aislamiento de Cloudinary y red:
          * vetericano/test_settings.py e inventario/tests/base.py NO contienen aislamiento de Cloudinary (0 referencias).
          * El aislamiento activo reside exclusivamente en inventario/tests/runner.py (VetericanoTestRunner).
          * No obstante, este test envía la petición multipart SIN archivos adjuntos (sin 'foto'/'fotos'),
            por lo que el serializer no procesa archivos y, por ende, no se ejercita ninguna subida a Cloudinary.
        """
        # SubTest 1: Offline ("true") con stock insuficiente
        with self.subTest(caso="offline_string_true_conflicto"):
            b1 = crear_lote_bodega(self.med_a, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
            kl1 = crear_lote_kit(self.vet, b1, cantidad=0, cantidad_ref=5)
            p1 = crear_peticion_visita(veterinario=self.vet)
            u1 = str(uuid.uuid4())

            payload1 = payload_acta_visita(id_peticion=p1.id_peticion, id_veterinario=self.vet.id_usuario)
            payload1["medicamentos_usados"] = json.dumps([{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u1,
            }])
            payload1["sincronizacion_offline"] = "true"

            resp1 = self.client.post(
                "/api/peticiones/seguimiento/crear/",
                payload1,
                format="multipart",
                **self.headers_vet,
            )
            self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
            id_seg_1 = resp1.data.get("id_seguimiento")
            self.assertIsNotNone(id_seg_1)
            self.assertIn("alertas_farmacia", resp1.data)

            conflictos = resp1.data["alertas_farmacia"].get("conflictos_stock", [])
            self.assertEqual(len(conflictos), 1)
            conf = conflictos[0]
            claves_esperadas = {"client_uuid", "id_medicamento", "nombre_medicamento", "solicitado", "disponible", "motivo_rechazo"}
            self.assertEqual(set(conf.keys()), claves_esperadas)
            self.assertEqual(conf["client_uuid"], u1)
            self.assertEqual(conf["disponible"], 0)
            self.assertEqual(conf["solicitado"], 2)

            # DB: Acta creada, consumo en RECHAZADO_CONFLICTO, cero kardex
            self.assertTrue(SeguimientoPeticionesVisita.objects.filter(pk=id_seg_1).exists())
            reg1 = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
            self.assertIsNotNone(reg1)
            self.assertEqual(reg1.estado, CONSUMO_ESTADO_RECHAZADO_CONFLICTO)
            self.assertEqual(reg1.id_seguimiento_visita_id, id_seg_1)
            self.assertEqual(reg1.stock_disponible_al_rechazo, 0)
            self.assertEqual(InventarioKardex.objects.filter(client_uuid=u1).count(), 0)
            kl1.refresh_from_db()
            self.assertEqual(kl1.cantidad_actual, 0)

        # SubTest 2: Online ("false") con stock insuficiente -> Rollback total HTTP 400
        with self.subTest(caso="online_string_false_stock_insuficiente"):
            b2 = crear_lote_bodega(self.med_b, cantidad=20, fecha_vencimiento=self.hoy + timedelta(days=60))
            kl2 = crear_lote_kit(self.vet, b2, cantidad=0, cantidad_ref=5)
            p2 = crear_peticion_visita(veterinario=self.vet)
            u2 = str(uuid.uuid4())

            payload2 = payload_acta_visita(id_peticion=p2.id_peticion, id_veterinario=self.vet.id_usuario)
            payload2["medicamentos_usados"] = json.dumps([{
                "id_medicamento": self.med_b.id_medicamento,
                "unidades": 2,
                "client_uuid": u2,
            }])
            payload2["sincronizacion_offline"] = "false"

            resp2 = self.client.post(
                "/api/peticiones/seguimiento/crear/",
                payload2,
                format="multipart",
                **self.headers_vet,
            )
            self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp2.data.get("error"), "STOCK_INSUFICIENTE_KIT")

            # Rollback total en base de datos
            self.assertFalse(SeguimientoPeticionesVisita.objects.filter(id_peticion=p2.id_peticion).exists())
            self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u2).count(), 0)
            self.assertEqual(InventarioKardex.objects.filter(client_uuid=u2).count(), 0)
            kl2.refresh_from_db()
            self.assertEqual(kl2.cantidad_actual, 0)

        # SubTest 3: Online ("false") con stock suficiente -> Descuento exitoso HTTP 201
        with self.subTest(caso="online_string_false_stock_suficiente"):
            b3 = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
            kl3 = crear_lote_kit(self.vet, b3, cantidad=10, cantidad_ref=10)
            p3 = crear_peticion_visita(veterinario=self.vet)
            u3 = str(uuid.uuid4())

            payload3 = payload_acta_visita(id_peticion=p3.id_peticion, id_veterinario=self.vet.id_usuario)
            payload3["medicamentos_usados"] = json.dumps([{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 3,
                "client_uuid": u3,
            }])
            payload3["sincronizacion_offline"] = "false"

            resp3 = self.client.post(
                "/api/peticiones/seguimiento/crear/",
                payload3,
                format="multipart",
                **self.headers_vet,
            )
            self.assertEqual(resp3.status_code, status.HTTP_201_CREATED)
            id_seg_3 = resp3.data.get("id_seguimiento")
            self.assertIsNotNone(id_seg_3)
            self.assertNotIn("alertas_farmacia", resp3.data)

            # DB: Consumo en APLICADO, saldo en kit descontado, 1 fila kardex
            kl3.refresh_from_db()
            self.assertEqual(kl3.cantidad_actual, 7)
            reg3 = InventarioConsumosRegistro.objects.filter(client_uuid=u3).first()
            self.assertIsNotNone(reg3)
            self.assertEqual(reg3.estado, CONSUMO_ESTADO_APLICADO)
            self.assertEqual(reg3.id_seguimiento_visita_id, id_seg_3)
            self.assertEqual(reg3.cantidad_descontada, 3)

            assert_kardex_consistente(self, u3, TIPO_KARDEX_CONSUMO_ACTA, cantidad_esperada=3)
            k_row = InventarioKardex.objects.filter(client_uuid=u3).first()
            self.assertEqual(k_row.ubicacion, UBICACION_KARDEX_KIT)
            self.assertEqual(k_row.saldo_resultante, 7)

    def test_pet_19_patch_acta_con_medicamentos_usados_rechazado(self):
        """
        TEST-PET-19: Intento de PATCH sobre acta existente enviando medicamentos_usados.
        - Crea previamente un acta válida vía POST /api/peticiones/seguimiento/crear/.
        - Intenta actualizarla mediante PATCH a la ruta real de actualización
          en peticiones/api/urls.py:29: /api/peticiones/seguimiento/<id_seguimiento>/
          (Nota de desviación de diseño: DISENO_TAREA10.md §4.7 y §5.2 citaban
          /api/peticiones/seguimiento/{id}/actualizar/, pero el patrón real en urls.py:29
          es path('seguimiento/<int:id_seguimiento>/', SeguimientoPeticionesVisitaDetailView)).
        - Retorna HTTP 400 Bad Request con el mensaje de ValidationError exacto de
          peticiones/api/serializers.py:650:
          {"medicamentos_usados": ["No se admite consumo del kit al editar un acta."]}.
        - DB: Acta permanece inalterada; cero consumos registrados; kit intacto.
        """
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)

        # 1. Crear el acta de partida sin medicamentos_usados
        payload_crear = payload_acta_visita(
            id_peticion=peticion.id_peticion,
            id_veterinario=self.vet.id_usuario,
            observacion="Acta inicial para prueba de PATCH",
        )
        resp_crear = self.client.post(
            "/api/peticiones/seguimiento/crear/",
            payload_crear,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_crear.status_code, status.HTTP_201_CREATED)
        id_seguimiento = resp_crear.data["id_seguimiento"]

        acta_pre = SeguimientoPeticionesVisita.objects.get(pk=id_seguimiento)
        conteo_consumos_antes = InventarioConsumosRegistro.objects.count()
        conteo_kardex_antes = InventarioKardex.objects.count()

        # 2. Intentar PATCH sobre /api/peticiones/seguimiento/<id>/ enviando medicamentos_usados
        u_patch = str(uuid.uuid4())
        payload_patch = {
            "observacion": "Intento de actualización con consumo",
            "medicamentos_usados": [{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 2,
                "client_uuid": u_patch,
            }],
        }

        resp_patch = self.client.patch(
            f"/api/peticiones/seguimiento/{id_seguimiento}/",
            payload_patch,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp_patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("medicamentos_usados", resp_patch.data)
        self.assertIn(
            "No se admite consumo del kit al editar un acta.",
            str(resp_patch.data["medicamentos_usados"]),
        )

        # 3. DB inalterada: acta conserva observación original, cero consumos, cero kardex
        acta_post = SeguimientoPeticionesVisita.objects.get(pk=id_seguimiento)
        self.assertEqual(acta_post.observacion, acta_pre.observacion)
        self.assertEqual(InventarioConsumosRegistro.objects.count(), conteo_consumos_antes)
        self.assertEqual(InventarioKardex.objects.count(), conteo_kardex_antes)
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 10)

    def test_pet_20_reintento_idempotente_online_post_acta(self):
        """
        TEST-PET-20: Reintento idempotente online en acta de visita con el mismo client_uuid.
        - Envío inicial exitoso: Acta A1 creada, 3 unidades descontadas del kit (saldo 7).
        - Segundo POST con idéntico payload y mismo client_uuid:
          * Retorna HTTP 201 Created reutilizando la misma acta (id_seguimiento == A1).
          * Invariantes en BD: exactamente 1 acta para esa petición, 1 registro de consumo
            en InventarioConsumosRegistro, 1 fila de kardex CONSUMO_ACTA, y el kit no sufre
            doble descuento (saldo se mantiene en 7).
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
                "unidades": 3,
                "client_uuid": u1,
            }],
            offline=False,
        )

        # Primer POST de creación
        resp1 = self.client.post(
            "/api/peticiones/seguimiento/crear/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        id_seguimiento_1 = resp1.data["id_seguimiento"]

        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 7)

        # Segundo POST idéntico con el mismo client_uuid
        resp2 = self.client.post(
            "/api/peticiones/seguimiento/crear/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data["id_seguimiento"], id_seguimiento_1)

        # Invariantes en BD: sin duplicación
        self.assertEqual(
            SeguimientoPeticionesVisita.objects.filter(id_peticion=peticion.id_peticion).count(),
            1,
        )
        self.assertEqual(
            InventarioConsumosRegistro.objects.filter(client_uuid=u1).count(),
            1,
        )
        self.assertEqual(
            InventarioKardex.objects.filter(client_uuid=u1).count(),
            1,
        )
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 7)

    def test_pet_21_suplantacion_id_veterinario_por_body_en_acta(self):
        """
        TEST-PET-21: Suplantación de id_veterinario por body en acta de visita.
        - Vet A autenticado (request.user), Vet B existente en el sistema.
        - Ambos poseen kit con existencias distinguibles (Vet A: 10 uds, Vet B: 10 uds).
        - Payload envía id_veterinario=Vet_B.id_usuario en el cuerpo JSON.
        - Retorna HTTP 201 Created.
        - El consumo del kit se descuenta obligatoriamente del kit de Vet A (request.user),
          el kit de Vet B permanece 100% intacto.
        - En InventarioConsumosRegistro e InventarioKardex: id_veterinario_id == Vet_A.id_usuario.
        - Verificación en código real de SeguimientoPeticionesVisita.id_veterinario:
          En peticiones/api/views.py:205, la vista ejecuta
          serializer.save(id_veterinario=request.user), por lo que el acta queda
          asociada a Vet A (la vista sobrescribe el valor del body).
        """
        vet_b = crear_usuario(rol_id=3, email="vet_b_suplantacion_pet@cba.gov.co")

        b = crear_lote_bodega(self.med_a, cantidad=100, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl_a = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)
        kl_b = crear_lote_kit(vet_b, b, cantidad=10, cantidad_ref=10)

        peticion = crear_peticion_visita(veterinario=self.vet)
        u1 = str(uuid.uuid4())

        # Payload envía explícitamente el id de Vet B en el body
        payload = payload_acta_visita(
            id_peticion=peticion.id_peticion,
            id_veterinario=vet_b.id_usuario,
            consumos=[{
                "id_medicamento": self.med_a.id_medicamento,
                "unidades": 4,
                "client_uuid": u1,
            }],
            offline=False,
        )

        resp = self.client.post(
            "/api/peticiones/seguimiento/crear/",
            payload,
            format="json",
            **self.headers_vet,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        id_seguimiento = resp.data["id_seguimiento"]

        # Descuento estricto sobre el kit de Vet A; kit de Vet B intacto
        kl_a.refresh_from_db()
        self.assertEqual(kl_a.cantidad_actual, 6)

        kl_b.refresh_from_db()
        self.assertEqual(kl_b.cantidad_actual, 10)

        # Auditoría de consumo asociada a Vet A
        reg = InventarioConsumosRegistro.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.id_veterinario_id, self.vet.id_usuario)

        # Kardex asociado a Vet A tanto en veterinario como en ejecutor
        k_row = InventarioKardex.objects.filter(client_uuid=u1).first()
        self.assertIsNotNone(k_row)
        self.assertEqual(k_row.id_veterinario_id, self.vet.id_usuario)
        self.assertEqual(k_row.id_usuario_ejecuta_id, self.vet.id_usuario)

        # Comprobación del campo id_veterinario en el acta:
        # La vista peticiones/api/views.py:205 ejecuta serializer.save(id_veterinario=request.user),
        # por lo que el acta queda persistida con Vet A.
        acta = SeguimientoPeticionesVisita.objects.get(pk=id_seguimiento)
        self.assertEqual(acta.id_veterinario_id, self.vet.id_usuario)

    def test_pet_22_validacion_tipos_es_entero_valido_y_unidades_bool(self):
        """
        TEST-PET-22: Tipado estricto: _es_entero_valido rechaza estrictamente booleanos.
        - Verifica a nivel unitario que _es_entero_valido(True) y (False) retornen False.
        - Verifica que validar_payload_consumo_kit eleve CantidadInvalidaError
          cuando 'unidades': True se envía en el renglón de consumo
          (Nota de desviación de diseño: DISENO_TAREA10.md §5.2 mencionaba ConsumoInvalidoError,
          pero el código real en inventario/api/kit_serializers.py:444-447 eleva
          CantidadInvalidaError con mensaje 'unidades debe ser un entero mayor a cero').
        - Verifica a nivel de endpoint POST /api/peticiones/seguimiento/crear/
          que se traduzca en HTTP 400 Bad Request con error='CANTIDAD_INVALIDA'.
        - SubTests comparativos con unidades=0 y unidades negativo (-2).
        - DB inalterada: cero actas creadas, cero consumos, cero kardex, kit intacto.
        """
        # 1. Comprobaciones unitarias de _es_entero_valido (inventario/services/kit_service.py:62)
        self.assertFalse(_es_entero_valido(True))
        self.assertFalse(_es_entero_valido(False))
        self.assertFalse(_es_entero_valido(True, minimo=1))
        self.assertFalse(_es_entero_valido(False, minimo=1))
        self.assertFalse(_es_entero_valido(0, minimo=1))
        self.assertFalse(_es_entero_valido(-1, minimo=1))
        self.assertTrue(_es_entero_valido(1, minimo=1))
        self.assertTrue(_es_entero_valido(10, minimo=1))

        # 2. Comprobación directa en validar_payload_consumo_kit
        with self.assertRaises(CantidadInvalidaError):
            validar_payload_consumo_kit(
                raw_meds=[{
                    "id_medicamento": self.med_a.id_medicamento,
                    "unidades": True,
                    "client_uuid": str(uuid.uuid4()),
                }],
                raw_offline=False,
                id_veterinario=self.vet.id_usuario,
            )

        # 3. Comprobaciones en endpoint con subTest para booleanos, cero y negativos
        peticion = crear_peticion_visita(veterinario=self.vet)
        b = crear_lote_bodega(self.med_a, cantidad=50, fecha_vencimiento=self.hoy + timedelta(days=60))
        kl = crear_lote_kit(self.vet, b, cantidad=10, cantidad_ref=10)

        casos_unidades = [
            ("booleano_true", True),
            ("booleano_false", False),
            ("cero", 0),
            ("negativo", -2),
        ]

        for etiqueta, valor_unidades in casos_unidades:
            with self.subTest(caso=etiqueta):
                u_inv = str(uuid.uuid4())
                payload = payload_acta_visita(
                    id_peticion=peticion.id_peticion,
                    id_veterinario=self.vet.id_usuario,
                    consumos=[{
                        "id_medicamento": self.med_a.id_medicamento,
                        "unidades": valor_unidades,
                        "client_uuid": u_inv,
                    }],
                    offline=False,
                )

                resp = self.client.post(
                    "/api/peticiones/seguimiento/crear/",
                    payload,
                    format="json",
                    **self.headers_vet,
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(resp.data.get("error"), "CANTIDAD_INVALIDA")
                self.assertIn("unidades", resp.data.get("detalle", ""))

                # DB inalterada
                self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=u_inv).count(), 0)
                self.assertEqual(InventarioKardex.objects.filter(client_uuid=u_inv).count(), 0)

        # Comprobación global de base de datos intacta
        self.assertFalse(SeguimientoPeticionesVisita.objects.filter(id_peticion=peticion.id_peticion).exists())
        kl.refresh_from_db()
        self.assertEqual(kl.cantidad_actual, 10)
