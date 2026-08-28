"""
Custom fetcher for XS2538440780 from live.euronext.com.

Scrapes the latest price from:
    https://live.euronext.com/en/product/bonds/XS2538440780-MOTX

Logic: Download the HTML, extract the price from the relevant node,
and return as a float.
"""

from __future__ import annotations
import logging
import re
from datetime import datetime
import httpx
from lxml import html

from app.custom_fetchers import register_current

logger = logging.getLogger(__name__)

# --- Scraper Implementation ---

_BOND_SYMBOL = "XS2538440780"
_TARGET_URL = f"https://live.euronext.com/en/product/bonds/{_BOND_SYMBOL}-MOTX"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

def _fetch_current_xs2538440780() -> float | None:
    try:
        resp = httpx.get(_TARGET_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        doc = html.fromstring(resp.text)
        # XPath for the last price, as shown in the Euronext UI
        # The price is in a <span> with e.g. class "eui-price-display__price" 
        price_node = doc.xpath("//span[contains(@class, 'eui-price-display__price')]")
        if not price_node or not price_node[0].text_content():
            logger.error("Price node not found on Euronext page for XS2538440780")
            return None
        # The price might contain commas for decimal (e.g. "93,28")
        price_txt = price_node[0].text_content().strip().replace('\xa0', '')
        # Replace comma with dot if locale is used
        price_txt = price_txt.replace(",", ".")
        # Remove other non-numeric characters if any
        match = re.search(r"(\d+\.\d+|\d+)", price_txt)
        if not match:
            logger.error("Price text extract failed: %r", price_txt)
            return None
        return float(match.group(1))
    except Exception as exc:
        logger.error("Error fetching current price for XS2538440780: %s", exc)
        return None


# --- Registration (for app/custom_fetchers/__init__.py autodiscovery) ---

@register_current(_BOND_SYMBOL)
def _current(symbol: str) -> dict:
    price = _fetch_current_xs2538440780()
    return {
        "price": price,
        "volume": None,
        "timestamp": datetime.utcnow(),
    }