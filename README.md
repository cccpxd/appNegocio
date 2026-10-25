# Lector de facturas

Foto de una factura colombiana → datos ordenados, sin digitar.

FastAPI + HTML plano + Postgres (Neon) + Gemini. Corre en Vercel.

---

## Cómo funciona

```
navegador                    Vercel (FastAPI)                  Postgres
   │                               │                              │
   ├─ comprime la foto ───────────►│                              │
   │                               ├─ POST /procesar ────────────►│ imagen en bytea
   │◄──── id + estado 'pendiente' ─┤   (sin IA, 0.1 s)            │ estado 'pendiente'
   │                               │                              │
   ├─ POST /facturas/{id}/procesar►│                              │
   │                               ├─ UPDATE ... RETURNING ──────►│ estado 'procesando'
   │                               ├─ Gemini (1 llamada) ─────────┤
   │                               ├─ UPDATE datos + imagen=NULL ►│ estado 'ok'/'revisar'
   │◄──── datos para revisar ──────┤                              │
   │                               │                              │
   ├─ corrige y POST /guardar ────►│                              │
```

La imagen vive en la base **solo mientras la factura hace fila**. Al terminar se
pone en `NULL` y el espacio se recicla para la siguiente. La tabla se queda del
tamaño de la cola, no del historial.

Si la IA falla, la factura vuelve a `pendiente` conservando la imagen. A los 5
intentos se marca `error` y se suelta la imagen.

---

## Local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

copy .env.example .env          # y llena las variables
uvicorn api.index:app --reload --port 8000
```

Abre http://127.0.0.1:8000

Sin `DATABASE_URL` la app arranca igual: `/procesar` responde 501 con un mensaje
claro. Sirve para ver la interfaz, no para guardar.

---

## Base de datos (Neon)

1. Crea un proyecto en https://neon.tech
2. Abre el **SQL Editor** y pega el contenido de [`sql/001_facturas.sql`](sql/001_facturas.sql).
3. Copia la cadena de conexión **del pooler** (la que lleva `-pooler` en el host)
   y pégala en `DATABASE_URL`. Debe terminar en `?sslmode=require`.

El pooler es importante: el código ya viene con `prepare_threshold=None` para
que psycopg no pelee con PgBouncer.

---

## Vercel

### Variables de entorno

En **Project Settings → Environment Variables**:

| Variable | Valor |
|---|---|
| `GOOGLE_API_KEY` | tu clave de Gemini |
| `DATABASE_URL` | la cadena del pooler de Neon |
| `CRON_SECRET` | una cadena larga y aleatoria |

`CRON_SECRET` con ese nombre exacto: Vercel firma el cron con
`Authorization: Bearer <CRON_SECRET>` automáticamente.

Opcionales:

| Variable | Por defecto | Para qué |
|---|---|---|
| `SERVICIO_IA` | `standard` | `flex` cuesta la mitad pero tarda 1-2 min |
| `TIEMPO_IA_MS` | `60000` | corte de la llamada a Gemini |

### Despliegue

```bash
npm i -g vercel
vercel
vercel --prod
```

O conecta el repositorio de GitHub desde el dashboard. Vercel detecta FastAPI
por `pyproject.toml`:

```toml
[tool.vercel]
entrypoint = "api/index:app"
```

Esa línea es obligatoria: en la raíz hay un `app.py` viejo de Streamlit y sin el
`entrypoint` Vercel intenta usarlo.

### Después de desplegar

- `GET /salud` debe responder `base_datos: "conectada"` y `cron: "configurado"`.
- `GET /docs` muestra la API interactiva.
- El cron corre una vez al día (plan Hobby). Si quieres que recoja las
  abandonadas más seguido, apunta un cron externo gratuito (cron-job.org) a
  `POST /cron/pendientes` con la cabecera `x-cron-secreto`.

---

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/` | La interfaz |
| `GET` | `/salud` | Diagnóstico del despliegue |
| `POST` | `/procesar` | Encola la foto y responde al instante |
| `POST` | `/facturas/{id}/procesar` | El worker: intenta leerla con Gemini |
| `GET` | `/facturas/{id}` | Polling del navegador |
| `POST` | `/guardar` | Guarda las correcciones del usuario |
| `POST` | `/cron/pendientes` | Recoge lo que quedó en la cola (requiere secreto) |

Estados: `pendiente` → `procesando` → `ok` | `revisar` | `error`.

`revisar` significa que la IA respondió pero los números no cuadran
(subtotal + IVA ≠ total, o la suma de ítems no da). No se vuelve a llamar a la
IA: se marca y el usuario corrige.

---

## Estructura

```
api/index.py              rutas HTTP
backend/
  config.py               ajustes desde el entorno
  dependencias.py         Depends de FastAPI
  esquema.py              esquema JSON que se le pasa a Gemini
  ia.py                   la llamada a Gemini y la clasificación de errores
  imagen.py               compresión + hash SHA-256
  modelos.py              modelos Pydantic de la API
  repositorio.py          contrato de datos
  repositorio_postgres.py la cola real
  servicio.py             orquestación
  validacion.py           normalización y validaciones de negocio
public/index.html         toda la interfaz (CSS y JS incluidos)
sql/001_facturas.sql      esquema de la base
app.py                    prototipo viejo de Streamlit (no se usa)
```

---

## Costos

Una factura = una llamada a Gemini con resolución media y sin "thinking".
Con `standard` y ~10 facturas diarias sobra dentro del plan gratuito de Gemini.
La misma factura subida dos veces no gasta IA: el hash SHA-256 la detecta.
