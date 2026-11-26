"""
A-03 — Edición de actas de visita (PATCH/PUT /api/peticiones/seguimiento/<id>/).

El backend (no Android) debe impedir:
- cambiar la petición asociada, el veterinario responsable, el radicado, el animal o la ubicación;
- reescribir un acta confirmada (con radicado o firmas): solo se pueden AÑADIR evidencias;
- reescribir los registros automáticos "Cambio de estado a '…'" del historial.
Y conservar la edición legítima de un acta no confirmada. Las peticiones se envían "manipuladas"
directamente a la vista (JSON y multipart), sin pasar por ningún cliente.
"""

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from inventario.tests.base import VetericanoTestCase
from inventario.tests.factories import crear_peticion_visita, crear_usuario, sembrar_catalogos_base
from peticiones.api.views import SeguimientoPeticionesVisitaDetailView
from peticiones.models import (
    EvidenciaSeguimientoVisita, SeguimientoPeticionesVisita, SeguimientoVisitaFuncionarios, Ubicaciones,
)

detalle = SeguimientoPeticionesVisitaDetailView.as_view()
FIRMA = 'https://res.cloudinary.com/vetericano-test/image/upload/firma.png'
FOTO = 'https://res.cloudinary.com/vetericano-test/image/upload/evidencia-nueva.jpg'


class EdicionActasSeguridadTests(VetericanoTestCase):
    def setUp(self):
        super().setUp()
        sembrar_catalogos_base()
        self.admin = crear_usuario(rol_id=1, nombre='Admin')
        self.juridico = crear_usuario(rol_id=2, nombre='Juridico')
        self.vet1 = crear_usuario(rol_id=3, nombre='Vet1')
        self.vet2 = crear_usuario(rol_id=3, nombre='Vet2')
        self.peticion_vet1 = crear_peticion_visita(veterinario=self.vet1)
        self.peticion_vet2 = crear_peticion_visita(veterinario=self.vet2)

        self.borrador = SeguimientoPeticionesVisita.objects.create(
            id_peticion=self.peticion_vet1, id_veterinario=self.vet1,
            nombre_paciente='Toby', tratamiento_realizado='Inicial', observacion='Sin confirmar',
        )
        self.confirmada = SeguimientoPeticionesVisita.objects.create(
            id_peticion=self.peticion_vet1, id_veterinario=self.vet1, numero_radicado='RAD-VIS-QA-0001',
            nombre_paciente='Luna', tratamiento_realizado='Original firmado', firma_notificador=FIRMA,
            notificado_nombre='Propietario QA',
        )
        SeguimientoVisitaFuncionarios.objects.create(
            id_seguimiento=self.confirmada, id_usuario=self.vet1, nombre='Vet1 QA', cargo='Veterinario',
        )
        self.registro_estado = SeguimientoPeticionesVisita.objects.create(
            id_peticion=self.peticion_vet1, id_veterinario=self.vet1,
            observacion="Cambio de estado a 'En Proceso': revisión",
        )

    def _enviar(self, acta, datos, usuario=None, metodo='patch', formato='json'):
        factory = APIRequestFactory()
        request = getattr(factory, metodo)(
            f'/api/peticiones/seguimiento/{acta.pk}/', datos, format=formato,
        )
        force_authenticate(request, user=usuario or self.vet1)
        return detalle(request, id_seguimiento=acta.pk)

    def _recargar(self, acta):
        return SeguimientoPeticionesVisita.objects.get(pk=acta.pk)

    # ── Identidad del acta inmutable (todas las actas) ────────────────────────────────────

    def test_no_se_puede_mover_el_acta_a_otra_peticion(self):
        for formato in ('json', 'multipart'):
            with self.subTest(formato=formato):
                resp = self._enviar(self.borrador, {'id_peticion': self.peticion_vet2.pk}, formato=formato)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertIn('id_peticion', resp.data)
                self.assertEqual(self._recargar(self.borrador).id_peticion_id, self.peticion_vet1.pk)

    def test_no_se_puede_cambiar_el_veterinario_responsable(self):
        resp = self._enviar(self.borrador, {'id_veterinario': self.vet2.pk})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn('id_veterinario', resp.data)
        self.assertEqual(self._recargar(self.borrador).id_veterinario_id, self.vet1.pk)

    def test_no_se_puede_cambiar_radicado_ni_desde_el_administrador(self):
        for usuario in (self.vet1, self.admin):
            with self.subTest(rol=usuario.id_rol_id):
                resp = self._enviar(self.borrador, {'numero_radicado': 'RAD-FALSO'}, usuario=usuario)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertFalse(self._recargar(self.borrador).numero_radicado)

    def test_mismos_valores_de_identidad_se_aceptan_y_la_edicion_legitima_funciona(self):
        """Contrato JMeter PET-38: PUT con los mismos id_peticion/id_veterinario + cambios -> 200."""
        resp = self._enviar(self.borrador, {
            'id_peticion': self.peticion_vet1.pk, 'id_veterinario': self.vet1.pk,
            'observacion': 'Observación ampliada', 'tratamiento_realizado': 'Tratamiento corregido',
        }, metodo='put')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        acta = self._recargar(self.borrador)
        self.assertEqual(acta.observacion, 'Observación ampliada')
        self.assertEqual(acta.tratamiento_realizado, 'Tratamiento corregido')
        self.assertEqual(acta.id_peticion_id, self.peticion_vet1.pk)

    # ── Actas confirmadas ─────────────────────────────────────────────────────────────────

    def test_acta_confirmada_no_se_reescribe(self):
        intentos = [
            {'tratamiento_realizado': 'Reescrito'},
            {'firma_notificado': FIRMA},
            {'notificado_nombre': 'Otra persona'},
            {'funcionarios': [{'nombre': 'Intruso', 'cargo': 'x', 'es_externo': True}]},
            {'fecha_atencion': '2020-01-01'},
            {'observacion': 'cambio'},
        ]
        for usuario in (self.vet1, self.admin):
            for datos in intentos:
                with self.subTest(rol=usuario.id_rol_id, campo=next(iter(datos))):
                    resp = self._enviar(self.confirmada, datos, usuario=usuario)
                    self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
                    self.assertEqual(resp.data['codigo'], 'ACTA_CONFIRMADA')
        acta = self._recargar(self.confirmada)
        self.assertEqual(acta.tratamiento_realizado, 'Original firmado')
        self.assertFalse(acta.firma_notificado)
        self.assertEqual(acta.notificado_nombre, 'Propietario QA')
        self.assertEqual(list(acta.funcionarios.values_list('nombre', flat=True)), ['Vet1 QA'])

    def test_acta_confirmada_solo_admite_anadir_evidencias(self):
        antes = EvidenciaSeguimientoVisita.objects.filter(id_seguimiento=self.confirmada).count()
        resp = self._enviar(self.confirmada, {'fotos': [FOTO]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(EvidenciaSeguimientoVisita.objects.filter(id_seguimiento=self.confirmada).count(), antes + 1)
        self.assertEqual(self._recargar(self.confirmada).tratamiento_realizado, 'Original firmado')

    def test_acta_confirmada_con_identidad_manipulada_da_400(self):
        antes = EvidenciaSeguimientoVisita.objects.filter(id_seguimiento=self.confirmada).count()
        resp = self._enviar(self.confirmada, {'id_peticion': self.peticion_vet2.pk, 'fotos': [FOTO]})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(self._recargar(self.confirmada).id_peticion_id, self.peticion_vet1.pk)
        # Nada se guarda a medias: tampoco la evidencia de la misma petición.
        self.assertEqual(EvidenciaSeguimientoVisita.objects.filter(id_seguimiento=self.confirmada).count(), antes)

    def test_confirmada_solo_por_firma_tambien_se_bloquea(self):
        firmada = SeguimientoPeticionesVisita.objects.create(
            id_peticion=self.peticion_vet1, id_veterinario=self.vet1, nombre_paciente='Max',
            tratamiento_realizado='Firmado sin radicado', firma_notificado=FIRMA,
        )
        resp = self._enviar(firmada, {'tratamiento_realizado': 'Reescrito'})
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
        self.assertEqual(self._recargar(firmada).tratamiento_realizado, 'Firmado sin radicado')

    def test_no_se_puede_vaciar_el_radicado_de_un_acta_confirmada(self):
        for valor in ('', None):
            with self.subTest(valor=valor):
                resp = self._enviar(self.confirmada, {'numero_radicado': valor})
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(self._recargar(self.confirmada).numero_radicado, 'RAD-VIS-QA-0001')

    def test_animal_y_ubicacion_del_acta_son_inmutables(self):
        from inventario.tests.factories import crear_animal_con_historia
        animal, _ = crear_animal_con_historia()
        otra_ubicacion = Ubicaciones.objects.create(direccion='Otra calle', latitud=2.44, longitud=-76.61)
        for datos, campo in (({'id_animal': animal.pk}, 'id_animal'),
                             ({'id_ubicacion_visita': otra_ubicacion.pk}, 'id_ubicacion_visita')):
            with self.subTest(campo=campo):
                resp = self._enviar(self.borrador, datos)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertIn(campo, resp.data)
        acta = self._recargar(self.borrador)
        self.assertIsNone(acta.id_animal_id)
        self.assertIsNone(acta.id_ubicacion_visita_id)

    def test_lugar_atencion_no_modifica_la_ubicacion_de_otra_peticion(self):
        """Si el acta apunta a una ubicación que comparte otra petición, editarla crea una propia."""
        ubicacion_ajena = Ubicaciones.objects.create(direccion='Casa ajena', latitud=2.40, longitud=-76.60)
        self.peticion_vet2.id_ubicacion = ubicacion_ajena
        self.peticion_vet2.save(update_fields=['id_ubicacion'])
        SeguimientoPeticionesVisita.objects.filter(pk=self.borrador.pk).update(id_ubicacion_visita=ubicacion_ajena)

        resp = self._enviar(self.borrador, {'lugar_atencion': {'direccion': 'Dirección corregida', 'latitud': 1.0, 'longitud': -1.0}})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        ubicacion_ajena.refresh_from_db()
        self.assertEqual(ubicacion_ajena.direccion, 'Casa ajena')
        acta = self._recargar(self.borrador)
        self.assertNotEqual(acta.id_ubicacion_visita_id, ubicacion_ajena.pk)
        self.assertEqual(acta.id_ubicacion_visita.direccion, 'Dirección corregida')

    def test_firmar_un_borrador_lo_confirma_y_bloquea_ediciones_posteriores(self):
        self.assertEqual(self._enviar(self.borrador, {'firma_notificador': FIRMA}).status_code, status.HTTP_200_OK)
        resp = self._enviar(self.borrador, {'tratamiento_realizado': 'Cambio tras firmar'})
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
        self.assertEqual(self._recargar(self.borrador).tratamiento_realizado, 'Inicial')

    def test_patch_vacio_sobre_confirmada_no_cambia_nada(self):
        self.assertEqual(self._enviar(self.confirmada, {}).status_code, status.HTTP_200_OK)
        self.assertEqual(self._recargar(self.confirmada).tratamiento_realizado, 'Original firmado')

    def test_otro_veterinario_sobre_acta_confirmada_recibe_403(self):
        resp = self._enviar(self.confirmada, {'tratamiento_realizado': 'x'}, usuario=self.vet2)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_se_puede_disfrazar_un_borrador_como_registro_automatico(self):
        resp = self._enviar(self.borrador, {'observacion': "Cambio de estado a 'Cerrada': falso"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(self._recargar(self.borrador).observacion, 'Sin confirmar')

    # ── Registros automáticos de cambio de estado ─────────────────────────────────────────

    def test_registro_de_cambio_de_estado_es_inmutable(self):
        for datos in ({'observacion': 'Acta falsa'}, {'nombre_paciente': 'Inventado'}, {'fotos': [FOTO]}):
            with self.subTest(campo=next(iter(datos))):
                resp = self._enviar(self.registro_estado, datos)
                self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
                self.assertEqual(resp.data['codigo'], 'REGISTRO_CAMBIO_ESTADO')
        self.assertEqual(self._recargar(self.registro_estado).observacion, "Cambio de estado a 'En Proceso': revisión")

    def test_cuerpo_que_no_es_objeto_no_provoca_500(self):
        for acta in (self.confirmada, self.borrador):
            with self.subTest(acta=acta.pk):
                resp = self._enviar(acta, ['no-es-un-objeto'])
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Permisos existentes conservados ───────────────────────────────────────────────────

    def test_permisos_previos_se_conservan(self):
        self.assertEqual(self._enviar(self.borrador, {'observacion': 'x'}, usuario=self.vet2).status_code,
                         status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._enviar(self.borrador, {'observacion': 'x'}, usuario=self.juridico).status_code,
                         status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._recargar(self.borrador).observacion, 'Sin confirmar')
