# Ticker Tracker

Aplicación para seguimiento de cotizaciones bursátiles. Descarga el histórico diario y captura el precio actual cada 5 minutos. Expone una API REST protegida con Basic Auth.

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
    "created_at": "2024-03-15T09:00:00"
  },
  {
    "id": 2,
    "symbol": "BTC-USD",
    "name": "Bitcoin USD",
    "enabled": true,
    "created_at": "2024-03-15T09:00:00"
  }
]
```

---

#### `POST /api/tickers` — Añadir ticker

Tras añadirlo, lanza automáticamente la descarga del histórico en segundo plano.

```bash
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
  "created_at": "2024-03-15T11:00:00"
}
```

Errores:

```json
// 409 — el ticker ya existe
{ "detail": "Ticker 'NVDA' already exists." }
```

---

#### `PUT /api/tickers/{symbol}` — Actualizar ticker

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
```

```json
{
  "id": 6,
  "symbol": "NVDA",
  "name": "NVIDIA Corp.",
  "enabled": false,
  "created_at": "2024-03-15T11:00:00"
}
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
│   ├── data_fetcher.py  # Yahoo Finance (primario) + Alpha Vantage (fallback)
│   ├── scheduler.py     # Jobs APScheduler
│   └── auth.py          # HTTP Basic Auth
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
| `tickers` | Lista de símbolos configurados |
| `daily_prices` | OHLCV diario por ticker (único por ticker + fecha) |
| `intraday_prices` | Snapshots de precio cada 5 minutos |
