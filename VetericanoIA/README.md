# VetericanoIA

Microservicio FastAPI independiente dentro del repositorio `BackendVetericano` para extracción de embeddings visuales de 384 dimensiones con `facebook/dinov2-small` sobre PyTorch CPU.

## Ejecución local

1. Desde `BackendVetericano/VetericanoIA/`, crear y activar entorno virtual en `envs/`:
   - `python -m venv envs`
   - `.\envs\Scripts\pip install -r requirements.txt`
2. Configurar variables de entorno (copiar `.env.example` a `.env` y definir `IA_INTERNAL_SHARED_SECRET`).
3. Iniciar con un único worker Uvicorn (por defecto escucha en `127.0.0.1:8001`):
   - `.\envs\Scripts\python.exe -u run_server.py`

## Endpoints internos

- `GET /health/`: verifica si `facebook/dinov2-small` quedó cargado en RAM, capacidad de concurrencia y peticiones en vuelo (`200` listo / `503 degraded`).
- `POST /embed/`: recibe `multipart/form-data` (`imagen`) protegido por `X-Vetericano-IA-Secret`. Un middleware valida la credencial, `Content-Length` y cupo disponible **antes** de leer o parsear el payload multipart.

## Despliegue privado en Railway Hobby (monorepo `BackendVetericano`)

- Conectar el mismo repositorio `BackendVetericano` configurando **Root Directory = `VetericanoIA`** y **sin dominio público**.
- Usar `Procfile` (`web: python -u run_server.py`) y `railway.json` (`healthcheckPath: /health/`, `healthcheckTimeout: 120`).
- Cuando se detectan variables de Railway (`RAILWAY_ENVIRONMENT`, `RAILWAY_PROJECT_ID`, etc.), `app/config.py` cambia automáticamente el host por defecto a `::` (dual-stack IPv6 necesario para `*.railway.internal`) y respeta `PORT` cuando `IA_PORT` no está definido.
- Mantener **1 único worker** (`IA_MAX_CONCURRENT_INFERENCES=1`, `IA_MAX_PENDING_QUEUE=4`, `IA_MAX_QUEUE_WAIT_SECONDS=4.0`).

## Comprobación y reversión

- **Comprobar salud:** consultar `GET /health/` esperando `"status": "ok"` y `"modelo_cargado": true`.
- **Comprobar seguridad:** `POST /embed/` sin cabecera `X-Vetericano-IA-Secret` debe devolver `401`; con secreto incorrecto `403`.
- **Revertir sin pérdida de servicio:** en `BackendVetericano`, definir `DUPLICADOS_IA_DINOV2_ACTIVA=False` (o pausar el servicio `VetericanoIA` para activar el respaldo automático por dHash en Django). Más detalles en [`../docs/DESPLIEGUE_RAILWAY_IA.md`](../docs/DESPLIEGUE_RAILWAY_IA.md).
