"""
Pruebas sin base de datos del orden por defecto de los listados (vetericano/ordenamiento.py).
Revisan el ORDER BY que arma cada vista; no cambian la forma del JSON.
"""

from django.db.models import Case
from django.test import SimpleTestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from peticiones.utils import ESTADOS_TERMINALES_NORM, normalizar_texto_juridico
from users.constants import ROL_ADMINISTRADOR
from vetericano.ordenamiento import (
    ESTADOS_PETICION_TERMINADOS,
    orden_peticion_terminada,
    orden_prioridad,
    orden_valor_primero,
    ordenar_peticiones,
)
from vetericano.tests_paginacion import _UsuarioPrueba


def _campos(queryset):
    """Representación legible del order_by: textos tal cual y expresiones como 'Case' / 'F:campo'."""
    salida = []
    for campo in queryset.query.order_by:
        if isinstance(campo, str):
            salida.append(campo)
        elif isinstance(getattr(campo, 'expression', None), Case):
            salida.append('Case')
        elif hasattr(getattr(campo, 'expression', None), 'name'):
            salida.append(('-' if campo.descending else '') + 'F:' + campo.expression.name)
        else:
            salida.append(type(campo).__name__)
    return salida


def _vista(clase, ruta='/api/x/', usuario=None, **kwargs):
    vista = clase(**kwargs)
    request = Request(APIRequestFactory().get(ruta))
    request.user = usuario or _UsuarioPrueba(rol_id=ROL_ADMINISTRADOR)
    vista.request = request
    vista.format_kwarg = None
    vista.kwargs = {}
    vista.action = 'list'
    return vista


class ExpresionesOrdenTestCase(SimpleTestCase):
    def test_estados_terminados_cubren_los_terminales_del_flujo_juridico(self):
        normalizados = {normalizar_texto_juridico(n) for n in ESTADOS_PETICION_TERMINADOS}
        self.assertTrue(ESTADOS_TERMINALES_NORM.issubset(normalizados))
        self.assertNotIn('atendida', normalizados)
        self.assertNotIn('en tratamiento', normalizados)

    def test_expresiones_son_case(self):
        self.assertIsInstance(orden_prioridad(), Case)
        self.assertIsInstance(orden_peticion_terminada(), Case)
        self.assertIsInstance(orden_valor_primero('estado', 'PENDIENTE'), Case)

    def test_ordenar_peticiones_termina_en_fecha_e_id(self):
        from peticiones.models import Peticiones
        qs = ordenar_peticiones(Peticiones.objects.all())
        self.assertEqual(_campos(qs), ['Case', 'Case', '-fecha', '-id_peticion'])


class OrdenVistasOperativasTestCase(SimpleTestCase):
    def test_listar_peticiones_abiertas_urgentes_y_recientes(self):
        from peticiones.api.views import ListarPeticionesView
        qs = _vista(ListarPeticionesView).get_queryset()
        self.assertEqual(_campos(qs), ['Case', 'Case', '-fecha', '-id_peticion'])

    def test_listar_peticiones_conserva_filtros(self):
        from peticiones.api.views import ListarPeticionesView
        qs = _vista(ListarPeticionesView, '/api/peticiones/listar/?prioridad=Alta').get_queryset()
        self.assertIn('prioridad', str(qs.query.where))
        self.assertEqual(_campos(qs)[-2:], ['-fecha', '-id_peticion'])

    def test_reporte_peticiones_super_admin(self):
        from users.api.views import SuperAdminReportePeticionesView
        qs = _vista(SuperAdminReportePeticionesView).get_queryset()
        self.assertEqual(_campos(qs), ['Case', 'Case', '-fecha', '-id_peticion'])

    def test_reporte_atendidas_sigue_cronologico(self):
        from users.api.views import SuperAdminReporteAtendidasView
        qs = _vista(SuperAdminReporteAtendidasView).get_queryset()
        self.assertEqual(_campos(qs), ['-fecha', '-id_peticion'])

    def test_postulaciones_pendientes_primero(self):
        from voluntariado.api.views import PostulacionVoluntariadoViewSet
        qs = _vista(PostulacionVoluntariadoViewSet).get_queryset()
        self.assertEqual(_campos(qs), ['Case', '-fecha_postulacion', '-id'])

    def test_alertas_duplicados_pendientes_primero(self):
        from peticiones.services.alertas_duplicados_service import listar_alertas_duplicados
        qs = listar_alertas_duplicados()
        self.assertEqual(_campos(qs), ['Case', '-score_combinado', '-fecha_creacion', '-id_alerta'])

    def test_conflictos_kit_sin_resolver_primero(self):
        from inventario.api.kit_consultas import construir_queryset_conflictos
        qs = construir_queryset_conflictos({})
        self.assertEqual(_campos(qs), ['Case', '-fecha_consumo', '-id_consumo'])

    def test_adopciones_disponibles_primero(self):
        from adopciones.api.views import AdopcionAnimalViewSet
        qs = _vista(AdopcionAnimalViewSet).get_queryset()
        self.assertEqual(_campos(qs), ['-disponible_adopcion', '-F:fecha_ingreso', '-id_animal'])


class OrdenCronologicoTestCase(SimpleTestCase):
    def test_registros_del_mas_reciente_al_mas_antiguo(self):
        from clinica.api.views import ConsultaViewSet, HistoriaClinicaViewSet
        from inventario.api.views import CompraViewSet, SalidasViewSet
        from voluntariado.api.views import EventoVoluntariadoViewSet
        casos = {
            HistoriaClinicaViewSet: ['-fecha_apertura', '-id_historia'],
            ConsultaViewSet: ['-fecha_hora', '-id_consulta'],
            CompraViewSet: ['-fecha', '-id_compra'],
            SalidasViewSet: ['-fecha', '-id_salida'],
            EventoVoluntariadoViewSet: ['-fecha', '-id'],
        }
        for clase, esperado in casos.items():
            with self.subTest(vista=clase.__name__):
                self.assertEqual(_campos(_vista(clase).get_queryset()), esperado)

    def test_examenes_de_una_consulta_cronologicos(self):
        from examenes_clinicos.api.views import ExamenViewSet
        qs = _vista(ExamenViewSet, '/api/x/?id_consulta=5').get_queryset()
        self.assertEqual(_campos(qs), ['-fecha_realizacion', '-id_examen'])


class OrdenCatalogosTestCase(SimpleTestCase):
    def test_catalogos_alfabeticos(self):
        from especies.api.views import EspecieViewSet, RazaViewSet
        from examenes_clinicos.api.views import ExamenViewSet
        from inventario.api.views import ProveedoresViewSet
        from patologias.api.views import PatologiaViewSet
        from users.api.views import RolViewSet, SedeViewSet, UsuariosViewSet
        casos = {
            EspecieViewSet: ['nombre', 'id_especie'],
            RazaViewSet: ['nombre', 'id_raza'],
            ExamenViewSet: ['nombre_tipo_examen', 'id_examen'],
            ProveedoresViewSet: ['nombre', 'id_proveedor'],
            PatologiaViewSet: ['nombre', 'id_patologia'],
            RolViewSet: ['nombre_rol', 'id_rol'],
            SedeViewSet: ['nombre', 'id_sede'],
            UsuariosViewSet: ['nombre', 'apellido', 'id_usuario'],
        }
        for clase, esperado in casos.items():
            with self.subTest(vista=clase.__name__):
                self.assertEqual(_campos(_vista(clase).get_queryset()), esperado)

    def test_medicamentos_orden_por_defecto_y_parametro_ordering(self):
        from rest_framework.filters import OrderingFilter
        from medicamentos.api.views import MedicamentosViewSet
        vista = _vista(MedicamentosViewSet)
        qs = OrderingFilter().filter_queryset(vista.request, vista.get_queryset(), vista)
        self.assertEqual(_campos(qs), ['nombre', 'id_medicamento'])
        vista = _vista(MedicamentosViewSet, '/api/x/?ordering=-tipo')
        qs = OrderingFilter().filter_queryset(vista.request, vista.get_queryset(), vista)
        self.assertEqual(_campos(qs), ['-tipo'])
