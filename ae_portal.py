
# -*- coding: utf-8 -*-
# AliExpress Open Platform (Portal) Gateway adapter — TOP protocol (MD5 signature)
import os, time, json, hashlib, requests
from datetime import datetime

GATEWAY = os.getenv("AE_GATEWAY_URL", "https://gw.api.taobao.com/router/rest")
APP_KEY = os.getenv("AE_APP_KEY", "")
APP_SECRET = os.getenv("AE_APP_SECRET", "")
TRACKING_ID = os.getenv("AE_TRACKING_ID", "")
TIMEOUT = int(os.getenv("AE_API_TIMEOUT", "25"))

def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _sign(params: dict, secret: str) -> str:
    pieces = []
    for k in sorted(params.keys()):
        v = "" if params[k] is None else str(params[k])
        pieces.append(f"{k}{v}")
    base = f"{secret}{''.join(pieces)}{secret}"
    return hashlib.md5(base.encode("utf-8")).hexdigest().upper()

def _call(method: str, biz_params: dict) -> dict:
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError("חסרים AE_APP_KEY / AE_APP_SECRET ב־ENV")

    p = {
        "app_key": APP_KEY,
        "method": method,
        "format": "json",
        "sign_method": "md5",
        "v": "2.0",
        "timestamp": _timestamp(),
    }
    flat = {k: ("" if v is None else v) for k, v in biz_params.items()}
    payload = {**p, **flat}
    payload["sign"] = _sign(payload, APP_SECRET)

    try:
        r = requests.post(GATEWAY, data=payload, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except ValueError:
        preview = (r.text or "")[:400].replace("\n", " ")
        raise RuntimeError(f"לא הצלחתי לקרוא JSON מה־Gateway (preview={preview})")
    except Exception as e:
        raise RuntimeError(f"שגיאת רשת/HTTP בקריאה ל־Gateway: {e}")

    if isinstance(data, dict) and "error_response" in data:
        err = data["error_response"]
        code = err.get("code")
        sub = err.get("sub_msg") or err.get("msg") or str(err)
        raise RuntimeError(f"Gateway error {code}: {sub}")

    return data

def _extract_products_any(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    paths = [
        ["aliexpress_affiliate_product_query_response", "resp_result", "result", "products"],
        ["aliexpress_affiliate_hotproduct_query_response", "resp_result", "result", "products"],
        ["aliexpress_affiliate_productdetail_get_response", "resp_result", "result", "products"],
        ["result", "result", "products"],
        ["resp_result", "result", "products"],
    ]
    for path in paths:
        node = data
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, list):
            return node
    # fallback: first list anywhere
    def any_list(d):
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for v in d.values():
                got = any_list(v)
                if got: return got
        return None
    return any_list(data) or []

def affiliate_product_query_by_category(category_id: str, page_no=1, page_size=10,
                                       country="IL", keywords=None, sort="orders_desc") -> list:
    method = "aliexpress.affiliate.product.query"
    biz = {
        "category_ids": category_id,
        "target_country": country,
        "page_no": page_no,
        "page_size": page_size,
        "sort": sort,
    }
    if keywords:
        biz["keywords"] = keywords
    if TRACKING_ID:
        biz["tracking_id"] = TRACKING_ID

    raw = _call(method, biz)
    prods = _extract_products_any(raw)
    if not prods:
        raise RuntimeError(f"לא נמצאו מוצרים ב־Gateway (method={method})")
    return prods
