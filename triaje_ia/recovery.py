from __future__ import annotations

import atexit
import logging
import os
import sys
import threading

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_RECOVERY_LOCK = threading.Lock()
_RECOVERY_THREAD: threading.Thread | None = None
_STARTED_PID: int | None = None
_STOP_EVENT = threading.Event()
_ATEXIT_REGISTERED = False

_COMANDOS_CLI_EXCLUIDOS = frozenset({
    'test',
    'makemigrations',
    'migrate',
    'collectstatic',
    'check',
    'shell',
    'dbshell',
    'procesar_reintentos_triaje',
    'showmigrations',
    'sqlmigrate',
})


def _es_proceso_cli_excluido(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv
    if len(args) >= 2 and args[1] in _COMANDOS_CLI_EXCLUIDOS:
        return True
    # En `manage.py runserver` (sin --noreload), el proceso padre solo vigila archivos;
    # únicamente el proceso hijo (RUN_MAIN='true') debe iniciar el hilo recuperador.
    if 'runserver' in args and '--noreload' not in args:
        if os.environ.get('RUN_MAIN') != 'true':
            return True
    return False


def _ejecutar_ciclo_recuperacion() -> None:
    from triaje_ia.services import recuperar_trabajos_interrumpidos

    try:
        recuperar_trabajos_interrumpidos(limite=20, usar_advisory_lock=True)
    except Exception:
        logger.exception('Error controlado en ciclo automático de recuperación de triaje.')
    finally:
        try:
            connections.close_all()
        except Exception:
            pass


def _bucle_recuperador() -> None:
    if _STOP_EVENT.wait(2.0):
        return
    while not _STOP_EVENT.is_set():
        if getattr(settings, 'TRIAJE_N8N_ACTIVO', False):
            _ejecutar_ciclo_recuperacion()
        try:
            intervalo = float(getattr(settings, 'TRIAJE_RECOVERY_INTERVAL_SEGUNDOS', 30.0))
        except (TypeError, ValueError):
            intervalo = 30.0
        intervalo = max(5.0, min(600.0, intervalo))
        if _STOP_EVENT.wait(intervalo):
            break


def iniciar_recuperador_automatico_si_aplica(*, force: bool = False) -> bool:
    """
    Inicia un único hilo de recuperación periódica por PID cuando
    TRIAJE_N8N_ACTIVO=True y TRIAJE_RECOVERY_IN_PROCESS=True, evitando
    duplicados tras fork() o en procesos CLI/reloader padre.
    """
    global _RECOVERY_THREAD, _STARTED_PID, _ATEXIT_REGISTERED

    if not force:
        if not getattr(settings, 'TRIAJE_N8N_ACTIVO', False):
            return False
        if not getattr(settings, 'TRIAJE_RECOVERY_IN_PROCESS', True):
            return False
        if _es_proceso_cli_excluido():
            return False

    pid_actual = os.getpid()
    with _RECOVERY_LOCK:
        # Si el proceso hizo fork(), limpiar referencia heredada del proceso padre.
        if _STARTED_PID is not None and _STARTED_PID != pid_actual:
            _RECOVERY_THREAD = None
            _STOP_EVENT.clear()

        if _RECOVERY_THREAD is not None and _RECOVERY_THREAD.is_alive():
            return False

        _STOP_EVENT.clear()
        _RECOVERY_THREAD = threading.Thread(
            target=_bucle_recuperador,
            name=f'triaje-ia-recovery-{pid_actual}',
            daemon=True,
        )
        _STARTED_PID = pid_actual
        if not _ATEXIT_REGISTERED:
            atexit.register(detener_recuperador_automatico)
            _ATEXIT_REGISTERED = True
        _RECOVERY_THREAD.start()
        return True


def detener_recuperador_automatico() -> None:
    """Detiene limpiamente el hilo recuperador (útil en pruebas o apagado de workers)."""
    global _RECOVERY_THREAD, _STARTED_PID
    _STOP_EVENT.set()
    with _RECOVERY_LOCK:
        hilo = _RECOVERY_THREAD
        _RECOVERY_THREAD = None
        _STARTED_PID = None
    if hilo is not None and hilo.is_alive():
        hilo.join(timeout=1.5)
