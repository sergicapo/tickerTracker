"""
Data source registry for Ticker Tracker.

Each ticker is explicitly assigned a `DataSource` (yahoo_finance, euronext,
finanzen_ch, ...) instead of relying on a symbol allowlist buried in a
"custom fetcher" module. Some sources need per-ticker configuration (e.g.
euronext needs the market's MIC code, finanzen_ch needs the snapshot URL);
that configuration is supplied by the caller as `source_config` and stored
on the Ticker row.

To add a new source:
  1. Write a module under app/sources/ with functions decorated with
     @register_current / @register_historical / @register_config_validator.
  2. Add the source name to the `DataSource` enum below.
  3. Import the module at the bottom of this file so the decorators run.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Callable, Optional


class DataSource(str, Enum):
    YAHOO_FINANCE = "yahoo_finance"
    EURONEXT = "euronext"
    FINANZEN_CH = "finanzen_ch"


CurrentHandler = Callable[[str, dict], Optional[dict]]
HistoricalHandler = Callable[[str, date, date, dict], list[dict]]
ConfigValidator = Callable[[dict], Optional[str]]

_CURRENT: dict[DataSource, CurrentHandler] = {}
_HISTORICAL: dict[DataSource, HistoricalHandler] = {}
_CONFIG_VALIDATORS: dict[DataSource, ConfigValidator] = {}

DEFAULT_DATA_SOURCE = DataSource.YAHOO_FINANCE


def register_current(source: DataSource):
    """Register a current-price fetcher for `source`."""

    def decorator(fn: CurrentHandler) -> CurrentHandler:
        _CURRENT[source] = fn
        return fn

    return decorator


def register_historical(source: DataSource):
    """Register a daily OHLCV fetcher for `source`."""

    def decorator(fn: HistoricalHandler) -> HistoricalHandler:
        _HISTORICAL[source] = fn
        return fn

    return decorator


def register_config_validator(source: DataSource):
    """
    Register a `source_config` validator for `source`.

    The validator receives the config dict (never None — an empty dict if
    the caller didn't supply one) and returns an error message string if
    invalid, or None if the config is acceptable.
    """

    def decorator(fn: ConfigValidator) -> ConfigValidator:
        _CONFIG_VALIDATORS[source] = fn
        return fn

    return decorator


def validate_source_config(source: DataSource, config: Optional[dict]) -> Optional[str]:
    """Return an error message if `config` is invalid for `source`, else None."""
    validator = _CONFIG_VALIDATORS.get(source)
    if validator is None:
        return None
    return validator(config or {})


def has_current(source: DataSource) -> bool:
    return source in _CURRENT


def has_historical(source: DataSource) -> bool:
    return source in _HISTORICAL


def fetch_current(source: DataSource, symbol: str, config: Optional[dict]) -> Optional[dict]:
    return _CURRENT[source](symbol, config or {})


def fetch_historical(
    source: DataSource,
    symbol: str,
    start_date: date,
    end_date: date,
    config: Optional[dict],
) -> list[dict]:
    return _HISTORICAL[source](symbol, start_date, end_date, config or {})


# Import modules so @register_* decorators populate the maps.
from app.sources import yahoo_finance  # noqa: E402,F401
from app.sources import euronext  # noqa: E402,F401
from app.sources import finanzen_ch  # noqa: E402,F401
