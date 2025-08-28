
# -*- coding: utf-8 -*-
"""
AliExpress Affiliates helper for Telegram bot/CSV pipelines.
- REST base: https://api-sg.aliexpress.com/rest/
- Implements SHA256 HMAC signing over sorted key=value pairs (uppercase hex).
- Focus endpoints:
    * aliexpress/affiliate/link/generate
    * aliexpress/affiliate/product/query
    * aliexpress/affiliate/productdetail/get

Usage (standalone CLI):
    python aliexpress_affiliate.py enrich --in products.csv --out products_enriched.csv
    python aliexpress_affiliate.py hot --keyword "Bluetooth" --out hot.csv --count 10 --min_discount 40

Env vars (or pass explicitly when creating the client):
    AE_APP_KEY, AE_APP_SECRET, AE_TRACKING_ID, AE_TARGET_CURRENCY, AE_TARGET_LANGUAGE, AE_SHIP_TO_COUNTRY
"""
from __future__ import annotations
import os, time, csv, hmac, hashlib, requests
from typing import Any, Dict, List, Optional

REST_BASE = "https://api-sg.aliexpress.com/rest/"
DEFAULT_TIMEOUT = 20

def _now_ms() -> int:
    return int(time.time() * 1000)

def _hmac_sha256_upper(secret: str, base: str) -> str:
    return hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest().upper()

class AliExpressAffiliateClient:
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        tracking_id: Optional[str] = None,
        target_currency: str = None,
        target_language: str = None,
        ship_to_country: str = None,
        session: Optional[str] = None,  # if an endpoint requires it
    ):
        self.app_key = app_key or os.getenv("AE_APP_KEY", "").strip()
        self.app_secret = app_secret or os.getenv("AE_APP_SECRET", "").strip()
        self.tracking_id = tracking_id or os.getenv("AE_TRACKING_ID", "").strip()
        self.target_currency = (target_currency or os.getenv("AE_TARGET_CURRENCY") or "ILS").strip()
        self.target_language = (target_language or os.getenv("AE_TARGET_LANGUAGE") or "HE").strip()
        self.ship_to_country = (ship_to_country or os.getenv("AE_SHIP_TO_COUNTRY") or "IL").strip()
        self.session = session or os.getenv("AE_ACCESS_TOKEN")  # rarely needed for affiliate public endpoints

        if not self.app_key or not self.app_secret:
            raise RuntimeError("Missing AE_APP_KEY/AE_APP_SECRET. Set env vars or pass to constructor.")

    # ---- Core request/sign ----
    def _sign(self, params: Dict[str, Any]) -> str:
        # AliExpress: concatenate sorted key+value (no separators), HMAC-SHA256 with secret, UPPER hex
        base = "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
        return _hmac_sha256_upper(self.app_secret, base)

    def _rest(self, api_path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        q = dict(params)
        q.setdefault("app_key", self.app_key)
        q.setdefault("timestamp", _now_ms())
        q.setdefault("sign_method", "sha256")
        if self.session:
            # Only if needed by that endpoint
            q.setdefault("session", self.session)

        q["sign"] = self._sign(q)
        url = REST_BASE + api_path.lstrip("/")
        r = requests.get(url, params=q, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # normalize known envelope shapes
        for key in ("resp_result", "aliexpress_affiliate_link_generate_response", "aliexpress_affiliate_product_query_response"):
            if key in data:
                # Some responses nest again inside 'result'
                inner = data.get(key)
                if isinstance(inner, dict) and "result" in inner:
                    return inner["result"]
                return inner
        return data

    # ---- Public helpers ----
    def generate_affiliate_link(self, source_values: str, promotion_link_type: int = 0) -> Optional[str]:
        api = "aliexpress/affiliate/link/generate"
        params = {
            "source_values": source_values,
            "promotion_link_type": promotion_link_type,
            "tracking_id": self.tracking_id,
        }
        res = self._rest(api, params)
        links = (res.get("promotion_links") if isinstance(res, dict) else None) or []
        if links:
            return links[0].get("promotion_link") or links[0].get("promotion_target_link") or links[0].get("promotion_short_link")
        return None

    def query_products(
        self,
        keywords: Optional[str] = None,
        page_no: int = 1,
        page_size: int = 20,
        min_discount: Optional[int] = None,
        min_rating: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        api = "aliexpress/affiliate/product/query"
        params = {
            "page_no": page_no,
            "page_size": page_size,
            "target_currency": self.target_currency,
            "target_language": self.target_language,
            "ship_to_country": self.ship_to_country,
        }
        if keywords:
            params["keywords"] = keywords
        res = self._rest(api, params)
        products = res.get("products") or []
        out = []
        for p in products:
            discount = int(p.get("discount") or 0)
            rating = float(p.get("evaluate_rate") or 0.0)
            if min_discount is not None and discount < min_discount:
                continue
            if min_rating is not None and rating < min_rating:
                continue
            out.append({
                "product_id": p.get("product_id"),
                "title": p.get("product_title"),
                "image": p.get("product_main_image_url"),
                "orig_price": p.get("target_original_price"),
                "sale_price": p.get("target_sale_price"),
                "discount": discount,
                "rating": rating,
                "orders": p.get("orders"),
                "detail_url": p.get("product_detail_url"),
            })
        return out

    def product_detail(self, product_id: str) -> Dict[str, Any]:
        api = "aliexpress/affiliate/productdetail/get"
        params = {
            "product_ids": product_id,
            "target_currency": self.target_currency,
            "target_language": self.target_language,
            "ship_to_country": self.ship_to_country,
        }
        res = self._rest(api, params)
        if isinstance(res, dict):
            items = res.get("products") or res.get("product_detail_response") or []
            if isinstance(items, list) and items:
                return items[0]
        return res

    def enrich_csv(self, in_path: str, out_path: str, rate_limit_sec: float = 0.6) -> int:
        import csv, time
        cnt = 0
        with open(in_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        headers = rows[0].keys() if rows else []
        if "Promotion Url" not in headers:
            headers = list(headers) + ["Promotion Url"]
        new_rows = []
        for row in rows:
            changed = False
            if not row.get("Promotion Url"):
                source = row.get("Product Detail Url") or row.get("ProductId") or row.get("Source Url")
                if source:
                    link = self.generate_affiliate_link(str(source))
                    if link:
                        row["Promotion Url"] = link
                        changed = True
                        time.sleep(rate_limit_sec)
            need_detail = any(not row.get(k) for k in ("Origin Price", "Discount Price", "Discount", "Positive Feedback", "Orders", "Image Url"))
            if need_detail:
                pid = row.get("ProductId")
                product_id = None
                if pid and str(pid).strip().isdigit():
                    product_id = str(pid).strip()
                if product_id:
                    d = self.product_detail(product_id)
                    def _g(obj, key, default=None):
                        return (obj.get(key) if isinstance(obj, dict) else default) or default
                    row["Origin Price"] = row.get("Origin Price") or _g(d, "target_original_price")
                    row["Discount Price"] = row.get("Discount Price") or _g(d, "target_sale_price")
                    row["Discount"] = row.get("Discount") or _g(d, "discount")
                    row["Positive Feedback"] = row.get("Positive Feedback") or _g(d, "evaluate_rate")
                    row["Orders"] = row.get("Orders") or _g(d, "orders")
                    row["Image Url"] = row.get("Image Url") or _g(d, "product_main_image_url")
                    changed = True
                    time.sleep(rate_limit_sec)
            new_rows.append(row)
            if changed:
                cnt += 1
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(new_rows)
        return cnt

if __name__ == "__main__":
    import argparse, sys, json, time, csv
    parser = argparse.ArgumentParser(description="AliExpress Affiliates helper (enrich CSV / fetch hot deals).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("enrich", help="Enrich existing CSV with affiliate links and missing fields")
    p1.add_argument("--in", dest="in_path", required=True)
    p1.add_argument("--out", dest="out_path", required=True)
    p1.add_argument("--rate", dest="rate", type=float, default=0.6, help="Rate-limit seconds between calls")
    p2 = sub.add_parser("hot", help="Fetch hot products into a CSV")
    p2.add_argument("--keyword", required=True)
    p2.add_argument("--out", dest="out_path", required=True)
    p2.add_argument("--count", type=int, default=10)
    p2.add_argument("--min_discount", type=int, default=30)
    p2.add_argument("--min_rating", type=float, default=4.6)
    args = parser.parse_args()

    try:
        client = AliExpressAffiliateClient()
    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "enrich":
        changed = client.enrich_csv(args.in_path, args.out_path, rate_limit_sec=args.rate)
        print(json.dumps({"ok": True, "changed_rows": changed}, ensure_ascii=False))
    elif args.cmd == "hot":
        remaining = args.count
        page = 1
        all_items: List[Dict[str, Any]] = []
        while remaining > 0 and page < 50:
            page_size = min(20, remaining)
            batch = client.query_products(
                keywords=args.keyword,
                page_no=page,
                page_size=page_size,
                min_discount=args.min_discount,
                min_rating=args.min_rating
            )
            if not batch:
                break
            all_items.extend(batch)
            remaining -= len(batch)
            page += 1
            time.sleep(0.4)
        headers = ["product_id","title","image","orig_price","sale_price","discount","rating","orders","detail_url"]
        with open(args.out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for it in all_items:
                w.writerow(it)
        print(json.dumps({"ok": True, "items": len(all_items)}, ensure_ascii=False))
