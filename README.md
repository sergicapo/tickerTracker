# Ticker Tracker

Aplicación para seguimiento de cotizaciones bursátiles. Descarga el histórico diario y captura el precio actual cada 5 minutos. Expone una API REST protegida con Basic Auth.

Cada ticker tiene asignado un **origen de datos** (`data_source`) explícito — `yahoo_finance` (por defecto), `euronext` o `finanzen_ch` — que determina de dónde se obtienen sus precios. Algunos orígenes requieren configuración adicional por ticker (`source_config`), como el mercado (MIC) en Euronext o la URL del snapshot en finanzen.ch.

**Stack:** Python 3.12 · FastAPI · SQLite · yfinance · APScheduler · Docker

---

## Inicio rápido

### 1. Configuración

```bash
cp .env.example .env
```

Edita `.env` y cambia al menos las credenciales de la API:

```env
API_USER=admin
API_PASSWORD=tu_password_seguro

# Opcional: fallback a Alpha Vantage si Yahoo Finance falla
ALPHA_VANTAGE_API_KEY=tu_api_key
```

### 2. Arrancar con Docker

```bash
mkdir -p data
docker compose build
docker compose up -d
```

La API queda disponible en `http://localhost:8000`.

### 3. Verificar que está corriendo

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "timestamp": "2024-03-15T10:30:00"
}
```

---

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `API_USER` | `admin` | Usuario para Basic Auth |
| `API_PASSWORD` | `changeme` | Contraseña para Basic Auth |
| `DATABASE_URL` | `sqlite:////data/ticker_tracker.db` | Ruta a la base de datos SQLite |
| `LOG_LEVEL` | `INFO` | Nivel de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `FETCH_INTERVAL_MINUTES` | `5` | Frecuencia de captura de precio actual |
| `HISTORICAL_START_DATE` | `2020-01-01` | Fecha desde la que se descarga el histórico |
| `ALPHA_VANTAGE_API_KEY` | _(vacío)_ | API key para el fallback de Alpha Vantage |

---

## Comportamiento automático

- **Al arrancar:** descarga el histórico diario (desde `HISTORICAL_START_DATE` o desde el último dato guardado) para todos los tickers habilitados.
- **Cada 5 minutos:** captura el precio actual de cada ticker y lo guarda en `intraday_prices`.
- **Cada día a las 00:05 UTC:** descarga el OHLCV del día anterior para todos los tickers habilitados.
- **Tickers por defecto** (primera ejecución): `AAPL`, `MSFT`, `GOOGL`, `AMZN`, `BTC-USD`.

---

## Orígenes de datos (`data_source`)

Cada ticker está asociado a exactamente un origen de datos, que decide cómo se obtiene su precio actual y su histórico diario. Se define al crear el ticker (`POST /api/tickers`) y puede cambiarse después (`PUT /api/tickers/{symbol}`).

| `data_source` | Precio actual | Histórico diario | `source_config` requerido |
|---|---|---|---|
| `yahoo_finance` _(por defecto)_ | ✅ Yahoo Finance (fallback opcional a Alpha Vantage) | ✅ Yahoo Finance (fallback opcional a Alpha Vantage) | Ninguno |
| `euronext` | ✅ `live.euronext.com` (precio del último trade intradía) | ❌ No disponible | `{"mic": "<Market Identifier Code>"}` |
| `finanzen_ch` | ✅ Snapshot de finanzen.ch | ✅ (sintetiza una única barra OHLC diaria a partir del snapshot) | `{"url": "<URL de la página del bono en finanzen.ch>"}` |

Si el `data_source` elegido requiere `source_config` y no se aporta (o le faltan campos), la API responde **422 Unprocessable Entity** con el detalle del campo que falta — no se llega a crear/actualizar el ticker.

### `yahoo_finance`

Sin configuración adicional. El `symbol` se pasa directamente a `yfinance`.

```bash
curl -u admin:changeme \
  -X POST http://localhost:8000/api/tickers \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NVDA", "name": "NVIDIA Corporation"}'
```

(Omitir `data_source` es equivalente a indicar `"data_source": "yahoo_finance"`.)

### `euronext`

Para bonos listados en Euronext (`live.euronext.com`). Requiere el **MIC** (Market Identifier Code) del mercado donde cotiza el ISIN — por ejemplo `MOTX` para el mercado MOT. El MIC no se puede derivar del ISIN; hay que consultarlo en la página del instrumento en Euronext.

```bash
curl -u admin:changeme \
  -X POST http://localhost:8000/api/tickers \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "XS2538440780",
    "name": "ROMANIA TF 5% ST26",
    "data_source": "euronext",
    "source_config": {"mic": "MOTX"}
  }'
```

Solo expone precio actual — no tiene histórico diario, así que el job de backfill no descargará nada para estos tickers.

### `finanzen_ch`

Para bonos publicados en finanzen.ch. Requiere la URL completa de la página del bono (snapshot).

```bash
curl -u admin:changeme \
  -X POST http://localhost:8000/api/tickers \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "US912810SS87.SG",
    "name": "US-T Govt Bond 1.625 Nov15 2050",
    "data_source": "finanzen_ch",
    "source_config": {
      "url": "https://www.finanzen.ch/obligationen/united_states_of_americadl-bonds_202050-obligation-2050-us912810ss87?a=1"
    }
  }'
```

Como finanzen.ch solo expone el último precio (no una serie histórica), el histórico diario se sintetiza guardando ese snapshot como una barra OHLC de un único valor para el día en curso.

### Cambiar el origen de un ticker existente

```bash
curl -u admin:changeme \
  -X PUT http://localhost:8000/api/tickers/XS2538440780 \
  -H "Content-Type: application/json" \
  -d '{"data_source": "euronext", "source_config": {"mic": "MOTX"}}'
```

Si solo se cambian `name` o `enabled` sin tocar `data_source`/`source_config`, no se revalida la configuración del origen actual.

---

## API Reference

Todos los endpoints excepto `/health` requieren **HTTP Basic Auth**.

### Health

#### `GET /health`

Sin autenticación. Verifica que el servicio está activo.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "timestamp": "2024-03-15T10:30:00"
}
```

---

### Tickers

#### `GET /api/tickers` — Listar tickers

```bash
curl -u admin:changeme http://localhost:8000/api/tickers
```

```json
[
  {
    "id": 1,
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "enabled": true,
    "data_source": "yahoo_finance",
    "source_config": null,
    "created_at": "2024-03-15T09:00:00"
  },
  {
    "id": 8,
    "symbol": "XS2538440780",
    "name": "ROMANIA TF 5% ST26",
    "enabled": true,
    "data_source": "euronext",
    "source_config": {"mic": "MOTX"},
    "created_at": "2024-03-15T09:00:00"
  }
]
```

---

#### `POST /api/tickers` — Añadir ticker

Tras añadirlo, lanza automáticamente la descarga del histórico en segundo plano (si el `data_source` la soporta — ver [Orígenes de datos](#orígenes-de-datos-data_source)).

`data_source` es opcional (por defecto `yahoo_finance`). `source_config` es obligatorio para algunos orígenes.

```bash
# Yahoo Finance (por defecto) — sin source_config
curl -u admin:changeme \
  -X POST http://localhost:8000/api/tickers \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NVDA", "name": "NVIDIA Corporation"}'
```

```json
{
  "id": 6,
  "symbol": "NVDA",
  "name": "NVIDIA Corporation",
  "enabled": true,
  "data_source": "yahoo_finance",
  "source_config": null,
  "created_at": "2024-03-15T11:00:00"
}
```

```bash
# Euronext — requiere source_config.mic
curl -u admin:changeme \
  -X POST http://localhost:8000/api/tickers \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "XS2538440780",
    "name": "ROMANIA TF 5% ST26",
    "data_source": "euronext",
    "source_config": {"mic": "MOTX"}
  }'
```

Errores:

```json
// 409 — el ticker ya existe
{ "detail": "Ticker 'NVDA' already exists." }

// 422 — falta source_config para el data_source elegido
{ "detail": "source_config.mic is required for the euronext data source (e.g. {\"mic\": \"MOTX\"})." }
```

---

#### `PUT /api/tickers/{symbol}` — Actualizar ticker

Permite modificar `name`, `enabled`, `data_source` y/o `source_config`. Si se cambia el origen de datos (o su configuración), se revalida contra el nuevo origen antes de guardar.

```bash
# Deshabilitar un ticker (deja de actualizar sus precios)
curl -u admin:changeme \
  -X PUT http://localhost:8000/api/tickers/NVDA \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Cambiar el nombre
curl -u admin:changeme \
  -X PUT http://localhost:8000/api/tickers/NVDA \
  -H "Content-Type: application/json" \
  -d '{"name": "NVIDIA Corp."}'

# Cambiar el origen de datos (euronext requiere source_config.mic)
curl -u admin:changeme \
  -X PUT http://localhost:8000/api/tickers/XS2538440780 \
  -H "Content-Type: application/json" \
  -d '{"data_source": "euronext", "source_config": {"mic": "MOTX"}}'
```

```json
{
  "id": 6,
  "symbol": "NVDA",
  "name": "NVIDIA Corp.",
  "enabled": false,
  "data_source": "yahoo_finance",
  "source_config": null,
  "created_at": "2024-03-15T11:00:00"
}
```

Errores:

```json
// 404 — el ticker no existe
{ "detail": "Ticker 'NVDA' not found." }

// 422 — falta source_config para el nuevo data_source
{ "detail": "source_config.url is required for the finanzen_ch data source." }
```

---

#### `DELETE /api/tickers/{symbol}` — Eliminar ticker

Elimina el ticker y **todos sus datos históricos e intradiarios**.

```bash
curl -u admin:changeme \
  -X DELETE http://localhost:8000/api/tickers/NVDA
```

Respuesta: `204 No Content`

---

### Cotizaciones

#### `GET /api/quotes/{symbol}/current` — Precio actual y cierre de la sesión anterior

Devuelve el último snapshot de precio capturado (con timestamp) más el precio de cierre de la última sesión bursátil cerrada.

```bash
curl -u admin:changeme http://localhost:8000/api/quotes/AAPL/current
```

```json
{
  "ticker_id": 1,
  "timestamp": "2024-03-15T14:35:02",
  "price": 174.55,
  "volume": null,
  "previous_close": 173.72,
  "previous_close_date": "2024-03-14"
}
```

Errores:

```json
// 404 — ticker no existe
{ "detail": "Ticker 'AAPL' not found." }

// 404 — aún no hay snapshots intradiarios capturados
{ "detail": "No current price available for 'AAPL' yet." }
```

---

#### `GET /api/quotes/{symbol}/date/{date}` — Precio en una fecha concreta

Formato de fecha: `YYYY-MM-DD`.

```bash
curl -u admin:changeme http://localhost:8000/api/quotes/MSFT/date/2024-01-15
```

```json
{
  "id": 892,
  "ticker_id": 2,
  "date": "2024-01-15",
  "open": 374.15,
  "high": 376.23,
  "low": 370.80,
  "close": 375.47,
  "volume": 21345600
}
```

Errores:

```json
// 404 — no hay datos para esa fecha (festivo, fin de semana, fuera del rango descargado)
{ "detail": "No daily price for 'MSFT' on 2024-01-15." }
```

---

#### `GET /api/quotes/{symbol}/history` — Histórico por rango de fechas

Parámetros query: `start` y `end` en formato `YYYY-MM-DD` (ambos inclusivos).

```bash
curl -u admin:changeme \
  "http://localhost:8000/api/quotes/GOOGL/history?start=2024-01-01&end=2024-01-05"
```

```json
[
  {
    "id": 401,
    "ticker_id": 3,
    "date": "2024-01-02",
    "open": 140.08,
    "high": 141.55,
    "low": 138.79,
    "close": 140.93,
    "volume": 24876300
  },
  {
    "id": 402,
    "ticker_id": 3,
    "date": "2024-01-03",
    "open": 138.50,
    "high": 139.20,
    "low": 136.44,
    "close": 138.21,
    "volume": 28154000
  },
  {
    "id": 403,
    "ticker_id": 3,
    "date": "2024-01-04",
    "open": 137.80,
    "high": 139.75,
    "low": 137.02,
    "close": 139.51,
    "volume": 25632100
  },
  {
    "id": 404,
    "ticker_id": 3,
    "date": "2024-01-05",
    "open": 138.92,
    "high": 140.11,
    "low": 137.60,
    "close": 139.69,
    "volume": 22187400
  }
]
```

Errores:

```json
// 422 — rango inválido
{ "detail": "'end' date must be >= 'start' date." }
```

---

#### `GET /api/quotes/{symbol}/intraday` — Snapshots intradiarios de un día

Devuelve todos los snapshots de precio capturados cada 5 minutos para un día concreto.

Parámetro query: `date` en formato `YYYY-MM-DD`.

```bash
curl -u admin:changeme \
  "http://localhost:8000/api/quotes/BTC-USD/intraday?date=2024-03-15"
```

```json
[
  {
    "id": 7801,
    "ticker_id": 5,
    "timestamp": "2024-03-15T08:00:01",
    "price": 68432.50,
    "volume": null
  },
  {
    "id": 7802,
    "ticker_id": 5,
    "timestamp": "2024-03-15T08:05:01",
    "price": 68519.75,
    "volume": null
  },
  {
    "id": 7803,
    "ticker_id": 5,
    "timestamp": "2024-03-15T08:10:02",
    "price": 68387.20,
    "volume": null
  }
]
```

---

## Documentación interactiva

FastAPI genera documentación automática disponible en:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

---

## Despliegue en Ugreen DXP2800 (NAS ARM64)

1. Copia el proyecto al NAS (por SSH, SMB o la interfaz web):
   ```bash
   scp -r /ruta/local/Ticker_Tracker usuario@<ip-nas>:/ruta/destino/
   ```

2. Conéctate al NAS por SSH:
   ```bash
   ssh usuario@<ip-nas>
   cd /ruta/destino/Ticker_Tracker
   ```

3. Crea el `.env` con tus credenciales:
   ```bash
   cp .env.example .env
   nano .env
   ```

4. Construye y arranca (build nativo ARM64):
   ```bash
   mkdir -p data
   docker compose build
   docker compose up -d
   ```

5. Verifica el estado:
   ```bash
   docker compose ps
   docker compose logs -f
   ```

---

## Estructura del proyecto

```
ticker_tracker/
├── app/
│   ├── main.py          # FastAPI app, endpoints, lifespan
│   ├── database.py      # SQLAlchemy async engine y sesiones
│   ├── models.py        # Modelos ORM (Ticker, DailyPrice, IntradayPrice)
│   ├── schemas.py       # Schemas Pydantic v2
│   ├── crud.py          # Operaciones de base de datos
│   ├── data_fetcher.py  # Despacha al DataSource asignado a cada ticker
│   ├── scheduler.py     # Jobs APScheduler
│   ├── auth.py          # HTTP Basic Auth
│   └── sources/         # Orígenes de datos (uno por data_source)
│       ├── __init__.py       # Registro central (DataSource enum, decoradores)
│       ├── yahoo_finance.py  # Yahoo Finance (primario) + Alpha Vantage (fallback)
│       ├── euronext.py       # live.euronext.com (bonos, requiere source_config.mic)
│       └── finanzen_ch.py    # finanzen.ch (bonos, requiere source_config.url)
├── data/                # Volumen Docker — contiene ticker_tracker.db
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## Base de datos

| Tabla | Descripción |
|---|---|
| `tickers` | Lista de símbolos configurados, con su `data_source` y `source_config` (JSON) |
| `daily_prices` | OHLCV diario por ticker (único por ticker + fecha) |
| `intraday_prices` | Snapshots de precio cada 5 minutos |
