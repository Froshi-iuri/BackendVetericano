"""
Pruebas de los eventos en tiempo real de inventario y kit (TAREA A del plan WebSockets).

1) SimpleTestCase (sin BD): parchean realtime.notificar.* y el ORM de los servicios. Verifican que cada
   disparador emite el event_type correcto, a los destinatarios correctos, con la categoría correcta
   (NP = notificar_usuarios, AO = alerta_operativa, AS = actualizacion_silenciosa) y la estrictitud
   correcta (raise_on_error). `transaction.Atomic` se neutraliza para no abrir conexiones.
2) TestCase (BD local test_vetericano, vetericano.test_settings): filas reales en notificaciones_sistema
   y atomicidad (fallo inyectado en la NP estricta -> la mutación no queda guardada).

    python manage.py test inventario.tests_notificaciones_realtime --settings=vetericano.test_settings
"""
import uuid
from datetime import date, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from inventario import notificaciones_realtime as notif_rt
from inventario.services import consumo_service, kit_service, solicitudes_service

HOY = date(2026, 11, 17)
GESTORES = [SimpleNamespace(id_usuario=1), SimpleNamespace(id_usuario=2)]
EV_KIT_ALERTA = 'kit.alerta_stock_vencimiento'
EV_BODEGA = 'inventario.alerta_bodega'


def _fila_kit(id_kit_lote, cantidad, fecha=None, med=9):
    return {'id_kit_lote': id_kit_lote, 'id_medicamento': med, 'cantidad_actual': cantidad, 'fecha_vencimiento': fecha}


def _fila_bodega(id_inventario, cantidad, fecha=None, med=9, disponible=True):
    return {
        'id_inventario': id_inventario, 'id_medicamento': med, 'cantidad_actual': cantidad,
        'fecha_vencimiento': fecha, 'disponible_para_asignar': disponible,
    }


class _SinBDMixin:
    """Neutraliza transaction.Atomic y parchea la API realtime.notificar (NP/AO/AS)."""

    def setUp(self):
        super().setUp()
        self._parche_objeto(transaction.Atomic, '__enter__', lambda self_: None)
        self._parche_objeto(transaction.Atomic, '__exit__', lambda self_, *args: False)
        self.np = self._parche('realtime.notificar.notificar_usuarios', return_value=[])
        self.ao = self._parche('realtime.notificar.alerta_operativa')
        self.as_ = self._parche('realtime.notificar.actualizacion_silenciosa')
        self.activos = self._parche('realtime.notificar.usuarios_activos_por_rol', return_value=list(GESTORES))
        self._parche('inventario.notificaciones_realtime._hoy', return_value=HOY)

    def _parche(self, objetivo, **kwargs):
        parche = mock.patch(objetivo, **kwargs)
        simulado = parche.start()
        self.addCleanup(parche.stop)
        return simulado

    def _parche_objeto(self, objeto, atributo, nuevo):
        parche = mock.patch.object(objeto, atributo, nuevo)
        parche.start()
        self.addCleanup(parche.stop)

    def eventos_ao(self):
        return [c.args[0] for c in self.ao.call_args_list]


# ============================================================================
# 1. Umbrales (alineados con inventario/constants.py y consultar_alertas_kit)
# ============================================================================

class UmbralesTests(SimpleTestCase):

    def test_nivel_kit_usa_menor_que_10(self):
        self.assertEqual(notif_rt.nivel_stock_kit(0), 'agotado')
        self.assertEqual(notif_rt.nivel_stock_kit(4), 'critico')
        self.assertEqual(notif_rt.nivel_stock_kit(5), 'bajo')
        self.assertEqual(notif_rt.nivel_stock_kit(9), 'bajo')
        self.assertIsNone(notif_rt.nivel_stock_kit(10))

    def test_nivel_bodega_usa_menor_o_igual_que_10_segun_contrato(self):
        self.assertEqual(notif_rt.nivel_stock_bodega(0), 'agotado')
        self.assertEqual(notif_rt.nivel_stock_bodega(10), 'bajo')
        self.assertIsNone(notif_rt.nivel_stock_bodega(11))

    def test_umbral_vencimiento_inclusivo_30_dias(self):
        self.assertEqual(notif_rt.umbral_vencimiento(HOY - timedelta(days=1), HOY), 'vencido')
        self.assertEqual(notif_rt.umbral_vencimiento(HOY, HOY), 'proximo_a_vencer')
        self.assertEqual(notif_rt.umbral_vencimiento(HOY + timedelta(days=30), HOY), 'proximo_a_vencer')
        self.assertIsNone(notif_rt.umbral_vencimiento(HOY + timedelta(days=31), HOY))
        self.assertIsNone(notif_rt.umbral_vencimiento(None, HOY))

    def test_clave_vencimiento_estable_y_acotada(self):
        candidato = {'ambito': 'kit', 'id_lote': 55, 'umbral': 'vencido', 'fecha_vencimiento': date(2026, 11, 10)}
        self.assertEqual(notif_rt.clave_vencimiento(candidato), 'venc:kit:55:vencido:2026-11-10')
        self.assertEqual(notif_rt.clave_vencimiento(dict(candidato)), notif_rt.clave_vencimiento(candidato))
        bodega = {'ambito': 'bodega', 'id_lote': 100, 'umbral': 'proximo_a_vencer', 'fecha_vencimiento': date(2026, 12, 1)}
        self.assertEqual(notif_rt.clave_vencimiento(bodega), 'venc:bod:100:proximo_a_vencer:2026-12-01')
        self.assertLessEqual(len(notif_rt.clave_vencimiento(bodega)), 150)


# ============================================================================
# 2. KIT-02 AO tras movimiento (cruce de umbral)
# ============================================================================

class Kit02AlertaOperativaTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.filas = self._parche('inventario.notificaciones_realtime._filas_kit')

    def _mover(self, previo, posterior):
        self.filas.side_effect = [previo, posterior]
        foto = notif_rt.capturar_estado_kit(30, [9])
        return notif_rt.alertar_kit_tras_movimiento(30, foto)

    def test_cruce_a_critico_emite_ao_al_vet_y_gestores(self):
        self.assertTrue(self._mover([_fila_kit(55, 12)], [_fila_kit(55, 3)]))
        self.ao.assert_called_once()
        args, kwargs = self.ao.call_args
        self.assertEqual(args[0], EV_KIT_ALERTA)
        self.assertEqual(kwargs['usuarios'], [30])
        self.assertEqual(tuple(kwargs['roles']), (1, 2))
        self.assertEqual(kwargs['nivel'], 'danger')
        self.assertEqual(kwargs['clave_unica'], ('KIT-02', 30))
        self.assertEqual(kwargs['referencia']['id_medicamentos'], [9])
        self.np.assert_not_called()
        self.as_.assert_not_called()

    def test_sin_cruce_no_emite(self):
        self.assertFalse(self._mover([_fila_kit(55, 3)], [_fila_kit(55, 2)]))
        self.ao.assert_not_called()

    def test_asignacion_que_mejora_nivel_no_emite(self):
        self.assertFalse(self._mover([_fila_kit(55, 2)], [_fila_kit(55, 20)]))
        self.ao.assert_not_called()

    def test_lote_nuevo_por_vencer_emite_warning(self):
        self.assertTrue(self._mover([], [_fila_kit(56, 20, HOY + timedelta(days=10))]))
        kwargs = self.ao.call_args.kwargs
        self.assertEqual(kwargs['nivel'], 'warning')
        self.assertEqual(kwargs['referencia']['id_kit_lotes'], [56])

    def test_lote_ya_vencido_no_se_repite(self):
        vencido = _fila_kit(57, 20, HOY - timedelta(days=3))
        self.assertFalse(self._mover([vencido], [dict(vencido, cantidad_actual=19)]))
        self.ao.assert_not_called()

    def test_fallo_de_consulta_es_tolerante(self):
        self.filas.side_effect = DatabaseError('fallo inyectado')
        self.assertIsNone(notif_rt.capturar_estado_kit(30, [9]))
        foto = {'med_ids': [9], 'niveles': {}, 'lotes': {}}
        self.assertIsNone(notif_rt.alertar_kit_tras_movimiento(30, foto))
        self.ao.assert_not_called()

    def test_sin_foto_previa_no_consulta(self):
        self.assertIsNone(notif_rt.alertar_kit_tras_movimiento(30, None))
        self.filas.assert_not_called()


# ============================================================================
# 3. INV-02 AO bodega
# ============================================================================

class Inv02BodegaTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.meds_lotes = self._parche('inventario.notificaciones_realtime._medicamentos_de_lotes', return_value=[9])
        self.filas = self._parche('inventario.notificaciones_realtime._filas_bodega')

    def test_medicamento_en_10_unidades_emite_ao_a_gestores(self):
        self.filas.return_value = [_fila_bodega(100, 10)]
        self.assertTrue(notif_rt.alertar_bodega(inventario_ids=[100]))
        self.ao.assert_called_once()
        args, kwargs = self.ao.call_args
        self.assertEqual(args[0], EV_BODEGA)
        self.assertEqual(tuple(kwargs['roles']), (1, 2))
        self.assertNotIn('usuarios', kwargs)
        self.assertEqual(kwargs['nivel'], 'warning')
        self.assertEqual(kwargs['referencia']['id_medicamentos'], [9])

    def test_stock_suficiente_y_sin_vencimiento_no_emite(self):
        self.filas.return_value = [_fila_bodega(100, 11), _fila_bodega(101, 40, disponible=False)]
        self.assertFalse(notif_rt.alertar_bodega(med_ids=[9], inventario_ids=[100]))
        self.ao.assert_not_called()

    def test_lote_afectado_por_vencer_emite(self):
        self.filas.return_value = [_fila_bodega(100, 50, HOY + timedelta(days=5))]
        self.assertTrue(notif_rt.alertar_bodega(inventario_ids=[100]))
        self.assertEqual(self.ao.call_args.kwargs['referencia']['id_inventarios'], [100])

    def test_agotado_es_danger(self):
        self.filas.return_value = []
        self.assertTrue(notif_rt.alertar_bodega(med_ids=['9']))
        self.assertEqual(self.ao.call_args.kwargs['nivel'], 'danger')

    def test_sin_entradas_no_consulta(self):
        self.assertIsNone(notif_rt.alertar_bodega())
        self.filas.assert_not_called()

    def test_fallo_de_consulta_es_tolerante(self):
        self.filas.side_effect = DatabaseError('fallo inyectado')
        self.assertIsNone(notif_rt.alertar_bodega(med_ids=[9]))
        self.ao.assert_not_called()


class Inv02DisparadoresBodegaTests(_SinBDMixin, SimpleTestCase):
    """Compras, salidas, ajustes/altas y bajas de lotes llaman a INV-02."""

    def setUp(self):
        super().setUp()
        self.alertar = self._parche('inventario.notificaciones_realtime.alertar_bodega')

    def test_inventarios_create_update_destroy(self):
        from inventario.api.views import InventariosViewSet

        vista = InventariosViewSet()
        serializer = mock.Mock()
        serializer.instance = SimpleNamespace(pk=100)
        vista.perform_create(serializer)
        self.alertar.assert_called_with(inventario_ids=[100])
        vista.perform_update(serializer)
        self.assertEqual(serializer.save.call_count, 2)
        self.alertar.assert_called_with(inventario_ids=[100])

        self._parche('inventario.notificaciones_realtime.medicamentos_de_lotes', return_value=[9])
        instancia = mock.Mock(pk=100)
        vista.perform_destroy(instancia)
        instancia.delete.assert_called_once()
        self.alertar.assert_called_with(med_ids=[9])

    def test_compra_serializer_create(self):
        from inventario.api.serializers import CompraSerializer

        for nombre in ('Compra', 'DetalleCompra', 'Inventarios', 'InventarioKardex', 'Proveedores'):
            self._parche(f'inventario.services.compras_service.{nombre}')
        medicamentos_mock = self._parche('inventario.services.compras_service.Medicamentos')
        medicamentos_mock.objects.filter.return_value.first.side_effect = [
            SimpleNamespace(pk=9, activo=True, nombre='Med 9'),
            SimpleNamespace(pk=12, activo=True, nombre='Med 12'),
        ]
        self._parche('inventario.services.compras_service._validar_gestor', return_value=SimpleNamespace(pk=1))
        self._parche('inventario.services.compras_service.notif_rt.alertar_bodega', side_effect=self.alertar)
        serializer = CompraSerializer(context={'request': SimpleNamespace(user=SimpleNamespace(pk=1, is_authenticated=True))})
        serializer.initial_data = {'detalles': [{'id_medicamento': 9, 'cantidad': 5}, {'id_medicamento': 12, 'cantidad': 1}]}
        serializer.create({'id_proveedor': SimpleNamespace(pk=3)})
        self.alertar.assert_called_once_with(med_ids=[9, 12])

    def test_detalle_salida_serializer_create_bloqueado(self):
        from inventario.api.serializers import DetalleSalidaSerializer
        from inventario.services.exceptions import ConflictoOperacionInventarioError

        with self.assertRaises(ConflictoOperacionInventarioError) as ctx:
            DetalleSalidaSerializer().create({})
        self.assertEqual(ctx.exception.codigo, 'SALIDA_LEGACY_BLOQUEADA')
        self.alertar.assert_not_called()


# ============================================================================
# 4. KIT-01 parte NP (gestor -> vet titular), tolerante
# ============================================================================

class Kit01HelpersTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.alertar_kit = self._parche('inventario.notificaciones_realtime.alertar_kit_tras_movimiento')
        self.alertar_bodega = self._parche('inventario.notificaciones_realtime.alertar_bodega')
        self.resultado = [{'id_medicamento': 9, 'cantidad_total_asignada': 5, 'lotes_asignados': [{'id_inventario': 100}]}]

    def test_asignacion_directa_np_tolerante_al_vet(self):
        notif_rt.tras_asignacion_kit(1, 30, self.resultado, 'FOTO')
        self.np.assert_called_once()
        args, kwargs = self.np.call_args
        self.assertEqual(args[0], 'kit.stock_actualizado')
        self.assertEqual(args[1], [30])
        self.assertFalse(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [1])
        self.assertEqual(kwargs['tipo'], 'ASIGNACION_KIT')
        self.assertEqual(kwargs['referencia'], {'id_veterinario': 30, 'id_medicamentos': [9]})
        self.alertar_kit.assert_called_once_with(30, 'FOTO')
        self.alertar_bodega.assert_called_once_with(med_ids=[9], inventario_ids=[100])

    def test_asignacion_por_solicitud_no_duplica_np(self):
        notif_rt.tras_asignacion_kit(1, 30, self.resultado, 'FOTO', id_solicitud=5)
        self.np.assert_not_called()
        self.alertar_kit.assert_called_once_with(30, 'FOTO')
        self.alertar_bodega.assert_called_once()

    def test_retiro_np_tolerante(self):
        notif_rt.tras_retiro_kit(1, 30, {'id_medicamento': 9, 'cantidad_total_retirada': 4}, 'FOTO')
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('kit.stock_actualizado', [30]))
        self.assertEqual(kwargs['tipo'], 'RETIRO_KIT')
        self.assertFalse(kwargs['raise_on_error'])
        self.alertar_kit.assert_called_once_with(30, 'FOTO')

    def test_fallo_de_la_np_no_propaga(self):
        self.np.side_effect = DatabaseError('fallo inyectado')
        notif_rt.tras_asignacion_kit(1, 30, self.resultado, 'FOTO')
        notif_rt.tras_retiro_kit(1, 30, {'id_medicamento': 9, 'cantidad_total_retirada': 4}, 'FOTO')


class AsignarStockServicioTests(_SinBDMixin, SimpleTestCase):
    """kit_service.asignar_stock_a_kit con ORM simulado: KIT-01 NP + KIT-02 AO + INV-02 AO."""

    def setUp(self):
        super().setUp()
        base = 'inventario.services.kit_service.'
        self._parche(base + '_validar_gestor')
        self._parche(base + '_validar_veterinario')
        med = SimpleNamespace(id_medicamento=9, nombre='Medicamento prueba', activo=True)
        medicamentos = self._parche(base + 'Medicamentos')
        medicamentos.objects.filter.return_value.first.return_value = med
        medicamentos.objects.get.return_value = med
        vista = self._parche(base + 'VInventarioGeneralLotes')
        vista.objects.filter.return_value.order_by.return_value = [SimpleNamespace(id_inventario=100, fecha_compra=None)]
        self.inv = SimpleNamespace(id_inventario=100, cantidad_actual=12, estado='Disponible', fecha_vencimiento=None,
                                   save=mock.Mock())
        inventarios = self._parche(base + 'Inventarios')
        inventarios.objects.select_for_update.return_value.filter.return_value.order_by.return_value = [self.inv]
        kit = self._parche(base + 'KitVeterinarioLotes')
        kit.objects.filter.return_value.values_list.return_value = []
        kit.objects.select_for_update.return_value.filter.return_value.filter.return_value.order_by.return_value = []
        kit.objects.create.side_effect = lambda **kw: SimpleNamespace(id_kit_lote=55, **kw)
        self._parche(base + 'InventarioKardex')
        self.filas_kit = self._parche('inventario.notificaciones_realtime._filas_kit')
        self.filas_kit.side_effect = [[], [_fila_kit(55, 5)]]
        self._parche('inventario.notificaciones_realtime._medicamentos_de_lotes', return_value=[9])
        self._parche('inventario.notificaciones_realtime._filas_bodega', return_value=[_fila_bodega(100, 7)])

    def test_asignacion_directa_emite_kit01_kit02_inv02(self):
        resultado = kit_service.asignar_stock_a_kit(1, 30, [{'id_medicamento': 9, 'cantidad': 5}], 'Dotación')
        self.assertEqual(resultado[0]['cantidad_total_asignada'], 5)
        self.assertEqual(self.inv.cantidad_actual, 7)
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('kit.stock_actualizado', [30]))
        self.assertFalse(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [1])
        self.assertEqual(self.eventos_ao(), [EV_KIT_ALERTA, EV_BODEGA])
        kit02 = self.ao.call_args_list[0].kwargs
        self.assertEqual(kit02['usuarios'], [30])
        self.assertEqual(tuple(kit02['roles']), (1, 2))

    def test_fallo_de_la_np_tolerante_no_revierte_la_asignacion(self):
        self.np.side_effect = DatabaseError('fallo inyectado')
        resultado = kit_service.asignar_stock_a_kit(1, 30, [{'id_medicamento': 9, 'cantidad': 5}], 'Dotación')
        self.assertEqual(resultado[0]['lotes_asignados'][0]['stock_restante_bodega'], 7)


class RetiroStockServicioTests(_SinBDMixin, SimpleTestCase):

    def test_retiro_emite_kit01_np_y_kit02_ao(self):
        base = 'inventario.services.kit_service.'
        self._parche(base + '_validar_gestor')
        self._parche(base + '_validar_veterinario')
        medicamentos = self._parche(base + 'Medicamentos')
        medicamentos.objects.filter.return_value.first.return_value = SimpleNamespace(nombre='Med', activo=True)
        kit = self._parche(base + 'KitVeterinarioLotes')
        kit.objects.filter.return_value.values_list.return_value = [100]
        inv = SimpleNamespace(id_inventario=100, cantidad_actual=2, estado='Agotado', save=mock.Mock())
        inventarios = self._parche(base + 'Inventarios')
        inventarios.objects.select_for_update.return_value.filter.return_value.order_by.return_value = [inv]
        kl = SimpleNamespace(id_kit_lote=55, id_inventario_id=100, cantidad_actual=6, fecha_vencimiento=None,
                             fecha_compra_origen=None, actualizado_el=None, save=mock.Mock())
        kit.objects.select_for_update.return_value.filter.return_value.order_by.return_value = [kl]
        self._parche(base + 'InventarioKardex')
        filas = self._parche('inventario.notificaciones_realtime._filas_kit')
        filas.side_effect = [[_fila_kit(55, 6)], [_fila_kit(55, 0)]]

        resultado = kit_service.retirar_stock_de_kit(1, 30, 9, 6, 'Retiro')

        self.assertEqual(resultado['unidades_reintegradas_bodega'], 6)
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1], kwargs['tipo']), ('kit.stock_actualizado', [30], 'RETIRO_KIT'))
        self.assertFalse(kwargs['raise_on_error'])
        self.assertEqual(self.eventos_ao(), [EV_KIT_ALERTA])
        self.assertEqual(self.ao.call_args.kwargs['nivel'], 'danger')


class DevolucionServicioTests(_SinBDMixin, SimpleTestCase):

    def test_procesar_devolucion_evalua_kit02(self):
        base = 'inventario.services.kit_service.'
        self._parche(base + '_validar_gestor')
        detalle = SimpleNamespace(id_detalle_solicitud=1, cantidad_solicitada=2, cantidad_aprobada=0,
                                  id_inventario_origen_id=None, id_medicamento_id=9, es_vencido=False, save=mock.Mock())
        solicitud = mock.MagicMock(tipo_solicitud='DEVOLUCION', estado='PENDIENTE', id_veterinario_id=30,
                                   id_solicitud=5, motivo_solicitud='m', observaciones_gestor=None)
        solicitud.detalles.select_for_update.return_value.order_by.return_value = [detalle]
        solicitudes = self._parche(base + 'SolicitudesKit')
        solicitudes.objects.select_for_update.return_value.filter.return_value.first.return_value = solicitud
        kit = self._parche(base + 'KitVeterinarioLotes')
        kit.objects.filter.return_value.values_list.return_value = [100]
        inv = SimpleNamespace(id_inventario=100, cantidad_actual=0, estado='Agotado', save=mock.Mock())
        inventarios = self._parche(base + 'Inventarios')
        inventarios.objects.select_for_update.return_value.filter.return_value.order_by.return_value = [inv]
        kl = SimpleNamespace(id_kit_lote=55, id_inventario_id=100, id_medicamento_id=9, cantidad_actual=5,
                             fecha_vencimiento=None, fecha_compra_origen=None, actualizado_el=None, save=mock.Mock())
        kit.objects.select_for_update.return_value.filter.return_value.filter.return_value.order_by.return_value = [kl]
        self._parche(base + 'InventarioKardex')
        captura = self._parche('inventario.notificaciones_realtime.capturar_estado_kit', return_value='FOTO')
        alertar = self._parche('inventario.notificaciones_realtime.alertar_kit_tras_movimiento')

        resultado = kit_service.procesar_devolucion_aprobada(1, 5, [{'id_detalle_solicitud': 1, 'cantidad_aprobada': 2}])

        self.assertEqual(resultado['unidades_reintegradas_bodega'], 2)
        captura.assert_called_once_with(30, [9])
        alertar.assert_called_once_with(30, 'FOTO')
        self.np.assert_not_called()  # la NP KIT-04 la emite solicitudes_service.aprobar_solicitud


# ============================================================================
# 5. KIT-06 conflicto de sincronización (consumo offline/clínico)
# ============================================================================

class Kit06ConflictoTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        base = 'inventario.services.consumo_service.'
        self._parche(base + '_validar_veterinario')
        medicamentos = self._parche(base + 'Medicamentos')
        medicamentos.objects.filter.return_value = [SimpleNamespace(id_medicamento=9, nombre='Med')]
        self.registro = self._parche(base + 'InventarioConsumosRegistro')
        self.registro.objects.filter.return_value = []
        self.registro.objects.create.return_value = SimpleNamespace(id_consumo=77)
        kit = self._parche(base + 'KitVeterinarioLotes')
        kit.objects.select_for_update.return_value.filter.return_value.order_by.return_value = []
        self._parche('inventario.notificaciones_realtime._filas_kit', return_value=[])
        self.uuid = uuid.uuid4()
        self.consumos = [{'client_uuid': str(self.uuid), 'id_medicamento': 9, 'unidades': 2, 'id_consulta': 5}]

    def _sincronizar(self, es_offline=True):
        return consumo_service.descontar_consumo_kit(30, self.consumos, 'DIRECTO', None, es_offline=es_offline)

    def test_conflicto_nuevo_np_estricta_a_gestores_y_vet_mas_ao(self):
        resultado = self._sincronizar()
        self.assertEqual(resultado['conflictos'][0]['estado'], 'RECHAZADO_CONFLICTO')
        self.activos.assert_called_once_with((1, 2))
        self.np.assert_called_once()
        args, kwargs = self.np.call_args
        self.assertEqual(args[0], 'kit.conflicto_sincronizacion')
        self.assertEqual(args[1], GESTORES + [30])
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['referencia']['id_consumos'], [77])
        self.assertEqual(self.eventos_ao(), ['kit.conflicto_sincronizacion'])
        ao = self.ao.call_args.kwargs
        self.assertEqual(tuple(ao['roles']), (1, 2))
        self.assertEqual(ao['nivel'], 'danger')

    def test_fallo_de_la_np_estricta_propaga(self):
        self.np.side_effect = DatabaseError('fallo inyectado')
        with self.assertRaises(DatabaseError):
            self._sincronizar()
        self.ao.assert_not_called()

    def test_reintento_idempotente_no_renotifica(self):
        self.registro.objects.filter.return_value = [SimpleNamespace(
            client_uuid=self.uuid, id_veterinario_id=30, id_medicamento_id=9, cantidad_solicitada=2,
            estado='RECHAZADO_CONFLICTO', stock_disponible_al_rechazo=0, motivo_rechazo='m', codigo_http=409,
            id_consumo=77,
        )]
        resultado = self._sincronizar()
        self.assertTrue(resultado['conflictos'][0]['idempotente'])
        self.np.assert_not_called()
        self.ao.assert_not_called()

    def test_online_sin_stock_no_crea_conflicto_ni_np(self):
        from inventario.services.exceptions import StockInsuficienteKitError

        with self.assertRaises(StockInsuficienteKitError):
            self._sincronizar(es_offline=False)
        self.np.assert_not_called()


# ============================================================================
# 6. KIT-03 / KIT-04 / KIT-05 (solicitudes)
# ============================================================================

class SolicitudesKitTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.base = 'inventario.services.solicitudes_service.'
        self.solicitudes = self._parche(self.base + 'SolicitudesKit')

    def _preparar_creacion(self, tipo):
        self._parche(self.base + '_bloquear_y_validar_veterinario')
        self.solicitudes.objects.select_for_update.return_value.filter.return_value.order_by.return_value = []
        self.solicitudes.objects.create.return_value = SimpleNamespace(
            id_solicitud=7, tipo_solicitud=tipo, estado='PENDIENTE', id_veterinario_id=30,
            fecha_solicitud=None, motivo_solicitud='m', es_reposicion_total=False,
        )
        detalles = self._parche(self.base + 'SolicitudesKitDetalles')
        detalles.objects.create.side_effect = lambda **kw: SimpleNamespace(
            id_detalle_solicitud=1, id_medicamento_id=kw['id_medicamento_id'], id_inventario_origen_id=None,
            cantidad_solicitada=kw['cantidad_solicitada'], cantidad_aprobada=0, es_vencido=False,
        )
        return self._parche(self.base + 'Medicamentos')

    def _solicitud_pendiente(self, tipo='REPOSICION', detalles=()):
        sol = mock.MagicMock(estado='PENDIENTE', id_solicitud=7, tipo_solicitud=tipo, id_veterinario_id=30)
        sol.detalles.select_for_update.return_value.order_by.return_value = list(detalles)
        sol.detalles.all.return_value.order_by.return_value = list(detalles)
        self.solicitudes.objects.select_for_update.return_value.filter.return_value.first.return_value = sol
        return sol

    def test_kit03_reposicion_np_estricta_a_gestores(self):
        medicamentos = self._preparar_creacion('REPOSICION')
        medicamentos.objects.filter.return_value.first.return_value = SimpleNamespace(activo=True, nombre='Med')
        solicitudes_service.crear_solicitud_reposicion(30, 'Reposición', items=[{'id_medicamento': 9, 'cantidad': 3}])
        self.activos.assert_called_once_with((1, 2))
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('kit.solicitud_creada', GESTORES))
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [30])
        self.assertEqual(kwargs['referencia']['id_solicitud'], 7)
        self.assertEqual(kwargs['tipo'], 'SOLICITUD_REPOSICION')

    def test_kit03_fallo_np_estricta_propaga(self):
        medicamentos = self._preparar_creacion('REPOSICION')
        medicamentos.objects.filter.return_value.first.return_value = SimpleNamespace(activo=True, nombre='Med')
        self.np.side_effect = DatabaseError('fallo inyectado')
        with self.assertRaises(DatabaseError):
            solicitudes_service.crear_solicitud_reposicion(30, 'Reposición', items=[{'id_medicamento': 9, 'cantidad': 3}])

    def test_kit03_devolucion_np_estricta(self):
        medicamentos = self._preparar_creacion('DEVOLUCION')
        medicamentos.objects.filter.return_value = [SimpleNamespace(id_medicamento=9, nombre='Med')]
        kit = self._parche(self.base + 'KitVeterinarioLotes')
        kit.objects.select_for_update.return_value.filter.return_value.order_by.return_value = [
            SimpleNamespace(id_medicamento_id=9, cantidad_actual=5)
        ]
        solicitudes_service.crear_solicitud_devolucion(30, [{'id_medicamento': 9, 'cantidad': 2}], 'Devolución')
        args, kwargs = self.np.call_args
        self.assertEqual(args[0], 'kit.solicitud_creada')
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['tipo'], 'SOLICITUD_DEVOLUCION')

    def test_kit04_rechazo_np_estricta_al_vet_y_as_a_gestores(self):
        self._parche(self.base + '_validar_gestor')
        self._solicitud_pendiente()
        solicitudes_service.rechazar_solicitud(1, 7, 'Sin stock')
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('kit.solicitud_resuelta', [30]))
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [1])
        self.assertEqual(kwargs['referencia']['estado'], 'RECHAZADA')
        self.as_.assert_called_once()
        self.assertEqual(self.as_.call_args.args[0], 'kit.solicitud_resuelta')
        self.assertEqual(tuple(self.as_.call_args.kwargs['roles']), (1, 2))

    def test_kit04_rechazo_fallo_np_propaga(self):
        self._parche(self.base + '_validar_gestor')
        self._solicitud_pendiente()
        self.np.side_effect = DatabaseError('fallo inyectado')
        with self.assertRaises(DatabaseError):
            solicitudes_service.rechazar_solicitud(1, 7, 'Sin stock')
        self.as_.assert_not_called()

    def test_kit04_aprobar_devolucion_usa_estado_final(self):
        self._parche(self.base + '_validar_gestor')
        self._solicitud_pendiente('DEVOLUCION', [SimpleNamespace(id_detalle_solicitud=1, cantidad_solicitada=3)])
        procesar = self._parche('inventario.services.kit_service.procesar_devolucion_aprobada',
                                return_value={'id_solicitud': 7, 'estado': 'PARCIAL'})
        resultado = solicitudes_service.aprobar_solicitud(1, 7, [{'id_detalle_solicitud': 1, 'cantidad_aprobada': 2}])
        self.assertEqual(resultado, procesar.return_value)
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('kit.solicitud_resuelta', [30]))
        self.assertEqual(kwargs['referencia']['estado'], 'PARCIAL')
        self.assertTrue(kwargs['raise_on_error'])
        self.as_.assert_called_once()

    def test_kit04_aprobar_reposicion(self):
        self._parche(self.base + '_validar_gestor')
        detalle = SimpleNamespace(id_detalle_solicitud=1, cantidad_solicitada=3, cantidad_aprobada=0,
                                  id_medicamento_id=9, es_vencido=False, save=mock.Mock())
        self._solicitud_pendiente('REPOSICION', [detalle])
        asignar = self._parche('inventario.services.kit_service.asignar_stock_a_kit', return_value=[])
        solicitudes_service.aprobar_solicitud(1, 7)
        self.assertEqual(asignar.call_args.kwargs['id_solicitud'], 7)
        args, kwargs = self.np.call_args
        self.assertEqual(args[0], 'kit.solicitud_resuelta')
        self.assertEqual(kwargs['referencia']['estado'], 'APROBADA')

    def test_kit05_cancelar_solo_as(self):
        self._parche(self.base + '_validar_veterinario')
        self._solicitud_pendiente()
        solicitudes_service.cancelar_solicitud(30, 7)
        self.np.assert_not_called()
        self.as_.assert_called_once()
        args, kwargs = self.as_.call_args
        self.assertEqual(args[0], 'kit.solicitud_cancelada')
        self.assertEqual(tuple(kwargs['roles']), (1, 2))
        self.assertEqual(kwargs['referencia']['id_solicitud'], 7)


# ============================================================================
# 7. KIT-02 NP: comando alertas_vencimiento
# ============================================================================

CMD = 'inventario.management.commands.alertas_vencimiento.Command'
CANDIDATO_KIT = {
    'ambito': 'kit', 'id_lote': 55, 'id_inventario': 100, 'id_medicamento': 9, 'nombre_medicamento': 'Med',
    'id_veterinario': 30, 'cantidad': 4, 'fecha_vencimiento': date(2026, 11, 10), 'umbral': 'vencido',
}
CANDIDATO_BODEGA = {
    'ambito': 'bodega', 'id_lote': 101, 'id_inventario': 101, 'id_medicamento': 9, 'nombre_medicamento': 'Med',
    'id_veterinario': None, 'cantidad': 6, 'fecha_vencimiento': date(2026, 11, 27), 'umbral': 'proximo_a_vencer',
}
CLAVE_KIT = 'venc:kit:55:vencido:2026-11-10'
CLAVE_BODEGA = 'venc:bod:101:proximo_a_vencer:2026-11-27'


class ComandoAlertasVencimientoTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self._parche('inventario.notificaciones_realtime.candidatos_vencimiento',
                     return_value=[CANDIDATO_KIT, CANDIDATO_BODEGA])
        self._parche(CMD + '._veterinarios_activos', return_value={30})
        self.existentes = self._parche(CMD + '._existentes', return_value={(1, CLAVE_KIT)})
        self.np.side_effect = lambda event_type, usuarios, *a, **k: list(usuarios)

    def _ejecutar(self, **opciones):
        salida = StringIO()
        call_command('alertas_vencimiento', stdout=salida, stderr=StringIO(), **opciones)
        return salida.getvalue()

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
    def test_crea_np_por_lote_con_dedup(self):
        salida = self._ejecutar()
        self.assertEqual(self.np.call_count, 2)
        kit, bodega = self.np.call_args_list
        self.assertEqual(kit.args[0], EV_KIT_ALERTA)
        self.assertEqual(kit.args[1], [2, 30])  # el gestor 1 ya tenía esa clave
        self.assertEqual(kit.kwargs['clave_dedup'], CLAVE_KIT)
        self.assertFalse(kit.kwargs['raise_on_error'])
        self.assertEqual(kit.kwargs['referencia']['id_kit_lote'], 55)
        self.assertEqual(bodega.args[1], [1, 2])
        self.assertEqual(bodega.kwargs['clave_dedup'], CLAVE_BODEGA)
        self.assertNotIn('id_veterinario', bodega.kwargs['referencia'])
        self.assertIn('Notificaciones creadas: 4', salida)

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
    def test_segunda_ejecucion_es_idempotente(self):
        self.existentes.return_value = {(1, CLAVE_KIT), (2, CLAVE_KIT), (30, CLAVE_KIT), (1, CLAVE_BODEGA), (2, CLAVE_BODEGA)}
        salida = self._ejecutar()
        self.np.assert_not_called()
        self.assertIn('lotes ya notificados: 2', salida)

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
    def test_dry_run_no_escribe(self):
        salida = self._ejecutar(dry_run=True)
        self.np.assert_not_called()
        self.assertIn('[dry-run]', salida)
        self.assertIn('Se crearían 4', salida)

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=False)
    def test_flag_apagado_no_persiste(self):
        salida = self._ejecutar()
        self.np.assert_not_called()
        self.assertIn('NOTIFICACIONES_SISTEMA_HABILITADAS=False', salida)


class CandidatosVencimientoTests(SimpleTestCase):

    def test_filtra_por_umbral_y_ambito(self):
        kl = SimpleNamespace(id_kit_lote=55, id_inventario_id=100, id_medicamento_id=9,
                             id_medicamento=SimpleNamespace(nombre='Med'), id_veterinario_id=30,
                             cantidad_actual=4, fecha_vencimiento=HOY - timedelta(days=1))
        lote = SimpleNamespace(id_inventario=101, id_medicamento_id=9, nombre_medicamento='Med',
                               cantidad_actual=6, fecha_vencimiento=HOY + timedelta(days=30))
        with mock.patch.object(notif_rt, 'KitVeterinarioLotes') as kit, \
                mock.patch.object(notif_rt, 'VInventarioGeneralLotes') as vista:
            kit.objects.filter.return_value.select_related.return_value.order_by.return_value = [kl]
            vista.objects.filter.return_value.order_by.return_value = [lote]
            candidatos = notif_rt.candidatos_vencimiento(HOY)
        self.assertEqual([(c['ambito'], c['umbral']) for c in candidatos],
                         [('kit', 'vencido'), ('bodega', 'proximo_a_vencer')])
        self.assertEqual(kit.objects.filter.call_args.kwargs['fecha_vencimiento__lte'], HOY + timedelta(days=30))
        titulo, mensaje = notif_rt.texto_vencimiento(candidatos[0])
        self.assertEqual(titulo, 'Lote vencido')
        self.assertIn('veterinario #30', mensaje)


# ============================================================================
# 8. Pruebas con BD real (PostgreSQL local test_vetericano)
# ============================================================================

FALLO_NP = 'realtime.models.NotificacionSistema.objects.create'


@override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
class NotificacionesInventarioBDTests(TestCase):
    """Filas reales en notificaciones_sistema y atomicidad. Requieren la BD local de pruebas."""

    def setUp(self):
        from inventario.tests.factories import crear_lote_bodega, crear_medicamento, crear_usuario

        self.hoy = timezone.now().date()
        self.admin = crear_usuario(rol_id=1)
        self.juridico = crear_usuario(rol_id=2)
        self.admin_inactivo = crear_usuario(rol_id=1, activo=False)
        self.vet = crear_usuario(rol_id=3)
        self.med = crear_medicamento()
        self.lote = crear_lote_bodega(self.med, cantidad=12, fecha_vencimiento=self.hoy + timedelta(days=90))

    def _np(self, usuario, event_type):
        from realtime.models import NotificacionSistema

        return NotificacionSistema.objects.filter(id_usuario_destino=usuario, event_type=event_type)

    def _crear_reposicion(self):
        return solicitudes_service.crear_solicitud_reposicion(
            self.vet.pk, 'Reposición de prueba', items=[{'id_medicamento': self.med.pk, 'cantidad': 3}],
        )

    def _solicitud_pendiente(self):
        from inventario.models import SolicitudesKit

        return SolicitudesKit.objects.create(
            tipo_solicitud='REPOSICION', estado='PENDIENTE', id_veterinario=self.vet, motivo_solicitud='m',
        )

    def test_kit03_np_para_gestores_activos(self):
        res = self._crear_reposicion()
        for gestor in (self.admin, self.juridico):
            notif = self._np(gestor, 'kit.solicitud_creada').get()
            self.assertEqual(notif.modulo, 'inventario')
            self.assertEqual(notif.referencia['id_solicitud'], res['id_solicitud'])
        self.assertFalse(self._np(self.admin_inactivo, 'kit.solicitud_creada').exists())
        self.assertFalse(self._np(self.vet, 'kit.solicitud_creada').exists())

    def test_kit03_fallo_np_revierte_la_solicitud(self):
        from inventario.models import SolicitudesKit

        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            with self.assertRaises(DatabaseError):
                self._crear_reposicion()
        self.assertFalse(SolicitudesKit.objects.filter(id_veterinario=self.vet).exists())

    def test_kit04_rechazo_np_al_vet(self):
        sol = self._solicitud_pendiente()
        solicitudes_service.rechazar_solicitud(self.admin.pk, sol.pk, 'Sin stock')
        notif = self._np(self.vet, 'kit.solicitud_resuelta').get()
        self.assertEqual(notif.referencia, {'id_solicitud': sol.pk, 'tipo_solicitud': 'REPOSICION', 'estado': 'RECHAZADA'})
        self.assertFalse(self._np(self.admin, 'kit.solicitud_resuelta').exists())

    def test_kit04_fallo_np_revierte_el_rechazo(self):
        sol = self._solicitud_pendiente()
        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            with self.assertRaises(DatabaseError):
                solicitudes_service.rechazar_solicitud(self.admin.pk, sol.pk, 'Sin stock')
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'PENDIENTE')

    def test_kit01_asignacion_np_al_vet(self):
        kit_service.asignar_stock_a_kit(self.admin.pk, self.vet.pk, [{'id_medicamento': self.med.pk, 'cantidad': 5}], 'Dotación')
        notif = self._np(self.vet, 'kit.stock_actualizado').get()
        self.assertEqual(notif.tipo, 'ASIGNACION_KIT')
        self.assertFalse(self._np(self.admin, 'kit.stock_actualizado').exists())

    def test_kit01_tolerante_fallo_np_no_revierte_la_asignacion(self):
        from inventario.models import KitVeterinarioLotes

        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            kit_service.asignar_stock_a_kit(self.admin.pk, self.vet.pk, [{'id_medicamento': self.med.pk, 'cantidad': 5}], 'Dotación')
        self.assertEqual(KitVeterinarioLotes.objects.get(id_veterinario=self.vet).cantidad_actual, 5)
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 7)
        self.assertFalse(self._np(self.vet, 'kit.stock_actualizado').exists())

    def test_kit02_e_inv02_ao_tras_asignacion(self):
        with mock.patch('realtime.publicar.publicar_evento') as publicar, \
                self.captureOnCommitCallbacks(execute=True):
            kit_service.asignar_stock_a_kit(self.admin.pk, self.vet.pk, [{'id_medicamento': self.med.pk, 'cantidad': 5}], 'Dotación')
        ao = {c.args[0]: c for c in publicar.call_args_list if c.args[1] == 'AO'}
        self.assertIn(EV_KIT_ALERTA, ao)   # kit nuevo con 5 uds -> "bajo" (<10)
        self.assertIn(EV_BODEGA, ao)       # bodega queda en 7 uds -> "≤10"
        self.assertEqual(ao[EV_KIT_ALERTA].kwargs['usuarios'], [self.vet.pk])
        self.assertEqual(sorted(ao[EV_BODEGA].kwargs['roles']), [1, 2])

    def test_kit06_conflicto_offline_np_y_reintento_idempotente(self):
        from rest_framework.test import APIClient

        from inventario.models import InventarioConsumosRegistro
        from inventario.tests.factories import crear_consulta
        from inventario.tests.helpers import obtener_headers_jwt

        consulta = crear_consulta()
        cuuid = uuid.uuid4()
        payload = {'consumos': [{'client_uuid': str(cuuid), 'id_medicamento': self.med.pk, 'unidades': 3,
                                 'id_consulta': consulta.id_consulta}]}
        cliente = APIClient()
        cabeceras = obtener_headers_jwt(self.vet)
        resp = cliente.post('/api/inventario/kit/sincronizar-offline/', payload, format='json', **cabeceras)
        self.assertEqual(resp.status_code, 409)
        registro = InventarioConsumosRegistro.objects.get(client_uuid=cuuid)
        self.assertEqual(registro.estado, 'RECHAZADO_CONFLICTO')
        for usuario in (self.admin, self.juridico, self.vet):
            notif = self._np(usuario, 'kit.conflicto_sincronizacion').get()
            self.assertIn(registro.id_consumo, notif.referencia['id_consumos'])

        resp = cliente.post('/api/inventario/kit/sincronizar-offline/', payload, format='json', **cabeceras)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._np(self.vet, 'kit.conflicto_sincronizacion').count(), 1)

    def test_kit06_fallo_np_no_deja_conflicto_huerfano(self):
        from inventario.models import InventarioConsumosRegistro
        from inventario.tests.factories import crear_consulta

        consulta = crear_consulta()
        cuuid = uuid.uuid4()
        consumos = [{'client_uuid': str(cuuid), 'id_medicamento': self.med.pk, 'unidades': 3,
                     'id_consulta': consulta.id_consulta}]
        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            with self.assertRaises(DatabaseError):
                consumo_service.descontar_consumo_kit(self.vet.pk, consumos, 'DIRECTO', None, es_offline=True)
        self.assertFalse(InventarioConsumosRegistro.objects.filter(client_uuid=cuuid).exists())

    def test_comando_alertas_vencimiento_idempotente_y_dry_run(self):
        from realtime.models import NotificacionSistema
        from inventario.tests.factories import crear_lote_bodega, crear_lote_kit

        origen = crear_lote_bodega(self.med, cantidad=0, fecha_vencimiento=self.hoy + timedelta(days=10), estado='Agotado')
        lote_kit = crear_lote_kit(self.vet, origen, cantidad=4, cantidad_ref=4)
        vencido = crear_lote_bodega(self.med, cantidad=6, fecha_vencimiento=self.hoy - timedelta(days=2))
        clave_kit = f'venc:kit:{lote_kit.pk}:proximo_a_vencer:{lote_kit.fecha_vencimiento.isoformat()}'
        clave_bod = f'venc:bod:{vencido.pk}:vencido:{vencido.fecha_vencimiento.isoformat()}'
        nuestras = NotificacionSistema.objects.filter(clave_dedup__in=[clave_kit, clave_bod])

        call_command('alertas_vencimiento', dry_run=True, stdout=StringIO())
        self.assertFalse(nuestras.exists())

        call_command('alertas_vencimiento', stdout=StringIO())
        self.assertTrue(nuestras.filter(id_usuario_destino=self.vet, clave_dedup=clave_kit).exists())
        self.assertFalse(nuestras.filter(id_usuario_destino=self.vet, clave_dedup=clave_bod).exists())
        for gestor in (self.admin, self.juridico):
            self.assertEqual(nuestras.filter(id_usuario_destino=gestor).count(), 2)
        total = nuestras.count()

        call_command('alertas_vencimiento', stdout=StringIO())
        self.assertEqual(nuestras.count(), total)
