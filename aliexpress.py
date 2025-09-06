# -*- coding: utf-8 -*-
import os, time, csv, json, re, random
from datetime import datetime
from urllib.parse import urlencode, quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")
os.makedirs(BASE_DIR, exist_ok=True)
QUEUE_FILE = os.path.join(BASE_DIR, "queue.csv")

# ===== Queue helpers =====
HEADER = ["ItemId","Title","Price","Currency","Url","Image","Category","CreatedAt"]

def _ensure_queue_header():
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(HEADER)

def _existing_ids():
    if not os.path.exists(QUEUE_FILE):
        return set()
    ids = set()
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8-sig") as f:
            for i, row in enumerate(csv.reader(f)):
                if i == 0: 
                    continue
                if row and row[0]:
                    ids.add(row[0])
    except Exception:
        pass
    return ids

def _append_items(items):
    _ensure_queue_header()
    ids = _existing_ids()
    added = 0
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    with open(QUEUE_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for it in items:
            pid = str(it.get("ItemId") or it.get("item_id") or it.get("productId") or "")
            if not pid or pid in ids:
                continue
            title = it.get("Title") or it.get("title") or ""
            price = it.get("Price") or it.get("price") or ""
            currency = it.get("Currency") or it.get("currency") or os.getenv("BOT_CURRENCY","ILS")
            url = it.get("Url") or it.get("url") or ""
            image = it.get("Image") or it.get("image") or it.get("imageUrl") or ""
            cat = it.get("Category") or it.get("category") or ""
            w.writerow([pid, title, price, currency, url, image, cat, now])
            ids.add(pid)
            added += 1
    return added

# ===== HTTP session with retry/proxy =====
def _make_sess():
    s = requests.Session()
    total = int(os.getenv("AE_RETRY_TOTAL", "2"))
    backoff = float(os.getenv("AE_RETRY_BACKOFF", "1.2"))
    status = [int(x) for x in (os.getenv("AE_RETRY_STATUS","429,500,502,503,504").split(",")) if x.strip().isdigit()]
    retry = Retry(total=total, connect=total, read=total, backoff_factor=backoff,
                  status_forcelist=status, allowed_methods=frozenset(["GET","POST"]))
    ad = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    s.mount("https://", ad); s.mount("http://", ad)
    http_proxy = os.getenv("AE_HTTP_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("AE_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or http_proxy
    proxies = {}
    if http_proxy: proxies["http"] = http_proxy
    if https_proxy: proxies["https"] = https_proxy
    if proxies:
        s.proxies.update(proxies)
    s.headers.update({
        "User-Agent": os.getenv("AE_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept-Language": os.getenv("AE_ACCEPT_LANG", "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"),
        "Cache-Control": "no-cache",
    })
    return s

# ===== AliExpress API (Affiliate via Taobao gateway) =====
def _api_fetch(category_id, limit=12):
    APP_KEY = os.getenv("AE_APP_KEY") or os.getenv("AE_API_APP_KEY") or ""
    APP_SECRET = os.getenv("AE_APP_SECRET") or os.getenv("AE_API_APP_SECRET") or ""
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError("Missing AE_APP_KEY/AE_APP_SECRET")
    # NOTE: Implement a simple call to an open method; some deployments may require signatures.
    # If this fails due to region/timeout, the caller will fallback to scraping.
    # This placeholder sends a POST to gateway with minimal params (adjusted by deployer).
    payload = {
        "method": "aliexpress.affiliate.product.query",
        "app_key": APP_KEY,
        "sign_method": "md5",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "category_id": str(category_id),
        "fields": "product_id,product_title,product_main_image_url,app_sale_price,app_sale_price_currency,product_detail_url",
        "target_currency": os.getenv("BOT_CURRENCY","ILS"),
        "ship_to_country": os.getenv("AE_SHIP_TO","IL"),
        "page_size": str(limit),
        "sort": "sale_price_asc"
    }
    # Signature omitted for brevity; if your keys require sign, add MD5 of secret+sorted_params+secret.
    sess = _make_sess()
    gateways = [g.strip() for g in (os.getenv("AE_GATEWAY_LIST") or "https://gw.api.taobao.com/router/rest,https://eco.taobao.com/router/rest").split(",") if g.strip()]
    last_err = None
    for gw in gateways:
        try:
            r = sess.post(gw, data=payload, timeout=(float(os.getenv("AE_CONNECT_TIMEOUT","10")), float(os.getenv("AE_READ_TIMEOUT","20"))))
            r.raise_for_status()
            data = r.json()
            # Map fields (best effort; actual structure may vary)
            products = []
            raw_list = None
            for k in ["resp_result", "aliexpress_affiliate_product_query_response", "result"]:
                if isinstance(data, dict) and k in data:
                    raw_list = data[k]
                    break
            if raw_list is None:
                raw_list = data
            # Try find 'products' key recursively
            def find_products(obj):
                if isinstance(obj, dict):
                    if "products" in obj and isinstance(obj["products"], list):
                        return obj["products"]
                    for v in obj.values():
                        res = find_products(v)
                        if res is not None:
                            return res
                return None
            prods = find_products(raw_list) or []
            for p in prods:
                pid = str(p.get("product_id") or p.get("item_id") or "")
                if not pid: 
                    continue
                products.append({
                    "ItemId": pid,
                    "Title": p.get("product_title") or p.get("title") or "",
                    "Price": p.get("app_sale_price") or p.get("sale_price") or "",
                    "Currency": p.get("app_sale_price_currency") or p.get("currency") or os.getenv("BOT_CURRENCY","ILS"),
                    "Url": p.get("product_detail_url") or p.get("url") or "",
                    "Image": p.get("product_main_image_url") or p.get("image_url") or "",
                    "Category": str(category_id)
                })
            return products
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"AE API failed: {last_err}")

# ===== Scrape fallback =====
def _extract_items_from_json(obj):
    found = []
    def rec(o):
        if isinstance(o, dict):
            # Consider product-like dicts
            id_key = None
            for k in ["productId","product_id","itemId","item_id","id"]:
                if k in o:
                    id_key = k; break
            title = o.get("title") or o.get("productTitle") or o.get("product_title")
            url = o.get("productDetailUrl") or o.get("product_detail_url") or o.get("productUrl") or o.get("url")
            img = o.get("image","") or o.get("imageUrl") or o.get("productMainImageUrl") or o.get("product_main_image_url")
            price = o.get("appSalePrice") or o.get("salePrice") or o.get("price")
            currency = o.get("currency") or o.get("currencyCode") or os.getenv("BOT_CURRENCY","ILS")
            if id_key and title and url:
                pid = str(o.get(id_key))
                found.append({
                    "ItemId": pid, "Title": title, "Price": price or "", "Currency": currency or "",
                    "Url": url, "Image": img or "", "Category": ""
                })
            for v in o.values():
                rec(v)
        elif isinstance(o, list):
            for it in o:
                rec(it)
    rec(obj)
    # Dedup by id
    uniq = {}
    for it in found:
        uniq[it["ItemId"]] = it
    return list(uniq.values())

def _scrape_fetch(category_or_query, limit=12):
    query = str(category_or_query)
    # If numeric id, we can still use it as a query term.
    params = {
        "SearchText": query,
        "ShipCountry": os.getenv("AE_SHIP_TO", "IL"),
        "SortType": "total_tranpro_desc",
        "g": "y"
    }
    base = "https://www.aliexpress.com/wholesale"
    url = base + "?" + urlencode(params, doseq=True)
    sess = _make_sess()
    sess.headers.update({
        "Referer": "https://www.aliexpress.com/",
    })
    cookies = {
        "xman_us_f": "x_lan=he_IL&x_locale=he_IL&region=IL&b_locale=he_IL"
    }
    r = sess.get(url, timeout=(float(os.getenv("AE_CONNECT_TIMEOUT","10")), float(os.getenv("AE_READ_TIMEOUT","20"))), cookies=cookies)
    r.raise_for_status()
    html = r.text
    # Known embed patterns
    patterns = [
        r"window\.__AER_DATA__\s*=\s*(\{.*?\});",
        r"window\.runParams\s*=\s*(\{.*?\});"
    ]
    items = []
    for pat in patterns:
        m = re.search(pat, html, re.S)
        if not m: 
            continue
        raw = m.group(1)
        raw = raw.strip().rstrip(";")
        try:
            data = json.loads(raw)
            cand = _extract_items_from_json(data)
            if cand:
                items.extend(cand)
        except Exception:
            continue
    # Fallback: link parsing (weak)
    if not items:
        for m in re.finditer(r'href="(https://www\.aliexpress\.com/item/[^"]+)"[^>]*>([^<]{10,120})</a>', html):
            url, title = m.group(1), m.group(2)
            items.append({"ItemId": str(abs(hash(url))), "Title": title.strip(), "Price":"", "Currency": os.getenv("BOT_CURRENCY","ILS"), "Url": url, "Image":"", "Category":""})
    out = []
    for it in items[:limit*2]:
        if it.get("ItemId") and it.get("Title") and it.get("Url"):
            out.append({
                "ItemId": str(it["ItemId"]),
                "Title": it["Title"],
                "Price": it.get("Price",""),
                "Currency": it.get("Currency") or os.getenv("BOT_CURRENCY","ILS"),
                "Url": it["Url"],
                "Image": it.get("Image",""),
                "Category": query
            })
    seen = set(); final = []
    for it in out:
        if it["ItemId"] in seen: 
            continue
        seen.add(it["ItemId"])
        final.append(it)
        if len(final) >= limit:
            break
    return final

# ===== Public function =====
def fetch_products_by_category(category_id_or_query, limit=12):
    """
    Fetch products from AliExpress by category id or free-text query.
    Returns: number of items appended to queue.csv
    """
    items = []
    api_first = os.getenv("AE_USE_API_FIRST","1") != "0"
    if api_first:
        try:
            items = _api_fetch(category_id_or_query, limit=limit)
        except Exception as e:
            print(f"[AE][API][WARN] {e}")
    if not items:
        try:
            items = _scrape_fetch(category_id_or_query, limit=limit)
        except Exception as e:
            print(f"[AE][SCRAPE][ERR] {e}")
            items = []
    if not items:
        return 0
    return _append_items(items)
