"""
Per-ticker fetch overrides.

Yahoo Finance is the default source. Register a handler here when a symbol
needs a custom current-price and/or historical implementation.

To add a new ticker:
  1. Write a module under app/custom_fetchers/ (or add to an existing one).
  2. Decorate the functions with @register_current / @register_historical.
  3. Import that module at the bottom of this file so the decorators run.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

CurrentHandler = Callable[[str], dict]
HistoricalHandler = Callable[[str, date, date], list[dict]]

_CURRENT: dict[str, CurrentHandler] = {}
_HISTORICAL: dict[str, HistoricalHandler] = {}


def _norm(symbol: str) -> str:
    return symbol.strip().upper()


def register_current(symbol: str):
    """Register a current-price fetcher for `symbol`."""

    def decorator(fn: CurrentHandler) -> CurrentHandler:
        _CURRENT[_norm(symbol)] = fn
        return fn

    return decorator


def register_historical(symbol: str):
    """Register a daily OHLCV fetcher for `symbol`."""

    def decorator(fn: HistoricalHandler) -> HistoricalHandler:
        _HISTORICAL[_norm(symbol)] = fn
        return fn

    return decorator


def has_custom_current(symbol: str) -> bool:
    return _norm(symbol) in _CURRENT


def has_custom_historical(symbol: str) -> bool:
    return _norm(symbol) in _HISTORICAL


def fetch_current(symbol: str) -> dict:
    return _CURRENT[_norm(symbol)](symbol)


def fetch_historical(symbol: str, start_date: date, end_date: date) -> list[dict]:
    return _HISTORICAL[_norm(symbol)](symbol, start_date, end_date)


# Import modules so @register_* decorators populate the maps.
from app.custom_fetchers import finanzen_ch  # noqa: E402,F401
from app.custom_fetchers import euronext  # noqa: E402,F401
