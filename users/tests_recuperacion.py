"""
Pruebas de la recuperación de contraseña (users/views.py + users/services/recuperacion_service.py).

Cubren: solicitud válida, correo no registrado, código correcto/incorrecto/vencido/reutilizado,
exceso de intentos y de solicitudes, cambio exitoso (login con la nueva contraseña), uso único,
concurrencia, almacenamiento no compartido (503), aislamiento entre cuentas y el contrato JSON
que consumen Angular (inicio-de-sesion-administrador) y Android (RecuperarContrasenia.kt).

Las vistas se invocan con APIRequestFactory (sin resolver URLs) y el envío de correo se simula:
ninguna prueba contacta servicios externos.
"""

import json
import re
import threading
from unittest.mock import MagicMock, patch

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory

from users.api.views import LoginView
from users.models import Rol, Usuarios
from users.services import recuperacion_service as rs
from users.views import confirmar_recuperacion, solicitar_recuperacion

ENVIO = 'users.services.recuperacion_service.enviar_correo_transaccional_brevo'
PASSWORD_ORIGINAL = 'Original123'
PASSWORD_NUEVA = 'NuevaClave2026'


class _CapturaCorreos:
    """Simula Brevo y guarda los códigos enviados (sin red)."""

    def __init__(self, exito=True):
        self.exito = exito
        self.envios = []
        self._lock = threading.Lock()

    def __call__(self, destinatario_email, destinatario_nombre, asunto, contenido_html):
        codigo = re.search(r'<strong>(\d{6})</strong>', contenido_html).group(1)
        with self._lock:
            self.envios.append({'email': destinatario_email, 'codigo': codigo})
        return {'exito': True, 'data': {}} if self.exito else {'exito': False, 'error': 'detalle interno'}

    @property
    def ultimo_codigo(self):
        return self.envios[-1]['codigo']


def _post(vista, datos, ip='10.0.0.1'):
    factory = APIRequestFactory()
    # JSON con escapes ASCII (como lo envía cualquier cliente), incluso para caracteres inválidos.
    cuerpo = json.dumps(datos, ensure_ascii=True)
    request = factory.post('/api/usuarios/x/', cuerpo, content_type='application/json', HTTP_X_REAL_IP=ip)
    return vista(request)


def _limpiar_cache():
    caches[rs.ALIAS_CACHE].clear()


# Las pruebas SIEMPRE usan memoria local: aunque el entorno tenga REDIS_URL/CHANNEL_REDIS_URL,
# clear() nunca puede vaciar un Redis real (FLUSHDB).
AISLAR_CACHE = override_settings(
    CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'pruebas-default'},
        rs.ALIAS_CACHE: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                         'LOCATION': 'pruebas-recuperacion'},
    },
    RECUPERACION_CACHE_COMPARTIDA=False,
    RECUPERACION_EXIGIR_CACHE_COMPARTIDA=False,
)


@AISLAR_CACHE
class RecuperacionBaseTestCase(TestCase):
    def setUp(self):
        _limpiar_cache()
        self.rol, _ = Rol.objects.get_or_create(id_rol=4, defaults={'nombre_rol': 'Peticionario'})
        self.usuario = Usuarios.objects.create(
            id_rol=self.rol, nombre='Ana', apellido='QA', email='ana.recuperacion@vetericano.test',
            password=make_password(PASSWORD_ORIGINAL), activo=True,
        )
        self.correo = _CapturaCorreos()
        parche = patch(ENVIO, side_effect=self.correo)
        self.mock_envio = parche.start()
        self.addCleanup(parche.stop)
        self.addCleanup(_limpiar_cache)

    def solicitar(self, email=None, ip='10.0.0.1'):
        return _post(solicitar_recuperacion, {'email': email or self.usuario.email}, ip=ip)

    def confirmar(self, codigo, password=PASSWORD_NUEVA, email=None, ip='10.0.0.1'):
        return _post(
            confirmar_recuperacion,
            {'email': email or self.usuario.email, 'codigo': codigo, 'nueva_password': password},
            ip=ip,
        )

    def password_actual(self, usuario=None):
        return Usuarios.objects.get(pk=(usuario or self.usuario).pk).password


class SolicitudRecuperacionTests(RecuperacionBaseTestCase):
    def test_01_solicitud_valida_envia_un_codigo_seguro(self):
        resp = self.solicitar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'mensaje': rs.MENSAJE_SOLICITUD})
        self.assertEqual(len(self.correo.envios), 1)
        self.assertEqual(self.correo.envios[0]['email'], self.usuario.email)
        self.assertRegex(self.correo.ultimo_codigo, r'^\d{6}$')

    def test_01b_el_codigo_no_se_guarda_en_texto_plano(self):
        self.solicitar()
        registro = caches[rs.ALIAS_CACHE].get(rs._clave('codigo', rs.normalizar_email(self.usuario.email)))
        self.assertIsInstance(registro, dict)
        self.assertEqual(registro['id_usuario'], self.usuario.pk)
        self.assertNotIn(self.correo.ultimo_codigo, repr(registro))

    def test_02_correo_no_registrado_responde_igual_y_no_envia(self):
        resp = self.solicitar(email='nadie@vetericano.test')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'mensaje': rs.MENSAJE_SOLICITUD})
        self.assertEqual(self.correo.envios, [])

    def test_02b_cuenta_inactiva_no_recibe_codigo(self):
        Usuarios.objects.filter(pk=self.usuario.pk).update(activo=False)
        resp = self.solicitar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self.correo.envios, [])

    def test_02c_fallo_del_proveedor_no_expone_detalles(self):
        self.correo.exito = False
        resp = self.solicitar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'mensaje': rs.MENSAJE_SOLICITUD})
        self.assertNotIn('detalle', resp.data)
        # El código del envío fallido no queda utilizable.
        self.assertEqual(self.confirmar(self.correo.ultimo_codigo).status_code, status.HTTP_400_BAD_REQUEST)

    def test_08_exceso_de_solicitudes_por_correo(self):
        respuestas = []
        for _ in range(rs.MAX_SOLICITUDES_POR_CORREO + 1):
            caches[rs.ALIAS_CACHE].delete(rs._clave('espera', rs.normalizar_email(self.usuario.email)))
            respuestas.append(self.solicitar().status_code)
        self.assertEqual(respuestas[:-1], [200] * rs.MAX_SOLICITUDES_POR_CORREO)
        self.assertEqual(respuestas[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_08b_limite_por_correo_igual_para_correo_inexistente(self):
        codigos = [self.solicitar(email='nadie@vetericano.test').status_code
                   for _ in range(rs.MAX_SOLICITUDES_POR_CORREO + 1)]
        self.assertEqual(codigos[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_08c_exceso_de_solicitudes_por_ip(self):
        codigos = [self.solicitar(email=f'otro{i}@vetericano.test', ip='10.9.9.9').status_code
                   for i in range(rs.MAX_SOLICITUDES_POR_IP + 1)]
        self.assertEqual(codigos[-1], status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(self.solicitar(ip='10.8.8.8').status_code, status.HTTP_200_OK)

    def test_08d_espera_minima_no_reenvia_codigos(self):
        self.solicitar()
        primero = self.correo.ultimo_codigo
        resp = self.solicitar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.correo.envios), 1)
        # El primer código sigue siendo válido.
        self.assertEqual(self.confirmar(primero).status_code, status.HTTP_200_OK)


class ConfirmacionRecuperacionTests(RecuperacionBaseTestCase):
    def _codigo(self):
        self.solicitar()
        return self.correo.ultimo_codigo

    def test_03_y_09_codigo_correcto_cambia_password_y_permite_login(self):
        resp = self.confirmar(self._codigo())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'mensaje': rs.MENSAJE_CONFIRMACION})
        self.assertTrue(check_password(PASSWORD_NUEVA, self.password_actual()))
        login = LoginView.as_view()(APIRequestFactory().post(
            '/api/usuarios/login/', {'email': self.usuario.email, 'password': PASSWORD_NUEVA}, format='json'))
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_04_codigo_incorrecto(self):
        codigo = self._codigo()
        incorrecto = '000000' if codigo != '000000' else '111111'
        resp = self.confirmar(incorrecto)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data, {'error': rs.ERROR_CODIGO_INVALIDO})
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual()))

    def test_05_codigo_vencido(self):
        codigo = self._codigo()
        import time as time_real
        reloj = MagicMock()
        reloj.time.return_value = time_real.time() + rs.CODIGO_TTL_SEGUNDOS + 5
        # LocMem calcula expiraciones en base.py y las comprueba en locmem.py: se adelantan ambos.
        with patch('django.core.cache.backends.locmem.time', reloj), \
                patch('django.core.cache.backends.base.time', reloj):
            resp = self.confirmar(codigo)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual()))

    def test_06_y_10_codigo_reutilizado_tras_el_cambio(self):
        codigo = self._codigo()
        self.assertEqual(self.confirmar(codigo).status_code, status.HTTP_200_OK)
        resp = self.confirmar(codigo, password='OtraClave999')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(check_password(PASSWORD_NUEVA, self.password_actual()))

    def test_06b_codigo_anterior_queda_invalido_al_pedir_otro(self):
        viejo = self._codigo()
        caches[rs.ALIAS_CACHE].delete(rs._clave('espera', rs.normalizar_email(self.usuario.email)))
        self.solicitar()
        nuevo = self.correo.ultimo_codigo
        if viejo != nuevo:
            self.assertEqual(self.confirmar(viejo).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.confirmar(nuevo).status_code, status.HTTP_200_OK)

    def test_07_exceso_de_intentos_bloquea_e_invalida_el_codigo(self):
        codigo = self._codigo()
        incorrecto = '000000' if codigo != '000000' else '111111'
        estados = [self.confirmar(incorrecto).status_code for _ in range(rs.MAX_INTENTOS_FALLIDOS)]
        self.assertEqual(estados[:-1], [400] * (rs.MAX_INTENTOS_FALLIDOS - 1))
        self.assertEqual(estados[-1], status.HTTP_429_TOO_MANY_REQUESTS)
        # Ni el código correcto funciona durante el bloqueo, ni se emiten códigos nuevos.
        resp = self.confirmar(codigo)
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('error', resp.data)
        enviados = len(self.correo.envios)
        caches[rs.ALIAS_CACHE].delete(rs._clave('espera', rs.normalizar_email(self.usuario.email)))
        self.assertEqual(self.solicitar().status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.correo.envios), enviados)
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual()))

    def test_07b_exceso_de_confirmaciones_por_ip(self):
        estados = [self.confirmar('123456', email=f'x{i}@vetericano.test', ip='10.7.7.7').status_code
                   for i in range(rs.MAX_CONFIRMACIONES_POR_IP + 1)]
        self.assertEqual(estados[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_politica_de_password_no_gasta_intentos(self):
        codigo = self._codigo()
        for debil in ('corta1A', 'sinmayusculas1'):
            resp = self.confirmar(codigo, password=debil)
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('error', resp.data)
        self.assertEqual(self.confirmar(codigo).status_code, status.HTTP_200_OK)

    def test_password_se_recorta_como_en_el_registro(self):
        resp = self.confirmar(self._codigo(), password=' ConEspacios9 ')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(check_password('ConEspacios9', self.password_actual()))

    def test_correo_malformado_da_400_no_503(self):
        for vista, datos in (
            (solicitar_recuperacion, {'email': '\ud800a@x.com'}),
            (solicitar_recuperacion, {'email': 'no-es-un-correo'}),
            (confirmar_recuperacion, {'email': '\ud800a@x.com', 'codigo': '123456',
                                      'nueva_password': PASSWORD_NUEVA}),
        ):
            resp = _post(vista, datos)
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, datos)
            self.assertIn('error', resp.data)

    def test_tope_diario_de_solicitudes_por_correo(self):
        cache = caches[rs.ALIAS_CACHE]
        email_n = rs.normalizar_email(self.usuario.email)
        estados = []
        for _ in range(rs.MAX_SOLICITUDES_POR_CORREO_DIA + 1):
            # Simula que pasaron la ventana de 15 minutos y la espera entre códigos.
            cache.delete(rs._clave('solicitudes-correo', email_n))
            cache.delete(rs._clave('espera', email_n))
            estados.append(self.solicitar().status_code)
        self.assertEqual(estados[:-1], [200] * rs.MAX_SOLICITUDES_POR_CORREO_DIA)
        self.assertEqual(estados[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_codigo_de_una_cuenta_no_cambia_otra(self):
        otro = Usuarios.objects.create(
            id_rol=self.rol, nombre='Beto', email='beto.recuperacion@vetericano.test',
            password=make_password(PASSWORD_ORIGINAL), activo=True,
        )
        codigo = self._codigo()
        resp = self.confirmar(codigo, email=otro.email)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual(otro)))
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual()))

    def test_correos_que_solo_difieren_en_mayusculas_cambian_una_sola_cuenta(self):
        gemela = Usuarios.objects.create(
            id_rol=self.rol, nombre='Gemela', email=self.usuario.email.upper(),
            password=make_password(PASSWORD_ORIGINAL), activo=True,
        )
        resp = self.confirmar(self._codigo())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(check_password(PASSWORD_NUEVA, self.password_actual()))
        self.assertTrue(check_password(PASSWORD_ORIGINAL, self.password_actual(gemela)))

    def test_13_contrato_json_para_angular_y_android(self):
        vacio = _post(confirmar_recuperacion, {})
        self.assertEqual(vacio.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(vacio.data, {'error': rs.ERROR_CAMPOS_REQUERIDOS})
        sin_email = _post(solicitar_recuperacion, {})
        self.assertEqual(sin_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(sin_email.data, {'error': rs.ERROR_EMAIL_REQUERIDO})
        # Tipos no textuales y cuerpos que no son objeto JSON: 400 con JSON, nunca 500.
        numerico = _post(confirmar_recuperacion, {'email': self.usuario.email, 'codigo': 123456,
                                                  'nueva_password': PASSWORD_NUEVA})
        self.assertEqual(numerico.status_code, status.HTTP_400_BAD_REQUEST)
        lista = _post(solicitar_recuperacion, ['no-es-un-objeto'])
        self.assertEqual(lista.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', lista.data)


@override_settings(RECUPERACION_EXIGIR_CACHE_COMPARTIDA=True, RECUPERACION_CACHE_COMPARTIDA=False)
@AISLAR_CACHE
class AlmacenamientoNoCompartidoTests(SimpleTestCase):
    """Caso Railway sin Redis: se niega con 503 en vez de fallar al azar entre workers."""

    def test_12_sin_cache_compartida_responde_503(self):
        for vista, datos in (
            (solicitar_recuperacion, {'email': 'ana@vetericano.test'}),
            (confirmar_recuperacion, {'email': 'ana@vetericano.test', 'codigo': '123456',
                                      'nueva_password': PASSWORD_NUEVA}),
        ):
            resp = _post(vista, datos)
            self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertEqual(resp.data, {'error': rs.ERROR_NO_DISPONIBLE})

    @override_settings(RECUPERACION_EXIGIR_CACHE_COMPARTIDA=False)
    def test_redis_caido_responde_503_json(self):
        roto = MagicMock()
        roto.add.side_effect = ConnectionError('redis no responde')
        with patch('users.services.recuperacion_service.caches', {rs.ALIAS_CACHE: roto}):
            resp = _post(solicitar_recuperacion, {'email': 'ana@vetericano.test'})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(resp.data, {'error': rs.ERROR_NO_DISPONIBLE})


@AISLAR_CACHE
class ConcurrenciaRecuperacionTests(SimpleTestCase):
    """Hilos reales sobre el mismo almacenamiento (sin BD: cuenta y actualización simuladas)."""

    def setUp(self):
        _limpiar_cache()
        self.addCleanup(_limpiar_cache)
        self.cuenta = MagicMock(id_usuario=77, email='conc@vetericano.test', nombre='Conc')

    def _en_paralelo(self, funcion, n=8):
        barrera = threading.Barrier(n)
        resultados = []
        lock = threading.Lock()

        def tarea():
            barrera.wait()
            r = funcion()
            with lock:
                resultados.append(r)

        hilos = [threading.Thread(target=tarea) for _ in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        return resultados

    def test_11_solicitudes_simultaneas_emiten_un_solo_codigo(self):
        correo = _CapturaCorreos()
        with patch(ENVIO, side_effect=correo), \
                patch.object(rs, '_cuenta_para_recuperar', return_value=self.cuenta):
            resultados = self._en_paralelo(lambda: rs.solicitar_codigo(self.cuenta.email, ip='10.1.1.1'), n=3)
        self.assertEqual([r.estado_http for r in resultados], [200] * 3)
        self.assertEqual(len(correo.envios), 1)

    def test_11b_confirmaciones_simultaneas_solo_una_cambia_la_password(self):
        correo = _CapturaCorreos()
        actualizaciones = []
        with patch(ENVIO, side_effect=correo), \
                patch.object(rs, '_cuenta_para_recuperar', return_value=self.cuenta), \
                patch.object(rs, '_actualizar_password',
                             side_effect=lambda id_usuario, pwd: actualizaciones.append(id_usuario) or True):
            rs.solicitar_codigo(self.cuenta.email, ip='10.1.1.1')
            codigo = correo.ultimo_codigo
            resultados = self._en_paralelo(
                lambda: rs.confirmar_codigo(self.cuenta.email, codigo, PASSWORD_NUEVA, ip='10.1.1.2'))
        estados = sorted(r.estado_http for r in resultados)
        self.assertEqual(estados.count(200), 1, estados)
        self.assertEqual(actualizaciones, [77])
        # Las demás llegan con el código ya consumido: cuentan como intentos fallidos (400) y,
        # al superar el máximo, bloquean temporalmente (429). Nunca 5xx.
        self.assertTrue(set(estados) <= {200, 400, 429}, estados)

    def test_11c_rafaga_tras_4_fallos_no_supera_el_maximo_de_intentos(self):
        """Tras 4 fallos, una ráfaga concurrente de conjeturas solo puede evaluar el código UNA vez más."""
        correo = _CapturaCorreos()
        actualizaciones = []
        original = rs._hash_codigo
        evaluaciones = []

        def contar(*args):
            evaluaciones.append(1)
            return original(*args)

        with patch(ENVIO, side_effect=correo),                 patch.object(rs, '_cuenta_para_recuperar', return_value=self.cuenta),                 patch.object(rs, '_actualizar_password',
                             side_effect=lambda id_usuario, pwd: actualizaciones.append(id_usuario) or True):
            rs.solicitar_codigo(self.cuenta.email, ip='10.4.4.1')
            codigo = correo.ultimo_codigo
            erroneos = [f'{n:06d}' for n in range(1, 20) if f'{n:06d}' != codigo]
            previos = [rs.confirmar_codigo(self.cuenta.email, erroneos[i], PASSWORD_NUEVA, ip='10.4.4.2').estado_http
                       for i in range(rs.MAX_INTENTOS_FALLIDOS - 1)]
            self.assertEqual(previos, [400] * (rs.MAX_INTENTOS_FALLIDOS - 1))
            rafaga = erroneos[4:12]
            indice = iter(range(len(rafaga)))
            lock = threading.Lock()

            def una():
                with lock:
                    i = next(indice)
                return rs.confirmar_codigo(self.cuenta.email, rafaga[i], PASSWORD_NUEVA, ip='10.4.4.3')

            with patch.object(rs, '_hash_codigo', side_effect=contar):
                resultados = self._en_paralelo(una, n=len(rafaga))
                despues = rs.confirmar_codigo(self.cuenta.email, codigo, PASSWORD_NUEVA, ip='10.4.4.4')
        estados = sorted(r.estado_http for r in resultados)
        # Solo una conjetura de la ráfaga llegó a compararse con el código (el 5.º intento).
        self.assertLessEqual(len(evaluaciones), 1, estados)
        self.assertEqual(set(estados), {429}, estados)
        # Agotados los intentos, ni el código correcto sirve y no se cambió ninguna contraseña.
        self.assertEqual(despues.estado_http, 429)
        self.assertEqual(actualizaciones, [])
