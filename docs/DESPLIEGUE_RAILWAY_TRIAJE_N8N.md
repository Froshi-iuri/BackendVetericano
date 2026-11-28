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
   - El workflow de demostración acepta únicamente escenarios sintéticos autorizados por Django.
2. En el `n8n` de Railway, crear únicamente las **3 credenciales** reales (no migrar `ZZ Temp Quota 429 Test Only`):
   - `Vetericano Triaje Webhook Secret` (`Header Auth`, header `X-Vetericano-Triaje-Secret`)
   - `Vetericano Triaje Service Token` (`Header Auth`, header `X-Vetericano-Triaje-Token`)
   - `Google Gemini API`
3. Actualizar el workflow existente con [`triaje_ia/n8n_workflow_triaje_vetericano.sanitized.json`](../triaje_ia/n8n_workflow_triaje_vetericano.sanitized.json):
   - En `Normalize Payload`, mantener `backendBaseUrl = http://backendvetericano.railway.internal:8080` (callbacks internos hacia `BackendVetericano`, que permanece despierto).
   - `Fetch Gemini Context` y `Download Primary Photo` consultan Django con la credencial de servicio y el token del despacho. El segundo devuelve la ilustración en JPEG sin metadatos.

---

## 5. Variables en `BackendVetericano` y soporte de arranque en frío (`Serverless ON`)

1. Configurar en `BackendVetericano` en Railway:
   - `TRIAJE_N8N_WEBHOOK_URL=https://<tu-dominio-n8n>.up.railway.app/webhook/vetericano-triaje-peticion` (recomendado con Serverless ON para que el Edge Proxy despierte `n8n` de forma fiable).
   - `TRIAJE_N8N_TIMEOUT_SEGUNDOS=20.0` (en hilo asíncrono de fondo; si `n8n` arranca en <20 s entra al primer intento).
   - `TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=30.0` y `TRIAJE_N8N_MAX_REINTENTOS=6` (si el primer intento recibe `502`/timeout mientras despierta `n8n`, el segundo intento corre a los ~30–60 s con `n8n` ya caliente dentro de su ventana activa de 5–10 min).
   - `TRIAJE_GEMINI_ENVIAR_FOTOS=False` por defecto. Para la demo autorizada, aplicar los valores de la sección siguiente.
   - `TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False` con Gemini gratuito. La lista opcional de IDs restringe la autorización del comando a casos concretos.
   - El resto de variables documentadas en [`.env.railway.example`](../.env.railway.example), manteniendo `TRIAJE_N8N_ACTIVO=False` hasta autorizar activación global.
2. **Compatibilidad con Supabase Pooler (`6543`):** `recuperar_trabajos_interrumpidos` ya no usa `pg_try_advisory_lock` a nivel de sesión; utiliza transacciones cortas por lote con `select_for_update(skip_locked=True)` y reclamo atómico con `despacho_token`, seguro para los 2 workers de Gunicorn sobre Supavisor/PgBouncer (`6543`).
3. Tras autorización, respaldar Supabase si hace falta y verificar migraciones de `triaje_ia`:
   ```bash
   python manage.py migrate triaje_ia
   ```
4. Cambiar `TRIAJE_N8N_ACTIVO=True` en `BackendVetericano` en Railway únicamente cuando se autorice la activación global.

---

## 6. Demostración de texto e imagen con Gemini gratuito

Aplicar estos valores en Django tras la autorización.

```dotenv
TRIAJE_N8N_ACTIVO=False
TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False
TRIAJE_GEMINI_ENVIAR_FOTOS=True
TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=
TRIAJE_ENVIAR_URLS_EXTERNAS_N8N=False
TRIAJE_N8N_WEBHOOK_URL=https://n8n-production-ef894373.up.railway.app/webhook/vetericano-triaje-peticion
```

Conservar los valores existentes de `TRIAJE_N8N_WEBHOOK_SECRET` y `TRIAJE_SERVICE_TOKEN` en el gestor de secretos. La lista de IDs puede quedar vacía porque Django exige el comando interno y el contenido exacto del catálogo. Conservar la credencial existente de Google Gemini.

### Publicar después de la autorización

1. Confirmar `/VetericanoIA/**` en Watch Paths del servicio `VetericanoIA`. Integrar únicamente los cambios de triaje, aplicar las variables anteriores y comprobar el despliegue de Django en `dev`. Esta corrección usa la columna JSON existente y no requiere migraciones nuevas.
2. Exportar el workflow publicado como `workflow-actual.json` desde n8n. Ejecutar desde el checkout de Django con el ID visible del workflow.

   ```bash
   python -m triaje_ia.preparar_workflow_n8n --exportado workflow-actual.json --id-workflow "$N8N_WORKFLOW_ID" --salida workflow-actualizacion.json
   ```

   El comando preserva el `webhookId`, la ruta y los IDs de las tres credenciales existentes. Enviar `workflow-actualizacion.json` mediante `PUT /api/v1/workflows/<ID>?publishIfActive=false` con la API existente de n8n. Abrir ese mismo workflow en el editor, revisar las referencias y publicar el borrador. Con acceso solo al editor, aplicar los nodos y conexiones del JSON en el lienzo del workflow existente y dejar un único nodo Webhook antes de publicar. Conservar la exportación y el JSON final de forma local.
3. Confirmar que el flujo publicado usa la URL privada de Django y conserva deshabilitado el guardado de datos de ejecuciones. Antes de enviar casos, comprobar que un `POST` sin autenticación al webhook publicado devuelve 401 o 403. Si devuelve 404 tras publicar, esperar a que n8n registre el webhook y volver a comprobarlo.

### Datos ficticios

Crear una petición nueva por escenario con una cuenta de prueba del entorno autorizado. Usar la descripción exacta y dejar la asignación pendiente. Los casos bajo y medio se crean sin adjuntos. Para el urgente, Django genera la ilustración que recibe Gemini Vision y permite autorizar aparte un adjunto sintético para demostrar DINOv2.

| Escenario para el comando | Prioridad esperada | Descripción exacta |
|---|---|---|
| `baja` | BAJA | [PRUEBA-TRIAJE] En esta simulación, un perro sano está alerta y camina con normalidad. Se solicita un registro preventivo de rutina. |
| `media` | MEDIA | [PRUEBA-TRIAJE] En esta simulación, un perro permanece en un recinto estrecho sin sombra durante varias horas. Está alerta y camina con normalidad. |
| `urgente` | URGENTE | [PRUEBA-TRIAJE] En esta simulación, un perro queda inmóvil junto a un vehículo tras un atropellamiento. Se reporta sangrado activo en una pata. |

### Preparar un duplicado visual reproducible

1. Desde el checkout de Django, exportar la ilustración sintética al directorio de trabajo. El comando solo escribe un PNG local.

   ```bash
   python -c "from pathlib import Path; from triaje_ia.escenarios_demo import dibujar_imagen_demo; Path('triaje-demo-urgente.png').write_bytes(dibujar_imagen_demo())"
   ```

2. Con permiso de escritura en el entorno de la exposición, crear un caso de control y el caso `urgente` con la misma descripción ficticia y las mismas coordenadas simuladas, por ejemplo `0.1500000, -140.1500000`. Adjuntar el PNG a ambas peticiones como única evidencia. Dar al caso de control su propio recurso en el Cloudinary configurado si se quiere verificar la comparación entre dos URLs. DINOv2 requiere coordenadas válidas y un candidato dentro de su radio. El caso de control solo se usa de referencia para duplicados.
3. Copiar la URL HTTPS de Cloudinary que quedó guardada en el caso urgente y pasarla al comando con `--foto-duplicados-url`. Django verifica que esa URL sea la única evidencia y que el caso tenga GPS válido. Gemini recibe exclusivamente la ilustración generada por el servidor. El detector de duplicados usa los adjuntos originales de los dos casos ficticios.

Para crear las peticiones por API, usar `POST /api/peticiones/iniciar/` con el JWT de la cuenta de prueba. Enviar `id_tipo` de un tipo activo y `descripcion` exacta del catálogo. Para el caso urgente y su control, agregar `latitud`, `longitud` y `foto` como URL de Cloudinary o como `data:image/png;base64,…` del PNG exportado. Guardar `id_peticion` y `foto` del HTTP 201. En los otros dos casos basta con el tipo y la descripción.

### Ejecutar y mostrar

1. Desde Bash en la consola autorizada del backend desplegado, asignar `ID_BAJA`, `ID_MEDIA`, `ID_URGENTE` y `URL_CLOUDINARY_URGENTE` a los valores de las peticiones ficticias. Ejecutar los siguientes comandos. El envío toma el secreto del entorno y muestra el HTTP resultante.

   ```bash
   set -o pipefail
   enviar_demo() {
     python manage.py preparar_prueba_webhook_triaje "$@" |
       python -c '
   import json
   import os
   import sys
   import requests

   payload = json.load(sys.stdin)["payload_webhook"]
   respuesta = requests.post(
       os.environ["TRIAJE_N8N_WEBHOOK_URL"],
       headers={"X-Vetericano-Triaje-Secret": os.environ["TRIAJE_N8N_WEBHOOK_SECRET"]},
       json=payload,
       timeout=45,
   )
   print("Petición {}: HTTP {}".format(payload["id_peticion"], respuesta.status_code))
   respuesta.raise_for_status()
   '
   }
   enviar_demo --id-peticion "$ID_URGENTE" --escenario urgente --foto-duplicados-url "$URL_CLOUDINARY_URGENTE"
   enviar_demo --id-peticion "$ID_BAJA" --escenario baja
   enviar_demo --id-peticion "$ID_MEDIA" --escenario media
   ```

2. Un HTTP 2xx confirma recepción. Si el arranque en frío devuelve un error de conexión, comprobar el estado del caso antes de volver a preparar y enviar ese escenario.
3. Con los cambios locales de `jhorman`, ejecutar `ng serve` normal y abrir `localhost:4200` como Jurídico o Admin. Abrir la denuncia ficticia y comprobar prioridad sugerida, justificación y recomendación de veterinario si hay candidatos elegibles. En `urgente` comprobar **Observaciones de la imagen sintética** y que el caso de control se muestre con el método DINOv2. Antes de la exposición, usar exclusivamente uno de los casos ficticios para verificar la confirmación humana de prioridad y de veterinario.
4. Ante un fallo o cuota agotada de Gemini, el panel debe mostrar un resultado **Parcial** con prioridad por reglas locales. Para verificar un arranque en frío real, esperar a que n8n esté dormido antes de enviar uno de los casos. Al terminar la demo, volver a `TRIAJE_GEMINI_ENVIAR_FOTOS=False` con autorización.

## 7. Frontend Angular (`adminVetericano`) y entorno `localhost:4200`

- **Rama real de integración de Angular:** `Developers`.
- **`localhost:4200` habitual:** `ng serve` sigue usando por defecto `development` (`environment.development.ts` → `https://backendvetericano-production.up.railway.app`) y `-c local` (`127.0.0.1:8000`).
