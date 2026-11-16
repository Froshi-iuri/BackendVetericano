"""
Pruebas del microservicio FastAPI VetericanoIA:
- Autenticación interna por secreto compartido (401 / 403 / 503 fail-closed).
- Validación de imágenes (corruptas, formato inválido, dimensiones mínimas, max_bytes).
- Inferencia real con DINOv2 Small sobre mismoa.png, mismoanimal.png y diferente.png,
  comparando contra los porcentajes de referencia 50.65 %, 23.24 % y 25.11 %.
- Reutilización única del modelo en RAM (no recarga por petición).
- Control de concurrencia y saturación de cola devolviendo HTTP 503.
- Endpoint GET /health/.
"""

from __future__ import annotations

import io
import math
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.config import IASettings, cargar_configuracion
from app.dinov2_engine import Dinov2Engine
from app.main import create_app
from app.security import HEADER_SECRETO_INTERNO

FOTOS_DIR = Path(__file__).resolve().parent.parent.parent / 'scripts' / 'fotos_prueba_dinov2'
FOTO_MISMO_1 = FOTOS_DIR / 'mismoa.png'
FOTO_MISMO_2 = FOTOS_DIR / 'mismoanimal.png'
FOTO_DIFERENTE = FOTOS_DIR / 'diferente.png'


def _crear_bytes_imagen(fmt: str = 'PNG', size: tuple[int, int] = (64, 64), color=(120, 180, 90)) -> bytes:
    buf = io.BytesIO()
    img = Image.new('RGB', size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


def _coseno(v1: list[float], v2: list[float]) -> float:
    return max(0.0, min(1.0, sum(a * b for a, b in zip(v1, v2))))


def _settings_prueba(**overrides) -> IASettings:
    base = dict(
        internal_shared_secret='test-internal-secret-value',
        model_name='facebook/dinov2-small',
        device='cpu',
        max_image_bytes=2 * 1024 * 1024,
        min_dimension_px=16,
        max_image_pixels=16_000_000,
        max_concurrent_inferences=1,
        max_pending_queue=2,
        queue_wait_seconds=1.5,
        preload_model=False,
    )
    base.update(overrides)
    return IASettings(**base)


class _DummyTensor:
    def __init__(self, vec):
        self._vec = list(vec)
        self.ndim = 3

    def __getitem__(self, _idx):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return list(self._vec)


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SeguridadYValidacionVetericanoIATests(unittest.TestCase):
    def test_health_y_autenticacion_interna_fail_closed(self):
        img_bytes = _crear_bytes_imagen('JPEG', (64, 64))

        # Secreto no configurado -> 503 fail-closed
        app_sin_secreto = create_app(custom_settings=_settings_prueba(internal_shared_secret=''))
        with TestClient(app_sin_secreto) as client:
            resp_h = client.get('/health/')
            self.assertEqual(resp_h.status_code, 200)
            resp_e = client.post('/embed/', files={'imagen': ('test.jpg', img_bytes, 'image/jpeg')})
            self.assertEqual(resp_e.status_code, 503)

        app = create_app(custom_settings=_settings_prueba())
        with TestClient(app) as client:
            # Sin cabecera -> 401
            resp_401 = client.post('/embed/', files={'imagen': ('test.jpg', img_bytes, 'image/jpeg')})
            self.assertEqual(resp_401.status_code, 401)

            # Secreto inválido -> 403
            resp_403 = client.post(
                '/embed/',
                headers={HEADER_SECRETO_INTERNO: 'secreto-incorrecto'},
                files={'imagen': ('test.jpg', img_bytes, 'image/jpeg')},
            )
            self.assertEqual(resp_403.status_code, 403)

    def test_rechaza_no_autorizados_antes_de_leer_multipart_y_cupo(self):
        cfg = _settings_prueba(max_image_bytes=32768)
        engine = Dinov2Engine(cfg)
        app = create_app(custom_settings=cfg, custom_engine=engine)
        payload_grande = b'\xff\xd8\xff' + (b'A' * 65536)

        with TestClient(app) as client:
            with patch('app.main.leer_upload_con_limite') as mock_leer:
                with patch.object(engine, 'reservar_cupo', wraps=engine.reservar_cupo) as mock_reservar:
                    r_401 = client.post(
                        '/embed/',
                        files={'imagen': ('grande.jpg', payload_grande, 'image/jpeg')},
                    )
                    r_403 = client.post(
                        '/embed/',
                        headers={HEADER_SECRETO_INTERNO: 'secreto-invalido'},
                        files={'imagen': ('grande.jpg', payload_grande, 'image/jpeg')},
                    )
                    r_413 = client.post(
                        '/embed/',
                        headers={HEADER_SECRETO_INTERNO: 'test-internal-secret-value'},
                        files={'imagen': ('muygrande.jpg', b'B' * 120000, 'image/jpeg')},
                    )

        self.assertEqual(r_401.status_code, 401)
        self.assertEqual(r_403.status_code, 403)
        self.assertEqual(r_413.status_code, 413)
        mock_leer.assert_not_called()
        mock_reservar.assert_not_called()
        self.assertEqual(engine.inflight_count, 0)

    def test_configuracion_local_vs_railway_ipv6_y_port(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg_local = cargar_configuracion()
        self.assertEqual(cfg_local.host, '127.0.0.1')
        self.assertEqual(cfg_local.port, 8001)
        self.assertEqual(cfg_local.queue_wait_seconds, 4.0)

        with patch.dict(
            os.environ,
            {'RAILWAY_ENVIRONMENT': 'production', 'PORT': '9042'},
            clear=True,
        ):
            cfg_railway = cargar_configuracion()
        self.assertEqual(cfg_railway.host, '::')
        self.assertEqual(cfg_railway.port, 9042)

        with patch.dict(
            os.environ,
            {
                'RAILWAY_ENVIRONMENT': 'production',
                'PORT': '9042',
                'IA_HOST': '0.0.0.0',
                'IA_PORT': '8111',
            },
            clear=True,
        ):
            cfg_override = cargar_configuracion()
        self.assertEqual(cfg_override.host, '0.0.0.0')
        self.assertEqual(cfg_override.port, 8111)
        self.assertEqual(cfg_override.torch_num_threads, 1)

        with patch.dict(os.environ, {'IA_TORCH_NUM_THREADS': '2'}, clear=True):
            cfg_t2 = cargar_configuracion()
        self.assertEqual(cfg_t2.torch_num_threads, 2)

        with patch.dict(os.environ, {'IA_TORCH_NUM_THREADS': '64'}, clear=True):
            cfg_tmax = cargar_configuracion()
        self.assertEqual(cfg_tmax.torch_num_threads, 4)

    def test_validaciones_imagen_rechaza_corruptas_formato_dimension_y_tamano(self):
        app = create_app(custom_settings=_settings_prueba(max_image_bytes=32768))
        headers = {HEADER_SECRETO_INTERNO: 'test-internal-secret-value'}

        with TestClient(app) as client:
            # Bytes corruptos -> 400
            r1 = client.post(
                '/embed/',
                headers=headers,
                files={'imagen': ('corrupta.jpg', b'no-es-imagen-valida', 'image/jpeg')},
            )
            self.assertEqual(r1.status_code, 400)

            # Formato BMP no soportado -> 400
            bmp = _crear_bytes_imagen('BMP', (64, 64))
            r2 = client.post(
                '/embed/',
                headers=headers,
                files={'imagen': ('test.bmp', bmp, 'image/bmp')},
            )
            self.assertEqual(r2.status_code, 400)

            # Dimensión inferior a 16x16 -> 400
            tiny = _crear_bytes_imagen('PNG', (8, 8))
            r3 = client.post(
                '/embed/',
                headers=headers,
                files={'imagen': ('tiny.png', tiny, 'image/png')},
            )
            self.assertEqual(r3.status_code, 400)

            # Excede max_bytes -> 413
            grande = b'\xff\xd8\xff' + (b'\x00' * 40000)
            r4 = client.post(
                '/embed/',
                headers=headers,
                files={'imagen': ('big.jpg', grande, 'image/jpeg')},
            )
            self.assertEqual(r4.status_code, 413)

    def test_saturacion_cola_concurrencia_retorna_503_sin_saturar_ram(self):
        cfg = _settings_prueba(
            max_concurrent_inferences=1,
            max_pending_queue=0,
            queue_wait_seconds=0.2,
        )
        engine = Dinov2Engine(cfg)
        iniciado = threading.Event()
        liberar = threading.Event()
        llamadas_carga = 0

        vec = [0.0] * 384
        vec[0] = 1.0

        def fake_cargar():
            nonlocal llamadas_carga
            llamadas_carga += 1
            fake_proc = MagicMock(return_value={'pixel_values': MagicMock()})

            def slow_model(**_kwargs):
                iniciado.set()
                liberar.wait(timeout=3.0)
                return type('Out', (), {'last_hidden_state': _DummyTensor(vec)})()

            return fake_proc, slow_model, type('Torch', (), {'no_grad': _NoGrad}), 'cpu'

        engine.cargar_modelo = fake_cargar  # type: ignore[method-assign]
        app = create_app(custom_settings=cfg, custom_engine=engine)
        headers = {HEADER_SECRETO_INTERNO: 'test-internal-secret-value'}
        img_bytes = _crear_bytes_imagen('PNG', (64, 64))

        resultados = []
        with TestClient(app) as client:
            def _worker():
                r = client.post(
                    '/embed/',
                    headers=headers,
                    files={'imagen': ('ok.png', img_bytes, 'image/png')},
                )
                resultados.append(r.status_code)

            t1 = threading.Thread(target=_worker)
            t1.start()
            self.assertTrue(iniciado.wait(timeout=2.0))

            # Con max_concurrent=1 y max_pending_queue=0, una segunda petición simultánea recibe 503
            r_saturada = client.post(
                '/embed/',
                headers=headers,
                files={'imagen': ('ok2.png', img_bytes, 'image/png')},
            )
            self.assertEqual(r_saturada.status_code, 503)

            liberar.set()
            t1.join(timeout=3.0)
            self.assertEqual(resultados, [200])
            self.assertEqual(llamadas_carga, 1)


@unittest.skipUnless(
    FOTO_MISMO_1.exists() and FOTO_MISMO_2.exists() and FOTO_DIFERENTE.exists(),
    'Requiere fotos reales en scripts/fotos_prueba_dinov2/',
)
class Dinov2RealFastAPITests(unittest.TestCase):
    def test_fotos_reales_reutiliza_modelo_y_reproduce_porcentajes_referencia(self):
        cfg = _settings_prueba(preload_model=True)
        engine = Dinov2Engine(cfg)

        app = create_app(custom_settings=cfg, custom_engine=engine)
        headers = {HEADER_SECRETO_INTERNO: 'test-internal-secret-value'}

        with TestClient(app) as client:
            health = client.get('/health/')
            self.assertEqual(health.status_code, 200)
            hdata = health.json()
            self.assertTrue(hdata['modelo_cargado'])
            self.assertEqual(hdata['dimensiones'], 384)

            # Confirmar que peticiones repetidas NO vuelven a llamar AutoModel.from_pretrained
            with patch('transformers.AutoModel.from_pretrained') as mock_from_pretrained:
                embs = {}
                for nombre, ruta in (
                    ('mismo1', FOTO_MISMO_1),
                    ('mismo2', FOTO_MISMO_2),
                    ('dif', FOTO_DIFERENTE),
                ):
                    r = client.post(
                        '/embed/',
                        headers=headers,
                        files={'imagen': (ruta.name, ruta.read_bytes(), 'image/png')},
                    )
                    self.assertEqual(r.status_code, 200, msg=r.text)
                    data = r.json()
                    self.assertTrue(data['ok'])
                    self.assertEqual(data['dimensiones'], 384)
                    self.assertEqual(len(data['embedding']), 384)
                    self.assertAlmostEqual(
                        math.sqrt(sum(v * v for v in data['embedding'])),
                        1.0,
                        places=4,
                    )
                    embs[nombre] = data['embedding']
                mock_from_pretrained.assert_not_called()

        pct_mismo = round(_coseno(embs['mismo1'], embs['mismo2']) * 100.0, 2)
        pct_dif_1 = round(_coseno(embs['mismo1'], embs['dif']) * 100.0, 2)
        pct_dif_2 = round(_coseno(embs['mismo2'], embs['dif']) * 100.0, 2)

        # Comparación contra porcentajes de referencia (50.65 %, 23.24 %, 25.11 %) dentro de tolerancia
        self.assertAlmostEqual(pct_mismo, 50.65, delta=0.15)
        self.assertAlmostEqual(pct_dif_1, 23.24, delta=0.15)
        self.assertAlmostEqual(pct_dif_2, 25.11, delta=0.15)
        self.assertGreaterEqual(pct_mismo, 45.0)
        self.assertLess(pct_dif_1, 45.0)
        self.assertLess(pct_dif_2, 45.0)
