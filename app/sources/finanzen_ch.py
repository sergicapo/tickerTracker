"""
Data source: finanzen.ch bond snapshot pages.

Requires per-ticker `source_config = {"url": "<finanzen.ch obligation URL>"}`.
There's no symbol-to-URL derivation rule on finanzen.ch, so the URL must be
supplied when the ticker is created or updated.

Equivalent to this Google Sheets formula:

    VALUE(SUBSTITUTE(
        INDEX(
            IMPORTXML(
                "<source_config.url>";
                "//div[@class='snapshot__current-value']"
            );
            2
        );
        ".";
        ","
    ))

IMPORTXML flattens the matching node into text cells; INDEX(..., 2) is the
numeric price (after the "Bid" label). The SUBSTITUTE/VALUE pair converts a
dot-decimal string into a number in a comma-decimal locale.

The historical source synthesises a single-day OHLC bar from the current
snapshot (finanzen.ch's public page only exposes the latest price, not a
daily bar history).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

from lxml import html

from app.sources import DataSource, register_config_validator, register_current, register_historical

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
}


def _http_get(url: str) -> str:
    """GET HTML, impersonating Chrome to avoid 403 from finanzen.ch."""
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(
            url, timeout=20, headers=_HEADERS, impersonate="chrome"
        )
    except Exception:
        import requests

        resp = requests.get(url, timeout=20, headers=_HEADERS)

    resp.raise_for_status()
    return resp.text


def _parse_sheets_value(raw: str) -> float:
    """VALUE(SUBSTITUTE(raw, '.', ',')) in a comma-decimal locale."""
    substituted = raw.strip().replace(".", ",")
    if "," in substituted:
        whole, frac = substituted.rsplit(",", 1)
        whole = re.sub(r"[^\d\-]", "", whole)
        frac = re.sub(r"[^\d]", "", frac)
        return float(f"{whole}.{frac}")
    return float(re.sub(r"[^\d\-]", "", substituted))


def scrape_snapshot_current_value(url: str, xpath_index: int = 2) -> float:
    """
    IMPORTXML(url, "//div[@class='snapshot__current-value']") then INDEX(..., xpath_index).

    `xpath_index` is 1-based, matching Google Sheets INDEX.
    """
    doc = html.fromstring(_http_get(url))
    nodes = doc.xpath("//div[@class='snapshot__current-value']")
    if not nodes:
        raise ValueError(f"No snapshot__current-value node on {url}")

    texts = [t.strip() for t in nodes[0].xpath(".//text()") if t.strip()]
    if len(texts) < xpath_index:
        raise ValueError(
            f"Expected at least {xpath_index} text cells in snapshot, got {texts!r}"
        )

    return _parse_sheets_value(texts[xpath_index - 1])


def _current_from_url(url: str) -> dict:
    price = scrape_snapshot_current_value(url)
    return {
        "price": price,
        "volume": None,
        "timestamp": datetime.utcnow(),
    }


def _daily_bar_from_current(symbol: str, start_date: date, end_date: date, url: str) -> list[dict]:
    """finanzen.ch snapshot is last price only — store it as today's OHLC if in range."""
    today = date.today()
    if today < start_date or today > end_date:
        return []
    quote = _current_from_url(url)
    price = quote["price"]
    logger.debug("Synthesised daily bar for %s on %s from snapshot %.4f", symbol, today, price)
    return [
        {
            "date": today,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": None,
        }
    ]


# --- Registration ---

@register_config_validator(DataSource.FINANZEN_CH)
def _validate_config(config: dict) -> Optional[str]:
    url = config.get("url")
    if not url or not isinstance(url, str):
        return "source_config.url is required for the finanzen_ch data source."
    return None


@register_current(DataSource.FINANZEN_CH)
def _current(symbol: str, config: dict) -> dict:
    return _current_from_url(config["url"])


@register_historical(DataSource.FINANZEN_CH)
def _historical(symbol: str, start_date: date, end_date: date, config: dict) -> list[dict]:
    return _daily_bar_from_current(symbol, start_date, end_date, config["url"])
