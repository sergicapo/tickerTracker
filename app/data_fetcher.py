"""
Data fetching module for Ticker Tracker.

Delegates to the ticker's assigned `DataSource` (see app/sources/), passing
along any per-ticker `source_config`.
"""
import logging
from datetime import date
from typing import Optional

from app import sources
from app.sources import DataSource

logger = logging.getLogger(__name__)


def fetch_historical(
    symbol: str,
    start_date: date,
    end_date: date,
    data_source: DataSource,
    source_config: Optional[dict] = None,
) -> list[dict]:
    """
    Fetch daily OHLCV data for `symbol` between start_date and end_date (inclusive)
    from `data_source`.

    Returns a list of dicts with keys:
        date (datetime.date), open, high, low, close, volume (all float/int or None)

    Raises nothing — on error logs a warning and returns an empty list.
    """
    if not sources.has_historical(data_source):
        logger.debug("Data source %s has no historical fetcher for %s.", data_source, symbol)
        return []

    try:
        return sources.fetch_historical(data_source, symbol, start_date, end_date, source_config)
    except Exception as exc:
        logger.error(
            "Historical fetch failed for %s via %s: %s", symbol, data_source, exc
        )
        return []


def fetch_current_price(
    symbol: str,
    data_source: DataSource,
    source_config: Optional[dict] = None,
) -> Optional[dict]:
    """
    Fetch the most recent price for `symbol` from `data_source`.

    Returns a dict with keys:
        price (float), volume (int or None), timestamp (datetime)

    Returns None on error or if the data source has no current-price fetcher.
    """
    if not sources.has_current(data_source):
        logger.warning("Data source %s has no current-price fetcher for %s.", data_source, symbol)
        return None

    try:
        return sources.fetch_current(data_source, symbol, source_config)
    except Exception as exc:
        logger.error(
            "Current-price fetch failed for %s via %s: %s", symbol, data_source, exc
        )
        return None
