"""
Catálogo público de adopciones (GET /api/adopciones/) — auditoría A-01 / JMeter PUB-02 [DEF-03].

El público, los ciudadanos y los roles no administradores solo deben ver animales disponibles
para adopción y nunca las observaciones veterinarias. El Administrador conserva el catálogo
completo (incluye "No Disponible") que usa la gestión de adopciones de Angular.
"""

import json

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from adopciones.api.views import AdopcionAnimalViewSet
from animales.models import Animal
from especies.models import Especie, Raza
from users.models import Rol, Usuarios

NOTA_CLINICA_DISPONIBLE = 'NOTA CLINICA PRIVADA: tratamiento antibiotico y control de herida'
NOTA_CLINICA_PACIENTE = 'PACIENTE CLINICO PRIVADO: caso de maltrato en seguimiento'
DESCRIPCION_ADOPCION = 'Jugueton y sociable, ideal para familias'
CAMPOS_CONTRATO = {'id', 'nombre', 'raza', 'descripcion', 'imagen', 'estado', 'fecha'}

lista = AdopcionAnimalViewSet.as_view({'get': 'list'})
detalle = AdopcionAnimalViewSet.as_view({'get': 'retrieve', 'put': 'update'})


class CatalogoAdopcionesPrivacidadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        especie = Especie.objects.create(nombre='Canino QA adopciones')
        cls.raza = Raza.objects.create(id_especie=especie, nombre='Mestizo QA adopciones')
        roles = {}
        for id_rol, nombre in ((1, 'Administrador'), (2, 'Jurídico'), (3, 'Veterinario'),
                               (4, 'Peticionario'), (5, 'super_administrador')):
            roles[id_rol], _ = Rol.objects.get_or_create(id_rol=id_rol, defaults={'nombre_rol': nombre})
        cls.usuarios = {
            id_rol: Usuarios.objects.create(
                id_rol=rol, nombre=f'QA{id_rol}', email=f'qa.adopciones.{id_rol}@vetericano.test',
                password='x', activo=True,
            )
            for id_rol, rol in roles.items()
        }
        cls.disponible = Animal.objects.create(
            id_raza=cls.raza, nombre='QA_Disponible', caracteristicas=DESCRIPCION_ADOPCION,
            observaciones=NOTA_CLINICA_DISPONIBLE, disponible_adopcion=True, activo=True,
        )
        cls.paciente = Animal.objects.create(
            id_raza=cls.raza, nombre='QA_ClinicoPrivado', caracteristicas='cicatriz quirurgica',
            observaciones=NOTA_CLINICA_PACIENTE, disponible_adopcion=False, activo=True,
        )

    def _get(self, vista, usuario=None, query='', **kwargs):
        request = APIRequestFactory().get(f'/api/adopciones/{query}')
        if usuario is not None:
            force_authenticate(request, user=usuario)
        return vista(request, **kwargs)

    def _cuerpo(self, response):
        response.render()
        return response.content.decode('utf-8')

    def _no_administradores(self):
        yield 'anonimo', None
        for id_rol in (2, 3, 4, 5):
            yield f'rol {id_rol}', self.usuarios[id_rol]

    def test_lista_publica_solo_disponibles_y_sin_observaciones(self):
        for etiqueta, usuario in self._no_administradores():
            with self.subTest(quien=etiqueta):
                resp = self._get(lista, usuario)
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                cuerpo = self._cuerpo(resp)
                nombres = [a['nombre'] for a in resp.data]
                self.assertIn('QA_Disponible', nombres)
                self.assertNotIn('QA_ClinicoPrivado', nombres)
                self.assertNotIn(NOTA_CLINICA_DISPONIBLE, cuerpo)
                self.assertNotIn(NOTA_CLINICA_PACIENTE, cuerpo)
                self.assertNotIn('cicatriz quirurgica', cuerpo)

    def test_detalle_publico_de_paciente_clinico_da_404(self):
        for etiqueta, usuario in self._no_administradores():
            with self.subTest(quien=etiqueta):
                resp = self._get(detalle, usuario, pk=self.paciente.pk)
                self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detalle_publico_de_disponible_usa_la_descripcion_de_adopcion(self):
        resp = self._get(detalle, None, pk=self.disponible.pk)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['descripcion'], DESCRIPCION_ADOPCION)
        self.assertNotIn(NOTA_CLINICA_DISPONIBLE, self._cuerpo(resp))

    def test_contrato_android_lista_plana_con_estado(self):
        resp = self._get(lista, self.usuarios[4])
        self.assertIsInstance(resp.data, list)
        animal = next(a for a in resp.data if a['nombre'] == 'QA_Disponible')
        self.assertEqual(set(animal), CAMPOS_CONTRATO)
        self.assertEqual(animal['estado'], 'Disponible')

    def test_paginacion_opcional_sigue_funcionando(self):
        resp = self._get(lista, None, query='?page=1&page_size=50')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        nombres = [a['nombre'] for a in resp.data['results']]
        self.assertIn('QA_Disponible', nombres)
        self.assertNotIn('QA_ClinicoPrivado', nombres)

    def test_administrador_conserva_el_catalogo_completo(self):
        admin = self.usuarios[1]
        resp = self._get(lista, admin)
        nombres = {a['nombre']: a for a in resp.data}
        self.assertIn('QA_Disponible', nombres)
        self.assertIn('QA_ClinicoPrivado', nombres)
        self.assertEqual(nombres['QA_ClinicoPrivado']['estado'], 'No Disponible')
        # Tampoco el Administrador recibe notas clínicas en este catálogo (están en el módulo clínico).
        self.assertNotIn(NOTA_CLINICA_PACIENTE, self._cuerpo(resp))
        self.assertEqual(self._get(detalle, admin, pk=self.paciente.pk).status_code, status.HTTP_200_OK)

    def test_edicion_del_administrador_es_lo_que_ve_el_publico(self):
        request = APIRequestFactory().put(
            f'/api/adopciones/{self.disponible.pk}/',
            {'nombre': 'QA_Disponible', 'descripcion': 'Descripcion revisada por el admin',
             'estado': 'Disponible', 'raza': 'Mestizo QA adopciones'},
            format='json',
        )
        force_authenticate(request, user=self.usuarios[1])
        self.assertEqual(detalle(request, pk=self.disponible.pk).status_code, status.HTTP_200_OK)
        publico = self._get(detalle, None, pk=self.disponible.pk)
        self.assertEqual(publico.data['descripcion'], 'Descripcion revisada por el admin')

    def test_edicion_normal_del_administrador_no_publica_observaciones(self):
        """Recorrido real de Angular: GET del admin -> PUT con lo recibido -> el público no ve notas clínicas."""
        admin = self.usuarios[1]
        recibido = self._get(detalle, admin, pk=self.disponible.pk).data
        self.assertNotIn(NOTA_CLINICA_DISPONIBLE, json.dumps(recibido))
        request = APIRequestFactory().put(
            f'/api/adopciones/{self.disponible.pk}/', {**recibido, 'imagen': 'https://foto.nueva/x.jpg'}, format='json',
        )
        force_authenticate(request, user=admin)
        self.assertEqual(detalle(request, pk=self.disponible.pk).status_code, status.HTTP_200_OK)
        for usuario in (None, admin):
            cuerpo = self._cuerpo(self._get(detalle, usuario, pk=self.disponible.pk))
            self.assertNotIn(NOTA_CLINICA_DISPONIBLE, cuerpo)
        self.assertEqual(Animal.objects.get(pk=self.disponible.pk).observaciones, NOTA_CLINICA_DISPONIBLE)

    def test_retiro_de_adopcion_lo_saca_del_catalogo_publico(self):
        admin = self.usuarios[1]
        recibido = self._get(detalle, admin, pk=self.disponible.pk).data
        request = APIRequestFactory().put(
            f'/api/adopciones/{self.disponible.pk}/', {**recibido, 'estado': 'No Disponible'}, format='json',
        )
        force_authenticate(request, user=admin)
        self.assertEqual(detalle(request, pk=self.disponible.pk).status_code, status.HTTP_200_OK)
        self.assertEqual(self._get(detalle, None, pk=self.disponible.pk).status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn('QA_Disponible', [a['nombre'] for a in self._get(lista, None).data])
        # El Administrador lo sigue viendo como "No Disponible" para poder republicarlo.
        self.assertEqual(self._get(detalle, admin, pk=self.disponible.pk).data['estado'], 'No Disponible')

    def test_escritura_anonima_rechazada_y_no_disponible_sigue_dando_403(self):
        request = APIRequestFactory().put(f'/api/adopciones/{self.disponible.pk}/', {'nombre': 'x'}, format='json')
        self.assertIn(detalle(request, pk=self.disponible.pk).status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        request = APIRequestFactory().put(f'/api/adopciones/{self.paciente.pk}/', {'nombre': 'x'}, format='json')
        force_authenticate(request, user=self.usuarios[3])
        self.assertEqual(detalle(request, pk=self.paciente.pk).status_code, status.HTTP_403_FORBIDDEN)

    def test_escrituras_siguen_reservadas_al_administrador(self):
        for id_rol in (2, 3, 4, 5):
            with self.subTest(rol=id_rol):
                request = APIRequestFactory().put(
                    f'/api/adopciones/{self.disponible.pk}/',
                    json.dumps({'nombre': 'hack'}), content_type='application/json',
                )
                force_authenticate(request, user=self.usuarios[id_rol])
                self.assertEqual(detalle(request, pk=self.disponible.pk).status_code, status.HTTP_403_FORBIDDEN)
