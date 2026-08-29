"""
Default data source: Yahoo Finance via yfinance, with an optional Alpha
Vantage fallback for when Yahoo Finance is unavailable or doesn't recognise
the symbol.

Takes no per-ticker `source_config` — Yahoo Finance tickers are looked up by
symbol alone.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Optional

import yfinance as yf

from app.sources import DataSource, register_current, register_historical

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API (registered handlers)
# ---------------------------------------------------------------------------

@register_historical(DataSource.YAHOO_FINANCE)
def _historical(symbol: str, start_date: date, end_date: date, config: dict) -> list[dict]:
    try:
        return _fetch_historical_yahoo(symbol, start_date, end_date)
    except Exception as primary_exc:
        logger.warning(
            "Yahoo Finance historical fetch failed for %s (%s). Trying fallback.",
            symbol,
            primary_exc,
        )
        try:
            return _fetch_historical_fallback(symbol, start_date, end_date)
        except NotImplementedError:
            logger.warning(
                "No fallback historical source configured for %s. Returning empty list.",
                symbol,
            )
            return []
        except Exception as fallback_exc:
            logger.error(
                "Fallback historical fetch also failed for %s: %s", symbol, fallback_exc
            )
            return []


@register_current(DataSource.YAHOO_FINANCE)
def _current(symbol: str, config: dict) -> Optional[dict]:
    try:
        return _fetch_current_price_yahoo(symbol)
    except Exception as primary_exc:
        logger.warning(
            "Yahoo Finance current-price fetch failed for %s (%s). Trying fallback.",
            symbol,
            primary_exc,
        )
        try:
            return _fetch_current_price_fallback(symbol)
        except NotImplementedError:
            logger.warning(
                "No fallback current-price source configured for %s. Returning None.",
                symbol,
            )
            return None
        except Exception as fallback_exc:
            logger.error(
                "Fallback current-price fetch also failed for %s: %s", symbol, fallback_exc
            )
            return None


# ---------------------------------------------------------------------------
# Primary source — Yahoo Finance
# ---------------------------------------------------------------------------

def _fetch_historical_yahoo(symbol: str, start_date: date, end_date: date) -> list[dict]:
    """
    Download daily OHLCV bars from Yahoo Finance using yfinance.

    yfinance's download() end date is *exclusive*, so we pass end_date + 1 day.
    """
    ticker = yf.Ticker(symbol)
    # yfinance end is exclusive, bump by one day
    end_exclusive = end_date + timedelta(days=1)

    df = ticker.history(
        start=start_date.isoformat(),
        end=end_exclusive.isoformat(),
        interval="1d",
        auto_adjust=True,
        actions=False,
    )

    if df.empty:
        logger.debug("Yahoo Finance returned empty DataFrame for %s (%s–%s)", symbol, start_date, end_date)
        return []

    rows: list[dict] = []
    for idx, row in df.iterrows():
        # idx is a timezone-aware Timestamp; normalise to a plain date
        price_date: date = idx.date() if hasattr(idx, "date") else idx.to_pydatetime().date()
        rows.append(
            {
                "date": price_date,
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_int(row.get("Volume")),
            }
        )

    logger.debug("Fetched %d daily bars for %s from Yahoo Finance.", len(rows), symbol)
    return rows


def _fetch_current_price_yahoo(symbol: str) -> dict:
    """
    Retrieve the latest price using yfinance fast_info (lightweight, no full download).
    Falls back to history(period='1d') if fast_info price is unavailable.
    """
    ticker = yf.Ticker(symbol)

    price: Optional[float] = None
    volume: Optional[int] = None

    # fast_info is the quickest path — a lightweight quote endpoint
    try:
        fi = ticker.fast_info
        price = _safe_float(getattr(fi, "last_price", None))
        volume = _safe_int(getattr(fi, "last_volume", None))
    except Exception as exc:
        logger.debug("fast_info unavailable for %s: %s", symbol, exc)

    # If fast_info gave us nothing, pull the most recent close from history
    if price is None:
        df = ticker.history(period="1d", interval="1m", auto_adjust=True, actions=False)
        if df.empty:
            df = ticker.history(period="2d", interval="1d", auto_adjust=True, actions=False)
        if not df.empty:
            last_row = df.iloc[-1]
            price = _safe_float(last_row.get("Close"))
            volume = _safe_int(last_row.get("Volume"))

    if price is None:
        raise ValueError(f"Could not determine current price for {symbol}")

    return {
        "price": price,
        "volume": volume,
        "timestamp": datetime.utcnow(),
    }


# ---------------------------------------------------------------------------
# TODO: FALLBACK SOURCE
# ---------------------------------------------------------------------------
# The functions below are intentional stubs. Replace the `raise NotImplementedError`
# body with a real implementation when you want a secondary data source.
#
# Example: Alpha Vantage
# ----------------------
# 1. Sign up at https://www.alphavantage.co/support/#api-key and obtain a free key.
# 2. Add ALPHA_VANTAGE_API_KEY=<your_key> to your .env file.
# 3. Install the SDK:  pip install alpha_vantage
#    Or use direct HTTP requests with the `requests` / `httpx` library.
#
# Historical bars endpoint (TIME_SERIES_DAILY_ADJUSTED):
#   GET https://www.alphavantage.co/query
#       ?function=TIME_SERIES_DAILY_ADJUSTED
#       &symbol=AAPL
#       &outputsize=full          # "compact" = last 100 days only
#       &apikey=<ALPHA_VANTAGE_API_KEY>
#
# Current price endpoint (GLOBAL_QUOTE):
#   GET https://www.alphavantage.co/query
#       ?function=GLOBAL_QUOTE
#       &symbol=AAPL
#       &apikey=<ALPHA_VANTAGE_API_KEY>
#
# Note: The free tier is limited to 25 requests/day and 5 requests/minute.
# Consider caching responses or upgrading to a paid plan for production use.
#
# Alternative: Polygon.io
# -----------------------
# 1. Sign up at https://polygon.io and obtain an API key.
# 2. Add POLYGON_API_KEY=<your_key> to your .env file.
# 3. Install the SDK: pip install polygon-api-client
#
# Historical bars: client.get_aggs(ticker, 1, "day", start_date, end_date)
# Current price:   client.get_snapshot_ticker("stocks", symbol)
# ---------------------------------------------------------------------------


def _fetch_historical_fallback(symbol: str, start_date: date, end_date: date) -> list[dict]:
    """Fallback historical data fetch via Alpha Vantage (TIME_SERIES_DAILY_ADJUSTED)."""
    import requests

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise NotImplementedError("ALPHA_VANTAGE_API_KEY not set")

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": "full",
            "apikey": api_key,
        },
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.json().get("Time Series (Daily)", {})
    if not data:
        logger.warning("Alpha Vantage returned no time series data for %s", symbol)
        return []

    rows = []
    for date_str, values in data.items():
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if start_date <= d <= end_date:
            rows.append({
                "date": d,
                "open":   _safe_float(values.get("1. open")),
                "high":   _safe_float(values.get("2. high")),
                "low":    _safe_float(values.get("3. low")),
                "close":  _safe_float(values.get("5. adjusted close")),
                "volume": _safe_int(values.get("6. volume")),
            })

    logger.debug("Alpha Vantage returned %d bars for %s", len(rows), symbol)
    return sorted(rows, key=lambda r: r["date"])

    # TODO: FALLBACK SOURCE — add a second fallback here (e.g. Polygon.io) if needed


def _fetch_current_price_fallback(symbol: str) -> dict:
    """Fallback current-price fetch via Alpha Vantage (GLOBAL_QUOTE)."""
    import requests

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise NotImplementedError("ALPHA_VANTAGE_API_KEY not set")

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()

    quote = resp.json().get("Global Quote", {})
    price = _safe_float(quote.get("05. price"))
    if price is None:
        raise ValueError(f"Alpha Vantage returned no price for {symbol}")

    return {
        "price": price,
        "volume": _safe_int(quote.get("06. volume")),
        "timestamp": datetime.utcnow(),
    }

    # TODO: FALLBACK SOURCE — add a second fallback here (e.g. Polygon.io) if needed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value) -> Optional[float]:
    """Convert a value to float, returning None if conversion fails."""
    if value is None:
        return None
    try:
        result = float(value)
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> Optional[int]:
    """Convert a value to int, returning None if conversion fails."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
