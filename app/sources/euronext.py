solo tien"""
Data source: live.euronext.com, for bonds listed on Euronext markets.

Requires per-ticker `source_config = {"mic": "<Market Identifier Code>"}`
(e.g. "MOTX" for the MOT market). The MIC varies by listing venue and isn't
derivable from the ISIN alone, so it must be supplied when the ticker is
created or updated.

The instrument page (e.g. https://live.euronext.com/en/product/bonds/{ISIN}-{MIC})
renders its price via an empty placeholder div that Drupal fills in
client-side through an AJAX call. The response body isn't plain HTML: it's a
CryptoJS-AES-encrypted JSON blob (``{"ct": ..., "iv": ..., "s": ...}``) that
the page's own JS decrypts with a passphrase found in ``drupalSettings.ajax_secure.kye``.

So fetching the static page HTML (previous approach) never contained the
price. This fetcher instead calls the AJAX endpoint directly and replicates
the CryptoJS decryption (OpenSSL-style EVP_BytesToKey + AES-CBC) to recover
the HTML snippet with the latest traded price.

No historical source is registered — Euronext's public page only exposes
the current day's intraday trades, not a daily OHLCV history.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from lxml import html

from app.sources import DataSource, register_config_validator, register_current

logger = logging.getLogger(__name__)

# --- Scraper Implementation ---

# Fallback passphrase used by the site's ajax_secure module. It's normally
# exposed in drupalSettings.ajax_secure.kye but is stable across requests.
_DEFAULT_AJAX_KEY = "24ayqVo7yJma"

# The ajax_secure key rarely changes, so cache it instead of re-fetching the
# instrument page (an extra GET) on every current-price call. TTL is a safety
# net in case Euronext rotates it; a decrypt failure also forces a refresh.
_KEY_CACHE_TTL = timedelta(minutes=30)
_key_cache_lock = threading.Lock()
_cached_ajax_key: str | None = None
_cached_ajax_key_expiry: datetime | None = None

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


def _http_get(url: str, *, extra_headers: dict | None = None) -> str:
    headers = {**_HEADERS, **(extra_headers or {})}
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(url, timeout=15, headers=headers, impersonate="chrome")
    except Exception:
        import requests

        resp = requests.get(url, timeout=15, headers=headers)
    resp.raise_for_status()
    return resp.text


def _http_post(url: str, *, extra_headers: dict | None = None) -> str:
    headers = {**_HEADERS, **(extra_headers or {})}
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.post(url, timeout=15, headers=headers, impersonate="chrome")
    except Exception:
        import requests

        resp = requests.post(url, timeout=15, headers=headers)
    resp.raise_for_status()
    return resp.text


def _get_ajax_secure_key(page_url: str, *, force_refresh: bool = False) -> str:
    """
    Return the ajax_secure passphrase, cached in-process for `_KEY_CACHE_TTL`.

    Pass `force_refresh=True` to bypass the cache (e.g. after a failed
    decrypt, in case Euronext rotated the key).
    """
    global _cached_ajax_key, _cached_ajax_key_expiry

    with _key_cache_lock:
        if (
            not force_refresh
            and _cached_ajax_key is not None
            and _cached_ajax_key_expiry is not None
            and datetime.utcnow() < _cached_ajax_key_expiry
        ):
            return _cached_ajax_key

    key = _fetch_ajax_secure_key(page_url)

    with _key_cache_lock:
        _cached_ajax_key = key
        _cached_ajax_key_expiry = datetime.utcnow() + _KEY_CACHE_TTL

    return key


def _fetch_ajax_secure_key(page_url: str) -> str:
    """Read drupalSettings.ajax_secure.kye from the instrument page, if present."""
    try:
        page = _http_get(page_url)
        match = re.search(r'"ajax_secure":\{"kye":"([^"]+)"\}', page)
        if match:
            return match.group(1)
    except Exception as exc:
        logger.warning("Could not fetch ajax_secure key, using default: %s", exc)
    return _DEFAULT_AJAX_KEY


def _evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    """OpenSSL's EVP_BytesToKey (MD5, 1 iteration), as used by CryptoJS's OpenSSL format."""
    derived = b""
    block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len : key_len + iv_len]


def _decrypt_cryptojs_aes(payload: dict, password: str) -> str:
    """Decrypt a CryptoJSAesJson payload ({"ct", "iv", "s"}) into its plaintext string."""
    ciphertext = base64.b64decode(payload["ct"])
    salt = bytes.fromhex(payload["s"])
    iv = bytes.fromhex(payload["iv"])
    key, _ = _evp_bytes_to_key(password.encode("ascii"), salt, key_len=32, iv_len=16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode("utf-8"))


def _fetch_current_price(symbol: str, mic: str) -> Optional[float]:
    instrument = f"{symbol}-{mic}"
    page_url = f"https://live.euronext.com/en/product/bonds/{instrument}"
    ajax_url = f"https://live.euronext.com/en/ajax/getIntradayPrice/{instrument}"

    try:
        raw = _http_post(
            ajax_url,
            extra_headers={"Referer": page_url, "X-Requested-With": "XMLHttpRequest"},
        )
        payload = json.loads(raw)

        ajax_key = _get_ajax_secure_key(page_url)
        try:
            table_html = _decrypt_cryptojs_aes(payload, ajax_key)
        except (ValueError, KeyError):
            # Cached key may be stale (Euronext rotated it) — refetch once.
            logger.info("Euronext ajax_secure key looks stale for %s, refreshing.", symbol)
            ajax_key = _get_ajax_secure_key(page_url, force_refresh=True)
            table_html = _decrypt_cryptojs_aes(payload, ajax_key)

        doc = html.fromstring(table_html)
        price_cells = doc.xpath("//table[@id='AwlIntradayPriceTable']//tbody/tr[1]/td[2]/text()")
        if not price_cells or not price_cells[0].strip():
            logger.error("Price cell not found in Euronext intraday-price response for %s", symbol)
            return None

        price_txt = price_cells[0].strip().replace("\xa0", "").replace(",", "")
        return float(price_txt)
    except Exception as exc:
        logger.error("Error fetching current price for %s: %s", symbol, exc)
        return None


# --- Registration ---

@register_config_validator(DataSource.EURONEXT)
def _validate_config(config: dict) -> Optional[str]:
    mic = config.get("mic")
    if not mic or not isinstance(mic, str):
        return "source_config.mic is required for the euronext data source (e.g. {\"mic\": \"MOTX\"})."
    return None


@register_current(DataSource.EURONEXT)
def _current(symbol: str, config: dict) -> dict:
    price = _fetch_current_price(symbol, config["mic"])
    return {
        "price": price,
        "volume": None,
        "timestamp": datetime.utcnow(),
    }
