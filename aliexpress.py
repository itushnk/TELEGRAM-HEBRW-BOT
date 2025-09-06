# -*- coding: utf-8 -*-
import os, time, csv, json, re
from datetime import datetime
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")
os.makedirs(BASE_DIR, exist_ok=True)
QUEUE_FILE = os.path.join(BASE_DIR, "queue.csv")
HEADER = ["ItemId","Title","Price","Currency","Url","Image","Category","CreatedAt"]

def _ensure_queue_header():
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as f:
            import csv as _csv
            _csv.writer(f).writerow(HEADER)

def _existing_ids():
    if not os.path.exists(QUEUE_FILE): return set()
    ids=set()
    try:
        with open(QUEUE_FILE,"r",encoding="utf-8-sig") as f:
            import csv as _csv
            for i,row in enumerate(_csv.reader(f)):
                if i==0: continue
                if row and row[0]: ids.add(row[0])
    except Exception: pass
    return ids

def _append_items(items):
    _ensure_queue_header()
    known = _existing_ids()
    added = 0
    ts = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    with open(QUEUE_FILE, "a", newline="", encoding="utf-8-sig") as f:
        import csv as _csv
        w = _csv.writer(f)
        for it in items:
            pid = str(it.get("ItemId") or it.get("productId") or it.get("item_id") or "")
            if not pid or pid in known: continue
            title = it.get("Title") or it.get("title") or ""
            price = it.get("Price") or it.get("price") or ""
            cur = it.get("Currency") or it.get("currency") or os.getenv("BOT_CURRENCY","ILS")
            url = it.get("Url") or it.get("url") or ""
            img = it.get("Image") or it.get("image") or it.get("imageUrl") or ""
            cat = it.get("Category") or it.get("category") or ""
            w.writerow([pid, title, price, cur, url, img, cat, ts])
            known.add(pid); added += 1
    return added

def _make_sess():
    s = requests.Session()
    retry = Retry(total=int(os.getenv("AE_RETRY_TOTAL","2")),
                  connect=int(os.getenv("AE_RETRY_TOTAL","2")),
                  read=int(os.getenv("AE_RETRY_TOTAL","2")),
                  backoff_factor=float(os.getenv("AE_RETRY_BACKOFF","1.2")),
                  status_forcelist=[429,500,502,503,504],
                  allowed_methods=frozenset(["GET","POST"]))
    ad = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    s.mount("https://", ad); s.mount("http://", ad)
    http_proxy = os.getenv("AE_HTTP_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("AE_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or http_proxy
    proxies = {}
    if http_proxy: proxies["http"] = http_proxy
    if https_proxy: proxies["https"] = https_proxy
    if proxies: s.proxies.update(proxies)
    s.headers.update({
        "User-Agent": os.getenv("AE_UA","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"),
        "Accept-Language": os.getenv("AE_ACCEPT_LANG","he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"),
        "Cache-Control": "no-cache",
    })
    return s

def _api_fetch(category_or_query, limit=12):
    APP_KEY = os.getenv("AE_APP_KEY") or os.getenv("AE_API_APP_KEY") or ""
    APP_SECRET = os.getenv("AE_APP_SECRET") or os.getenv("AE_API_APP_SECRET") or ""
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError("Missing AE_APP_KEY/AE_APP_SECRET")
    payload = {
        "method": "aliexpress.affiliate.product.query",
        "app_key": APP_KEY,
        "sign_method": "md5",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "category_id": str(category_or_query),
        "fields": "product_id,product_title,product_main_image_url,app_sale_price,app_sale_price_currency,product_detail_url",
        "target_currency": os.getenv("BOT_CURRENCY","ILS"),
        "ship_to_country": os.getenv("AE_SHIP_TO","IL"),
        "page_size": str(limit),
        "sort": "sale_price_asc"
    }
    sess = _make_sess()
    gateways = [g.strip() for g in (os.getenv("AE_GATEWAY_LIST") or "https://gw.api.taobao.com/router/rest,https://eco.taobao.com/router/rest").split(",") if g.strip()]
    last = None
    for gw in gateways:
        try:
            r = sess.post(gw, data=payload, timeout=(float(os.getenv("AE_CONNECT_TIMEOUT","10")), float(os.getenv("AE_READ_TIMEOUT","20"))))
            r.raise_for_status()
            data = r.json()
            # Try dig a list of products
            def find_products(obj):
                if isinstance(obj, dict):
                    if "products" in obj and isinstance(obj["products"], list):
                        return obj["products"]
                    for v in obj.values():
                        res = find_products(v)
                        if res is not None: return res
                return None
            products = find_products(data) or []
            out = []
            for p in products:
                pid = str(p.get("product_id") or p.get("item_id") or "")
                if not pid: continue
                out.append({
                    "ItemId": pid,
                    "Title": p.get("product_title") or p.get("title") or "",
                    "Price": p.get("app_sale_price") or p.get("sale_price") or "",
                    "Currency": p.get("app_sale_price_currency") or p.get("currency") or os.getenv("BOT_CURRENCY","ILS"),
                    "Url": p.get("product_detail_url") or p.get("url") or "",
                    "Image": p.get("product_main_image_url") or p.get("image_url") or "",
                    "Category": str(category_or_query)
                })
            return out
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"AE API failed: {last}")

def _extract_items_from_json(obj):
    found=[]
    def rec(o):
        if isinstance(o, dict):
            id_key=None
            for k in ["productId","product_id","itemId","item_id","id"]:
                if k in o: id_key=k; break
            title = o.get("title") or o.get("productTitle") or o.get("product_title")
            url = o.get("productDetailUrl") or o.get("product_detail_url") or o.get("productUrl") or o.get("url")
            img = o.get("image") or o.get("imageUrl") or o.get("productMainImageUrl") or o.get("product_main_image_url") or ""
            price = o.get("appSalePrice") or o.get("salePrice") or o.get("price") or ""
            cur = o.get("currency") or o.get("currencyCode") or os.getenv("BOT_CURRENCY","ILS")
            if id_key and title and url:
                found.append({"ItemId": str(o[id_key]), "Title": title, "Price": price, "Currency": cur, "Url": url, "Image": img, "Category": ""})
            for v in o.values(): rec(v)
        elif isinstance(o, list):
            for it in o: rec(it)
    rec(obj)
    uniq={}
    for it in found: uniq[it["ItemId"]]=it
    return list(uniq.values())

def _scrape_fetch(category_or_query, limit=12):
    query=str(category_or_query)
    params={"SearchText": query, "ShipCountry": os.getenv("AE_SHIP_TO","IL"), "SortType":"total_tranpro_desc", "g":"y"}
    url="https://www.aliexpress.com/wholesale?"+urlencode(params, doseq=True)
    sess=_make_sess()
    sess.headers.update({"Referer":"https://www.aliexpress.com/"})
    cookies={"xman_us_f":"x_lan=he_IL&x_locale=he_IL&region=IL&b_locale=he_IL"}
    r=sess.get(url, timeout=(float(os.getenv("AE_CONNECT_TIMEOUT","10")), float(os.getenv("AE_READ_TIMEOUT","20"))), cookies=cookies)
    r.raise_for_status()
    html=r.text
    items=[]
    for pat in [r"window\.__AER_DATA__\s*=\s*(\{.*?\});", r"window\.runParams\s*=\s*(\{.*?\});"]:
        m=re.search(pat, html, re.S)
        if not m: continue
        raw=m.group(1).strip().rstrip(";")
        try:
            data=json.loads(raw)
            items.extend(_extract_items_from_json(data))
        except Exception:
            continue
    if not items:
        for m in re.finditer(r'href="(https://www\.aliexpress\.com/item/[^"]+)"[^>]*>([^<]{10,120})</a>', html):
            url, title = m.group(1), m.group(2).strip()
            items.append({"ItemId": str(abs(hash(url))), "Title": title, "Price":"", "Currency": os.getenv("BOT_CURRENCY","ILS"), "Url": url, "Image":"", "Category":""})
    # Dedup & limit
    seen=set(); out=[]
    for it in items:
        pid=it.get("ItemId"); title=it.get("Title"); url=it.get("Url")
        if not pid or not title or not url: continue
        if pid in seen: continue
        seen.add(pid); it["Category"]=query; out.append(it)
        if len(out)>=limit: break
    return out

def fetch_products_by_category(category_id_or_query, limit=12):
    items=[]
    if os.getenv("AE_USE_API_FIRST","1")!="0":
        try:
            items=_api_fetch(category_id_or_query, limit=limit)
        except Exception as e:
            print(f"[AE][API][WARN] {e}")
    if not items:
        try:
            items=_scrape_fetch(category_id_or_query, limit=limit)
        except Exception as e:
            print(f"[AE][SCRAPE][ERR] {e}")
            items=[]
    if not items: return 0
    return _append_items(items)
