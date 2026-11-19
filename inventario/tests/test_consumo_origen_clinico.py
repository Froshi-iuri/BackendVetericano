"""
Contrato del origen clínico en POST /api/inventario/kit/sincronizar-offline/ (consumo DIRECTO).

Documenta lo que la web (VeterinaryInventoryService.registrarConsumo) debe enviar:
- id_consulta (historia clínica) o id_seguimiento_visita (acta de visita), sin mezclar grupos.
- Sin origen o con un origen inexistente el backend responde 400 CONSUMO_INVALIDO y no registra
  consumo, Kardex ni descuento del kit.
- Con origen válido se registra un único consumo APLICADO asociado a ese origen, con su Kardex
  (CONSUMO_HISTORIA o CONSUMO_ACTA) y un único descuento del kit, incluso si se reenvía el mismo
  client_uuid (idempotencia).
"""

import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from inventario.constants import (
    ROL_VETERINARIO,
    TIPO_KARDEX_CONSUMO_ACTA,
    TIPO_KARDEX_CONSUMO_HISTORIA,
    UBICACION_KARDEX_KIT,
)
from inventario.models import InventarioConsumosRegistro, InventarioKardex, KitVeterinarioLotes
from inventario.tests.base import VetericanoTestCase
from inventario.tests.factories import (
    crear_consulta,
    crear_lote_bodega,
    crear_lote_kit,
    crear_medicamento,
    crear_peticion_visita,
    crear_usuario,
)
from inventario.tests.helpers import obtener_headers_jwt
from peticiones.models import SeguimientoPeticionesVisita

URL = "/api/inventario/kit/sincronizar-offline/"


class ConsumoOrigenClinicoContratoTests(VetericanoTestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.hoy = timezone.now().date()
        self.vet = crear_usuario(rol_id=ROL_VETERINARIO, email=f"vet_origen_{uuid.uuid4().hex[:6]}@cba.gov.co")
        self.headers = obtener_headers_jwt(self.vet)
        self.med = crear_medicamento(nombre="Amoxicilina Origen Clínico")
        bodega = crear_lote_bodega(self.med, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=120), estado="Agotado")
        self.lote_kit = crear_lote_kit(self.vet, bodega, cantidad=10, cantidad_ref=10)

    def _post(self, item):
        return self.client.post(URL, {"consumos": [item]}, format="json", **self.headers)

    def _item(self, **origen):
        return {
            "client_uuid": str(uuid.uuid4()),
            "id_medicamento": self.med.id_medicamento,
            "unidades": 2,
            "fecha_consumo": timezone.now().isoformat(),
            **origen,
        }

    def _saldo_kit(self):
        return KitVeterinarioLotes.objects.get(pk=self.lote_kit.pk).cantidad_actual

    def _assert_sin_efectos(self, resp):
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error"), "CONSUMO_INVALIDO")
        self.assertEqual(InventarioConsumosRegistro.objects.count(), 0)
        self.assertEqual(InventarioKardex.objects.filter(ubicacion=UBICACION_KARDEX_KIT).count(), 0)
        self.assertEqual(self._saldo_kit(), 10)

    def test_id_consulta_valido_registra_consumo_asociado_a_la_consulta(self):
        consulta = crear_consulta()
        item = self._item(id_consulta=consulta.id_consulta)

        resp = self._post(item)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["conflictos"], [])
        self.assertEqual([a["client_uuid"] for a in resp.data["aplicados"]], [item["client_uuid"]])
        registro = InventarioConsumosRegistro.objects.get(client_uuid=item["client_uuid"])
        self.assertEqual(registro.estado, "APLICADO")
        self.assertEqual(registro.id_consulta_id, consulta.id_consulta)
        self.assertIsNone(registro.id_seguimiento_visita_id)
        kardex = InventarioKardex.objects.filter(ubicacion=UBICACION_KARDEX_KIT, id_consulta=consulta)
        self.assertEqual(kardex.count(), 1)
        self.assertEqual(kardex.first().tipo_movimiento, TIPO_KARDEX_CONSUMO_HISTORIA)
        self.assertEqual(self._saldo_kit(), 8)

    def test_id_seguimiento_visita_valido_registra_consumo_de_acta(self):
        peticion = crear_peticion_visita(veterinario=self.vet)
        seguimiento = SeguimientoPeticionesVisita.objects.create(
            id_peticion=peticion, id_veterinario=self.vet, observacion="Visita con administración"
        )
        item = self._item(id_seguimiento_visita=seguimiento.id_seguimiento)

        resp = self._post(item)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        registro = InventarioConsumosRegistro.objects.get(client_uuid=item["client_uuid"])
        self.assertEqual(registro.id_seguimiento_visita_id, seguimiento.id_seguimiento)
        self.assertIsNone(registro.id_consulta_id)
        kardex = InventarioKardex.objects.filter(ubicacion=UBICACION_KARDEX_KIT, id_seguimiento_visita=seguimiento)
        self.assertEqual(kardex.count(), 1)
        self.assertEqual(kardex.first().tipo_movimiento, TIPO_KARDEX_CONSUMO_ACTA)
        self.assertEqual(self._saldo_kit(), 8)

    def test_sin_identificador_de_origen_400_sin_efectos(self):
        self._assert_sin_efectos(self._post(self._item()))

    def test_id_consulta_inexistente_400_sin_efectos(self):
        resp = self._post(self._item(id_consulta=987654321))
        self._assert_sin_efectos(resp)
        self.assertIn("identificador de origen no existe", str(resp.data))

    def test_origen_mezclado_historia_y_acta_400_sin_efectos(self):
        consulta = crear_consulta()
        peticion = crear_peticion_visita(veterinario=self.vet)
        seguimiento = SeguimientoPeticionesVisita.objects.create(id_peticion=peticion, id_veterinario=self.vet)
        self._assert_sin_efectos(self._post(self._item(
            id_consulta=consulta.id_consulta, id_seguimiento_visita=seguimiento.id_seguimiento
        )))

    def test_reenvio_mismo_client_uuid_no_descuenta_dos_veces(self):
        consulta = crear_consulta()
        item = self._item(id_consulta=consulta.id_consulta)

        primera = self._post(item)
        segunda = self._post(item)

        self.assertEqual(primera.status_code, status.HTTP_200_OK)
        self.assertEqual(segunda.status_code, status.HTTP_200_OK)
        self.assertTrue(segunda.data["aplicados"][0].get("idempotente"))
        self.assertEqual(InventarioConsumosRegistro.objects.filter(client_uuid=item["client_uuid"]).count(), 1)
        self.assertEqual(InventarioKardex.objects.filter(ubicacion=UBICACION_KARDEX_KIT, id_consulta=consulta).count(), 1)
        self.assertEqual(self._saldo_kit(), 8)
