# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Usuario no root con UID/GID fijos
RUN groupadd --gid 10001 --system appuser \
    && useradd --uid 10001 --gid 10001 --system --create-home --shell /usr/sbin/nologin appuser

# Dependencias exclusivas del servicio WebSocket (todas con wheels compatibles en Linux glibc)
COPY requirements-ws.txt .
RUN pip install --no-cache-dir -r requirements-ws.txt \
    && pip check

# Código del backend (filtrado por .dockerignore) con permisos del usuario no root
COPY --chown=10001:10001 . .

USER 10001:10001

# El launcher existente lee PORT desde el entorno de Railway y arranca 1 proceso Uvicorn sin autoreload
CMD ["python", "-m", "realtime.servidor_ws"]
