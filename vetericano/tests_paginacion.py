"""
Pruebas de paginación opcional (opt-in) en vetericano/paginacion.py y en endpoints reales.
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from especies.models import Especie
from patologias.models import Patologia
from peticiones.models import Peticiones
from users.constants import ROL_ADMINISTRADOR
from vetericano.paginacion import PaginacionOpcional, ordenar_de_forma_estable


class _UsuarioPrueba:
    """Usuario autenticado simulado compatible con los permisos basados en rol."""

    def __init__(self, id_usuario=1, rol_id=ROL_ADMINISTRADOR, activo=True):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id, nombre_rol='Administrador')
        self.activo = activo
        self.is_authenticated = True
        self.is_anonymous = False
        self.nombre = 'Admin'
        self.apellido = 'Prueba'
        self.email = f'admin{id_usuario}@vetericano.test'


class PaginacionOpcionalUnitTestCase(SimpleTestCase):
    """Pruebas unitarias sin base de datos para PaginacionOpcional y ordenar_de_forma_estable."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.paginador = PaginacionOpcional()

    def _crear_request(self, ruta):
        return Request(self.factory.get(ruta))

    def test_sin_parametros_no_pagina(self):
        request = self._crear_request('/api/recurso/')
        resultado = self.paginador.paginate_queryset([1, 2, 3, 4, 5], request)
        self.assertIsNone(resultado)

    def test_con_page_y_page_size_pagina_y_devuelve_contrato_estandar(self):
        request = self._crear_request('/api/recurso/?page=2&page_size=2')
        pagina = self.paginador.paginate_queryset([1, 2, 3, 4, 5], request)

        self.assertEqual(pagina, [3, 4])

        respuesta = self.paginador.get_paginated_response(pagina)
        self.assertEqual(
            set(respuesta.data.keys()),
            {'count', 'next', 'previous', 'results'},
        )
        self.assertEqual(respuesta.data['count'], 5)
        self.assertEqual(respuesta.data['results'], [3, 4])
        self.assertIsNotNone(respuesta.data['next'])
        self.assertIsNotNone(respuesta.data['previous'])

    def test_page_size_mayor_al_maximo_se_limita_a_100(self):
        request = self._crear_request('/api/recurso/?page=1&page_size=500')
        datos = list(range(1, 121))
        pagina = self.paginador.paginate_queryset(datos, request)

        self.assertEqual(len(pagina), 100)
        self.assertEqual(pagina[0], 1)
        self.assertEqual(pagina[-1], 100)

    def test_solo_page_size_tambien_pagina(self):
        request = self._crear_request('/api/recurso/?page_size=2')
        pagina = self.paginador.paginate_queryset([10, 20, 30, 40, 50], request)

        self.assertEqual(pagina, [10, 20])
        respuesta = self.paginador.get_paginated_response(pagina)
        self.assertEqual(respuesta.data['count'], 5)
        self.assertIsNone(respuesta.data['previous'])
        self.assertIsNotNone(respuesta.data['next'])

    def test_ordenar_de_forma_estable_sin_orden(self):
        qs = ordenar_de_forma_estable(Especie.objects.all())
        self.assertEqual(qs.query.order_by, ('pk',))

    def test_ordenar_de_forma_estable_agrega_pk_como_desempate(self):
        qs = ordenar_de_forma_estable(Peticiones.objects.all().order_by('-fecha'))
        self.assertEqual(qs.query.order_by, ('-fecha', 'pk'))

    def test_ordenar_de_forma_estable_no_altera_orden_que_ya_incluye_pk(self):
        qs_peticiones = ordenar_de_forma_estable(Peticiones.objects.all().order_by('-id_peticion'))
        self.assertEqual(qs_peticiones.query.order_by, ('-id_peticion',))

        qs_especies = ordenar_de_forma_estable(Especie.objects.all().order_by('-id_especie'))
        self.assertEqual(qs_especies.query.order_by, ('-id_especie',))

    def test_ordenar_de_forma_estable_mantiene_lista_python_intacta(self):
        lista = [{'id': 3}, {'id': 1}, {'id': 2}]
        resultado = ordenar_de_forma_estable(lista)
        self.assertIs(resultado, lista)
        self.assertEqual(resultado, [{'id': 3}, {'id': 1}, {'id': 2}])


class PaginacionOpcionalIntegracionAPITestCase(APITestCase):
    """Pruebas de integración HTTP con base de datos para endpoints con PaginacionOpcional."""

    def setUp(self):
        self.client = APIClient()
        self.admin = _UsuarioPrueba(id_usuario=1, rol_id=ROL_ADMINISTRADOR, activo=True)
        self.client.force_authenticate(user=self.admin)

    def test_especies_sin_parametros_lista_plana_y_con_paginacion_devuelve_objeto(self):
        Especie.objects.create(nombre='Especie Paginacion Test 1', activo=True)
        Especie.objects.create(nombre='Especie Paginacion Test 2', activo=True)
        total = Especie.objects.count()
        self.assertGreaterEqual(total, 2)

        resp_plana = self.client.get('/api/especies/especies/')
        self.assertEqual(resp_plana.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp_plana.data, list)
        self.assertEqual(len(resp_plana.data), total)

        resp_pag = self.client.get('/api/especies/especies/?page=1&page_size=1')
        self.assertEqual(resp_pag.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp_pag.data, dict)
        self.assertEqual(
            set(resp_pag.data.keys()),
            {'count', 'next', 'previous', 'results'},
        )
        self.assertEqual(resp_pag.data['count'], total)
        self.assertEqual(len(resp_pag.data['results']), 1)
        self.assertIsNotNone(resp_pag.data['next'])
        self.assertIsNone(resp_pag.data['previous'])

    def test_patologias_sin_parametros_lista_plana_y_con_paginacion_devuelve_objeto(self):
        Patologia.objects.create(
            nombre='Patologia Paginacion Test 1',
            tipo_de_enfermedad='Infecciosa',
            activo=True,
        )
        Patologia.objects.create(
            nombre='Patologia Paginacion Test 2',
            tipo_de_enfermedad='Digestiva',
            activo=True,
        )
        total = Patologia.objects.count()
        self.assertGreaterEqual(total, 2)

        resp_plana = self.client.get('/api/patologias/patologias/')
        self.assertEqual(resp_plana.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp_plana.data, list)
        self.assertEqual(len(resp_plana.data), total)

        resp_pag = self.client.get('/api/patologias/patologias/?page=1&page_size=1')
        self.assertEqual(resp_pag.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp_pag.data, dict)
        self.assertEqual(
            set(resp_pag.data.keys()),
            {'count', 'next', 'previous', 'results'},
        )
        self.assertEqual(resp_pag.data['count'], total)
        self.assertEqual(len(resp_pag.data['results']), 1)
        self.assertIsNotNone(resp_pag.data['next'])
        self.assertIsNone(resp_pag.data['previous'])
