# Guía de operación y despliegue separado en Railway Hobby (`BackendVetericano` + `VetericanoIA`)

Ambos servicios viven dentro del **mismo repositorio Git (`BackendVetericano`)** y se despliegan en Railway Hobby como **dos servicios independientes** en el mismo proyecto y entorno, manteniendo la IA desactivada por defecto (`DUPLICADOS_IA_DINOV2_ACTIVA=False`, umbral visual `0.45` y radio GPS `200.0` m).

---

## 1. Arquitectura y Root Directories en el mismo repositorio

| Servicio en Railway | Root Directory | Dependencias | Arranque | Red |
|---|---|---|---|---|
| **`BackendVetericano`** (Django) | `/` (raíz del repo) | `requirements.txt` (sin PyTorch/Transformers) | `Procfile` (`gunicorn vetericano.wsgi:application --bind 0.0.0.0:$PORT`) | Dominio público |
| **`VetericanoIA`** (FastAPI + DINOv2) | `VetericanoIA` | `VetericanoIA/requirements.txt` (PyTorch CPU + Transformers) | `VetericanoIA/Procfile` (`python -u run_server.py`) | **Solo privada** (sin dominio público) |

- Django gestiona PostgreSQL, Cloudinary, GPS, usuarios, permisos, privacidad de fotos, alertas automáticas (`usar_dinov2=False` al registrar) y respaldo `dHash`.
- `VetericanoIA` escucha con **1 único worker** en IPv6 dual-stack (`::`) usando `PORT` cuando corre en Railway, y en `127.0.0.1:8001` en desarrollo local.

---

## 2. Cómo iniciar ambos servicios

### En desarrollo local (desde `BackendVetericano/`)
1. Iniciar `VetericanoIA`:
   - Configurar `VetericanoIA/.env` a partir de `VetericanoIA/.env.example`.
   - Ejecutar: `.\VetericanoIA\envs\Scripts\python.exe -u .\VetericanoIA\run_server.py`
2. Iniciar `BackendVetericano`:
   - Verificar en `.env` las variables `DUPLICADOS_DINOV2_MICROSERVICIO_URL=http://127.0.0.1:8001` y `DUPLICADOS_DINOV2_MICROSERVICIO_SECRET`.
   - Ejecutar Django normalmente (`.\envs\Scripts\python.exe manage.py runserver`).

### En Railway Hobby (mismo repo GitHub conectado a dos servicios)
1. **Servicio privado `VetericanoIA`:**
   - Conectar al repositorio `BackendVetericano` con **Root Directory = `VetericanoIA`**.
   - **NO asignar dominio público**; mantener Private Networking activo.
   - Cargar las variables de `VetericanoIA/.env.railway.example` (`PORT=8001`, `IA_HOST=::` y `IA_INTERNAL_SHARED_SECRET` aleatorio de 64 caracteres hex).
   - Healthcheck: `/health/` con `healthcheckTimeout: 120` (`VetericanoIA/railway.json`).
2. **Servicio público `BackendVetericano`:**
   - Conectar al mismo repositorio `BackendVetericano` con **Root Directory = `/`**.
   - Mantener intacto `Procfile` (`railway.json` en raíz solo define build/reinicio).
   - Añadir las variables de `.env.railway.example`, manteniendo inicialmente:
     - `DUPLICADOS_IA_DINOV2_ACTIVA=False`
     - `DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=False`
     - `DUPLICADOS_DINOV2_BACKEND=microservicio`
     - `DUPLICADOS_DINOV2_MICROSERVICIO_URL=http://<nombre-servicio-ia>.railway.internal:8001`
     - `DUPLICADOS_DINOV2_MICROSERVICIO_SECRET=<mismo secreto que IA_INTERNAL_SHARED_SECRET>`
     - `DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS=8.0`

---

## 3. Cómo comprobar ambos servicios

1. **Verificar salud y precarga en `VetericanoIA`:**
   - En logs de arranque debe registrarse una sola carga de `facebook/dinov2-small` en `cpu` y escucha en `[::]:8001`.
   - Desde una sesión/shell dentro de `BackendVetericano` en Railway:
     ```bash
     python manage.py shell -c "from peticiones.services.ia_client_service import consultar_salud_microservicio_ia; print(consultar_salud_microservicio_ia())"
     ```
   - Respuesta esperada: `'status_code': 200` y `'modelo_cargado': True`.
2. **Verificar autenticación fail-closed de `/embed/`:**
   - Solicitud sin cabecera `X-Vetericano-IA-Secret` -> HTTP `401`.
   - Solicitud con secreto incorrecto -> HTTP `403`.
3. **Verificar Django y activar progresivamente:**
   - `python manage.py check` -> `0 issues`.
   - Cuando se autorice la migración `0007`: respaldar PostgreSQL y aplicar `python manage.py migrate peticiones 0007`.
   - Luego habilitar `DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=True` y finalmente `DUPLICADOS_IA_DINOV2_ACTIVA=True`, probando `POST /api/peticiones/detectar-duplicados-ciudadano/`.

---

## 4. Cómo revertir de forma segura (< 1 minuto)

### Opción A: Desactivar DINOv2 sin redeploy (reversión inmediata)
- En las variables de `BackendVetericano` en Railway, cambiar:
  - `DUPLICADOS_IA_DINOV2_ACTIVA=False`
- El sistema regresa de inmediato al detector anterior (`dHash` + descripción + GPS) sin perder peticiones ni requerir cambios en Android o Angular.

### Opción B: Apagar o aislar solo `VetericanoIA`
- Si `VetericanoIA` se detiene o devuelve `503`/timeout mientras `DUPLICADOS_DINOV2_FALLBACK_DHASH=True`, `BackendVetericano` captura `ModelDinov2NoDisponibleError` y utiliza `dhash_fallback` automáticamente, sin interrumpir el registro de peticiones.

### Opción C: Volver a inferencia local dentro de Django (`DUPLICADOS_DINOV2_BACKEND=local`)
- Si fuese necesario ejecutar DINOv2 dentro del mismo proceso Django de forma temporal:
  1. Instalar `pip install -r requirements-ia-local.txt` en `BackendVetericano`.
  2. Cambiar la variable `DUPLICADOS_DINOV2_BACKEND=local` en `BackendVetericano`.
