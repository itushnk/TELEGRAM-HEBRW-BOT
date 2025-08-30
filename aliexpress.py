
import os
import json
import requests

# ---- ENV / Config ----
API_KEY = os.getenv("AE_API_APP_KEY") or os.getenv("AE_RAPIDAPI_KEY") or ""
API_SECRET = os.getenv("AE_API_APP_SECRET", "")
BASE_URL = os.getenv("AE_API_BASE_URL", "https://api.aliexpress.com/v1/products/search")
API_HOST = os.getenv("AE_API_HOST", "")  # RapidAPI style providers
TIMEOUT = int(os.getenv("AE_API_TIMEOUT", "25"))
METHOD = (os.getenv("AE_API_METHOD", "GET") or "GET").upper()

# Allow param key remapping via ENV (providers differ)
PARAM_CATEGORY = os.getenv("AE_PARAM_CATEGORY_KEY", "category_id")
PARAM_SHIPTO  = os.getenv("AE_PARAM_SHIPTO_KEY",  "target_country")
PARAM_PAGE    = os.getenv("AE_PARAM_PAGE_KEY",    "page_size")
PARAM_SORT    = os.getenv("AE_PARAM_SORT_KEY",    "sort")

# Allow appending/overriding custom params from JSON string
EXTRA_PARAMS = {}
try:
    if os.getenv("AE_API_PARAMS_JSON"):
        EXTRA_PARAMS = json.loads(os.getenv("AE_API_PARAMS_JSON"))
except Exception:
    EXTRA_PARAMS = {}

def _headers():
    h = {}
    # RapidAPI style
    if API_HOST:
        h["X-RapidAPI-Key"] = API_KEY
        h["X-RapidAPI-Host"] = API_HOST
    else:
        if API_KEY:
            h["X-API-KEY"] = API_KEY
            h["Authorization"] = f"Bearer {API_KEY}"
    # Common JSON acceptance
    h.setdefault("Accept", "application/json, text/plain;q=0.9, */*;q=0.5")
    return h

def _safe_json_parse(text: str):
    """Be tolerant to BOM, prefaces, and non-JSON leading junk."""
    if text is None:
        raise ValueError("Empty response body")
    s = text.lstrip("\ufeff \t\r\n")
    # Cut to first JSON-looking char
    first_brace = s.find("{")
    first_bracket = s.find("[")
    idxs = [i for i in (first_brace, first_bracket) if i != -1]
    if idxs:
        cut = min(idxs)
        if cut > 0:
            s = s[cut:]
    return json.loads(s)

def _extract_products(data: dict):
    """Support many typical vendor shapes."""
    if not isinstance(data, dict):
        return []
    # flat
    for k in ("products", "items", "list", "result_list"):
        if isinstance(data.get(k), list):
            return data[k]
    # nested
    candidates = [
        ("result", "result_list"),
        ("result", "items"),
        ("result", "products"),
        ("data", "list"),
        ("data", "items"),
        ("data", "products"),
        ("resp", "result"),
        ("resp", "items"),
    ]
    for a, b in candidates:
        if isinstance(data.get(a), dict):
            v = data[a].get(b)
            if isinstance(v, list):
                return v
    # first list anywhere
    for v in data.values():
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list) and vv:
                    return vv
    return []

def fetch_products_by_category(category_id: str, page_size: int = 5, ship_to: str = "IL"):
    if not (API_KEY or API_HOST):
        raise RuntimeError("AE_API_APP_KEY/AE_RAPIDAPI_KEY חסר ב-ENV (וגם AE_API_HOST אם זה RapidAPI)")

    params = {
        PARAM_CATEGORY: category_id,
        PARAM_PAGE: page_size,
        PARAM_SORT: "orders_desc",
        PARAM_SHIPTO: ship_to,
    }
    if EXTRA_PARAMS:
        params.update(EXTRA_PARAMS)

    headers = _headers()
    try:
        if METHOD == "POST":
            resp = requests.post(BASE_URL, headers=headers, json=params, timeout=TIMEOUT)
        else:
            resp = requests.get(BASE_URL, headers=headers, params=params, timeout=TIMEOUT)

        ct = resp.headers.get("Content-Type", "")
        preview = (resp.text or "")[:400].replace("\n", " ").replace("\r", " ")
        print(f"[AE] {METHOD} {resp.url} -> {resp.status_code} | CT={ct} | body[:400]={preview}", flush=True)
        resp.raise_for_status()
        try:
            # Prefer official json() but fall back to tolerant parser
            try:
                data = resp.json()
            except Exception:
                data = _safe_json_parse(resp.text or "")
        except Exception as je:
            raise RuntimeError(f"לא הצלחתי לקרוא JSON: {je}. CT={ct} PREVIEW={preview}")
    except Exception as e:
        raise RuntimeError(f"תקלה בקריאת ה-API: {e}")

    products = _extract_products(data)
    if not products:
        keys = list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__
        raise RuntimeError(f"לא נמצאו מוצרים בתגובה (keys={keys})")
    return products
