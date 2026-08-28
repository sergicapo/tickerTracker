from lxml import html

url = "https://live.euronext.com/en/product/bonds/XS2538440780-MOTX"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

try:
    from curl_cffi import requests as cffi_requests

    r = cffi_requests.get(url, timeout=25, headers=headers, impersonate="chrome")
    print("via curl_cffi")
except Exception as exc:
    import requests

    print("curl_cffi failed", exc)
    r = requests.get(url, timeout=25, headers=headers)
    print("via requests")

print("status", r.status_code, "len", len(r.text))
text = r.text
for needle in [
    "Last",
    "last",
    "price",
    "LastPrice",
    "lastPrice",
    "quote",
    "instrument",
    "XS2538440780",
    "data-price",
    "Last traded",
]:
    print(needle, text.find(needle))

# dump interesting json/api-looking bits
import re

for m in re.finditer(r"https?://[^\"']+api[^\"']+", text):
    print("api", m.group(0)[:200])

doc = html.fromstring(text)
for sel in [
    "//span[contains(@class,'last')]",
    "//div[contains(@class,'last')]",
    "//*[@data-field='last']",
    "//*[contains(@class,'Price')]",
    "//*[contains(@class,'price')]",
]:
    nodes = doc.xpath(sel)
    print(sel, len(nodes))
    for n in nodes[:8]:
        print(" ", n.tag, n.get("class"), repr(" ".join(n.text_content().split())[:80]))

# write snippet around last
idx = text.lower().find("last")
print("--- around last ---")
if idx >= 0:
    print(text[idx : idx + 400])
