# Guía de preparación y despliegue económico del Triaje Inteligente (`n8n` Serverless ON + SQLite en Railway)

Esta guía documenta la configuración de `n8n` en Railway usando **Serverless ON (App Sleeping) + SQLite con volumen persistente** para minimizar costos, manteniendo `BackendVetericano` como orquestador resiliente ante arranques en frío y sin modificar `VetericanoIA`, Supabase ni la app Android.

---

## 1. Arquitectura en Railway (proyecto `adventurous-freedom`, entorno `production`)

| Servicio | Estado | Rol | Notas críticas |
|---|---|---|---|
| `VetericanoIA` | Ya desplegado | Embeddings DINOv2 | **No modificar ni redesplegar.** Configurar `Watch Paths = /VetericanoIA/**` antes de mergear `andres -> dev`. |
| `BackendVetericano` | Ya desplegado | API Django + recuperador `triaje_ia` | Permanece despierto (Serverless OFF); despierta a `n8n` mediante webhook HTTPS y reintenta si hay cold start. |
| `vetericano-ws` + `Redis-7khR` | Ya desplegados | Notificaciones en tiempo real | Emiten `triaje_actualizado` y `prioridad_triaje_aceptada` sin cambios. |
| `n8n` *(nuevo servicio)* | Pendiente de autorización | Orquestador bajo demanda | Imagen `n8nio/n8n:2.49.7`, 1 réplica, SQLite en volumen `/home/node/.n8n`, **Serverless ON**. |

---

## 2. Protección de `VetericanoIA` antes de futuros merges a `dev`

`VetericanoIA` y `BackendVetericano` comparten el repositorio `Froshi-iuri/BackendVetericano` en la rama `dev`. Aunque `VetericanoIA` tiene `rootDirectory = /VetericanoIA`, actualmente **no tiene `Watch Paths` configurados**, por lo que Railway dispara rebuilds de `VetericanoIA` ante cada merge a `dev`.

Antes de autorizar cualquier merge `andres -> dev`:
1. En Railway Dashboard → servicio `VetericanoIA` → **Settings → Build → Watch Paths**, agregar:
   ```text
   /VetericanoIA/**
   ```
2. Verificar que en tu rama `andres` no existan cambios bajo `VetericanoIA/`.

---

## 3. Configuración del servicio `n8n` en Railway (Serverless ON + SQLite + Volumen)

1. Crear un servicio vacío en el mismo proyecto (`adventurous-freedom`) y entorno (`production`) con imagen Docker:
   ```text
   n8nio/n8n:2.49.7
   ```
2. Adjuntar **1 volumen persistente** montado en:
   ```text
   /home/node/.n8n
   ```
3. Configurar **1 sola réplica** (`numReplicas = 1`, obligatorio con SQLite para evitar corrupción o locks).
4. Activar **Serverless / App Sleeping (`sleepApplication = true`)**:
   - Railway suspende `n8n` tras **5 a 10 minutos sin tráfico saliente** (cobrando \$0 de RAM/CPU mientras duerme).
   - SQLite en archivo local evita mantener sockets TCP abiertos hacia un Postgres externo.
   - Las variables de [`triaje_ia/n8n.env.railway.example`](../triaje_ia/n8n.env.railway.example) apagan diagnósticos, comprobación de versiones, plantillas y onboarding para que `n8n` no genere tráfico saliente en reposo.
5. Configurar **Healthcheck y reinicio automático**:
   - `Healthcheck Path`: `/healthz/readiness`
   - `Healthcheck Timeout`: `120`
   - `Restart Policy`: `ON_FAILURE` con `Max Retries`: `10`
6. Configurar **Start Command** para garantizar permisos de escritura del volumen al usuario `node` (Railway monta volúmenes como `root` por defecto):
   ```sh
   /bin/sh -c 'test "$(stat -c %U /home/node/.n8n)" = node || chown -R node:node /home/node/.n8n; export HOME=/home/node; exec su -p -s /bin/sh node -c "exec /sbin/tini -- /docker-entrypoint.sh"'
   ```
7. Cargar las variables de [`triaje_ia/n8n.env.railway.example`](../triaje_ia/n8n.env.railway.example) (generando `N8N_ENCRYPTION_KEY` propia y respaldándola en un gestor seguro).
8. Habilitar dominio público HTTPS en `n8n` (necesario para despertar confiablemente al contenedor mediante el Edge Proxy de Railway y acceder al editor web).
9. En el primer arranque, abrir la URL HTTPS de `n8n` y crear inmediatamente la cuenta propietaria (*Owner*).

---

## 4. Importación del workflow y credenciales de privacidad

1. **Privacidad de Gemini para denuncias reales:**
   - En **Free Tier** (fuera de EEE/Reino Unido/Suiza), Google puede usar texto e imágenes enviadas para entrenar modelos y revisarlos humanamente; no existe botón de opt-out en Free Tier.
   - Para denuncias reales completas (texto ciudadano + foto), usar **Paid Tier** vinculando Google Cloud Billing a la API Key de Gemini (o evaluar alternativas sin datos personales/fotos reales).
2. En el `n8n` de Railway, crear únicamente las **3 credenciales** reales (no migrar `ZZ Temp Quota 429 Test Only`):
   - `Vetericano Triaje Webhook Secret` (`Header Auth`, header `X-Vetericano-Triaje-Secret`)
   - `Vetericano Triaje Service Token` (`Header Auth`, header `X-Vetericano-Triaje-Token`)
   - `Google Gemini API`
3. Importar [`triaje_ia/n8n_workflow_triaje_vetericano.sanitized.json`](../triaje_ia/n8n_workflow_triaje_vetericano.sanitized.json):
   - En `Normalize Payload`, mantener `backendBaseUrl = http://backendvetericano.railway.internal:8080` (callbacks internos hacia `BackendVetericano`, que permanece despierto).
   - `Download Primary Photo` consume `/api/triaje-ia/interno/peticiones/<id>/foto-segura/` con `Vetericano Triaje Service Token` (JPEG re-codificado sin EXIF/GPS/XMP).
   - Activar/publicar el workflow.

---

## 5. Variables en `BackendVetericano` y soporte de arranque en frío (`Serverless ON`)

1. Configurar en `BackendVetericano` en Railway:
   - `TRIAJE_N8N_WEBHOOK_URL=https://<tu-dominio-n8n>.up.railway.app/webhook/vetericano-triaje-peticion` (recomendado con Serverless ON para que el Edge Proxy despierte `n8n` de forma fiable).
   - `TRIAJE_N8N_TIMEOUT_SEGUNDOS=20.0` (en hilo asíncrono de fondo; si `n8n` arranca en <20 s entra al primer intento).
   - `TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=30.0` y `TRIAJE_N8N_MAX_REINTENTOS=6` (si el primer intento recibe `502`/timeout mientras despierta `n8n`, el segundo intento corre a los ~30–60 s con `n8n` ya caliente dentro de su ventana activa de 5–10 min).
   - `TRIAJE_GEMINI_ENVIAR_FOTOS=False` (fail-closed: evita enviar fotografías con posibles rostros o placas en píxeles a Gemini; cuando un caso contiene datos confidenciales o PII residual, se omite Gemini y se usa triaje local determinístico con revisión humana).
   - `TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False` (fail-closed: mientras se use Gemini gratuito, solo peticiones ficticias autorizadas internamente en servidor mediante `TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS` o el comando `preparar_prueba_webhook_triaje` salen a Gemini; el texto `[PRUEBA-TRIAJE]` escrito por un ciudadano jamás autoriza Gemini y todas las denuncias reales usan triaje local determinístico con revisión humana hasta activar Paid Tier / Cloud Billing).
   - El resto de variables documentadas en [`.env.railway.example`](../.env.railway.example), manteniendo `TRIAJE_N8N_ACTIVO=False` hasta autorizar activación global.
2. **Compatibilidad con Supabase Pooler (`6543`):** `recuperar_trabajos_interrumpidos` ya no usa `pg_try_advisory_lock` a nivel de sesión; utiliza transacciones cortas por lote con `select_for_update(skip_locked=True)` y reclamo atómico con `despacho_token`, seguro para los 2 workers de Gunicorn sobre Supavisor/PgBouncer (`6543`).
3. Tras autorización, respaldar Supabase si hace falta y verificar migraciones de `triaje_ia`:
   ```bash
   python manage.py migrate triaje_ia
   ```
4. Cambiar `TRIAJE_N8N_ACTIVO=True` en `BackendVetericano` en Railway únicamente cuando se autorice la activación global.

---

## 6. Procedimiento seguro de prueba integral en Railway (con `TRIAJE_N8N_ACTIVO=False`)

1. Crear una petición **ficticia de prueba** cuya descripción incluya el marcador literal `[PRUEBA-TRIAJE]` (sin datos reales ni fotos de terceros).
2. En Railway (`BackendVetericano`), ejecutar el comando seguro que genera un `despacho_token` válido y deja esa única petición ficticia en estado `ENVIADO` / `EN_PROCESO` sin tocar peticiones reales:
   ```bash
   python manage.py preparar_prueba_webhook_triaje --id-peticion <ID_FICTICIO>
   ```
   *(El comando aborta automáticamente si la descripción no contiene `[PRUEBA-TRIAJE]` o si el triaje ya está `COMPLETADO`.)*
3. Enviar por `POST` (desde consola/Postman) el `payload_webhook` impreso por el comando hacia `TRIAJE_N8N_WEBHOOK_URL` con la cabecera `X-Vetericano-Triaje-Secret`.
4. Verificar que:
   - `n8n` despierta en frío (Serverless ON), ejecuta el flujo y envía el callback a `/api/triaje-ia/interno/peticiones/<ID_FICTICIO>/resultado/`.
   - Django acepta el callback gracias al `despacho_token` válido y persiste `TriajePeticion` (`COMPLETADO`) sin modificar `Peticiones.prioridad` ni `Peticiones.asignado_a`.
   - El panel en `localhost:4200` (rama `jhorman`) muestra el triaje y permite aceptar/rechazar recomendaciones.

---

## 7. Frontend Angular (`adminVetericano`) y entorno `localhost:4200`

- **Rama real de integración de Angular:** `Developers`.
- **`localhost:4200` habitual:** `ng serve` sigue usando por defecto `development` (`environment.development.ts` → `https://backendvetericano-production.up.railway.app`) y `-c local` (`127.0.0.1:8000`).
