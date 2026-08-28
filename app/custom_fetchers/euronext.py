"""
Custom fetcher for XS2538440780 from live.euronext.com.

The instrument page (https://live.euronext.com/en/product/bonds/XS2538440780-MOTX)
renders its price via an empty placeholder div that Drupal fills in
client-side through an AJAX call. The response body isn't plain HTML: it's a
CryptoJS-AES-encrypted JSON blob (``{"ct": ..., "iv": ..., "s": ...}``) that
the page's own JS decrypts with a passphrase found in ``drupalSettings.ajax_secure.kye``.

So fetching the static page HTML (previous approach) never contained the
price. This fetcher instead calls the AJAX endpoint directly and replicates
the CryptoJS decryption (OpenSSL-style EVP_BytesToKey + AES-CBC) to recover
the HTML snippet with the latest traded price.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import datetime

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from lxml import html

from app.custom_fetchers import register_current

logger = logging.getLogger(__name__)

# --- Scraper Implementation ---

_BOND_SYMBOL = "XS2538440780"
_INSTRUMENT = f"{_BOND_SYMBOL}-MOTX"
_PAGE_URL = f"https://live.euronext.com/en/product/bonds/{_INSTRUMENT}"
_AJAX_URL = f"https://live.euronext.com/en/ajax/getIntradayPrice/{_INSTRUMENT}"

# Fallback passphrase used by the site's ajax_secure module. It's normally
# exposed in drupalSettings.ajax_secure.kye but is stable across requests.
_DEFAULT_AJAX_KEY = "24ayqVo7yJma"

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


def _fetch_ajax_secure_key() -> str:
    """Read drupalSettings.ajax_secure.kye from the instrument page, if present."""
    try:
        page = _http_get(_PAGE_URL)
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


def _fetch_current_xs2538440780() -> float | None:
    try:
        raw = _http_post(
            _AJAX_URL,
            extra_headers={"Referer": _PAGE_URL, "X-Requested-With": "XMLHttpRequest"},
        )
        payload = json.loads(raw)
        ajax_key = _fetch_ajax_secure_key()
        table_html = _decrypt_cryptojs_aes(payload, ajax_key)

        doc = html.fromstring(table_html)
        price_cells = doc.xpath("//table[@id='AwlIntradayPriceTable']//tbody/tr[1]/td[2]/text()")
        if not price_cells or not price_cells[0].strip():
            logger.error("Price cell not found in Euronext intraday-price response for %s", _BOND_SYMBOL)
            return None

        price_txt = price_cells[0].strip().replace("\xa0", "").replace(",", "")
        return float(price_txt)
    except Exception as exc:
        logger.error("Error fetching current price for %s: %s", _BOND_SYMBOL, exc)
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
