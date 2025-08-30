
import os, requests

API_KEY = os.getenv("AE_API_APP_KEY", "")
API_SECRET = os.getenv("AE_API_APP_SECRET", "")  # ייתכן ולא בשימוש לפי ספק ה-API
BASE_URL = os.getenv("AE_API_BASE_URL", "https://api.aliexpress.com/v1/products/search")

def fetch_products_by_category(category_id: str, page_size: int = 5, ship_to: str = "IL"):
    if not API_KEY:
        raise RuntimeError("AE_API_APP_KEY חסר ב-ENV")
    headers = {"X-API-KEY": API_KEY}
    params = {"category_id": category_id, "page_size": page_size, "sort": "orders_desc", "target_country": ship_to}
    r = requests.get(BASE_URL, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    data = r.json() or {}
    prods = data.get("products") or data.get("result") or data.get("data") or []
    if isinstance(prods, dict):
        prods = prods.get("items") or []
    return prods
