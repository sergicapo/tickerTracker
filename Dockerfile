# ---------------------------------------------------------------------------
# Ticker Tracker — Dockerfile
#
# Targets python:3.12-slim (multi-arch: amd64 + arm64).
# The docker-compose.yml pins platform: linux/arm64 for the Ugreen DXP2800.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Keeps Python from buffering stdout/stderr so logs appear immediately
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install OS-level build dependencies needed by some Python packages
# (e.g., lxml / cryptography pulled in transitively by yfinance)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# The SQLite database is stored on a named volume mounted at /data
RUN mkdir -p /data

# Default environment — override with .env or docker-compose env_file
ENV DATABASE_URL=sqlite:////data/ticker_tracker.db \
    API_USER=admin \
    API_PASSWORD=changeme \
    LOG_LEVEL=INFO \
    FETCH_INTERVAL_MINUTES=5 \
    HISTORICAL_START_DATE=2020-01-01

EXPOSE 8000

# Run with uvicorn; --workers 1 keeps SQLite writes safe (single process)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
