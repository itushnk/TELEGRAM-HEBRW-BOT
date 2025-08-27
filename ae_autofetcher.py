
"""
Auto-fetcher for AliExpress Affiliates: pulls products for given keywords
and appends them to the queue CSV. Designed to be imported by your bot's main.py.
"""
import os, time, csv, threading
from datetime import datetime
from urllib.parse import urlparse

IL_TZ_NAME = "Asia/Jerusalem"

def _now_il():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(tz=ZoneInfo(IL_TZ_NAME))
    except Exception:
        return datetime.now()

def _is_url(u: str) -> bool:
    try:
        r = urlparse((u or "").strip())
        return r.scheme in ("http", "https") and bool(r.netloc)
    except Exception:
        return False

def ensure_keywords_file(keywords_path: str):
    if not os.path.exists(keywords_path):
        with open(keywords_path, "w", encoding="utf-8") as f:
            f.write("# One keyword per line (comments start with #)\n")
            f.write("bluetooth earbuds\n")
            f.write("ssd 1tb\n")
            f.write("kids toys\n")
    return keywords_path

def read_keywords(keywords_path: str):
    keys = []
    try:
        with open(keywords_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                keys.append(s)
    except Exception:
        pass
    return keys

def _call_ae_search(AE, keyword: str, page_size: int = 5):
    """
    Best-effort search over common method names.
    Returns: list of dict-like product objects, or [] on failure.
    """
    if AE is None:
        return []
    candidates = [
        ("search_products", {"keyword": keyword, "page_size": page_size}),
        ("search", {"keyword": keyword, "page_size": page_size}),
        ("product_search", {"keyword": keyword, "page_size": page_size}),
        ("get_products", {"query": keyword, "limit": page_size}),
        ("fetch_products", {"query": keyword, "limit": page_size}),
    ]
    for name, params in candidates:
        try:
            fn = getattr(AE, name, None)
            if callable(fn):
                res = fn(**params)
                if isinstance(res, dict) and res.get("items"):
                    return res["items"]
                if isinstance(res, (list, tuple)):
                    return list(res)
        except Exception as e:
            print(f"[AUTO] AE.{name} failed for '{keyword}': {e}", flush=True)
            continue
    print(f"[AUTO] No suitable AE search method found for '{keyword}'", flush=True)
    return []

def _norm_item(obj):
    # Try to normalize common field names
    get = lambda *names: next((obj.get(n) for n in names if isinstance(obj, dict) and obj.get(n) is not None), None)
    item_id = get("item_id", "itemId", "product_id", "productId", "aliExpressItemId", "target_id")
    title   = get("title", "subject", "name")
    img     = get("image_url", "imageUrl", "img_url", "picture", "main_image", "image", "pic_url")
    video   = get("video_url", "videoUrl")
    link    = get("promotion_link", "promotionUrl", "url", "link", "target_url")

    if not link and item_id:
        # generic fallback
        link = f"https://www.aliexpress.com/item/{item_id}.html"

    return {
        "ItemId": str(item_id or "").strip(),
        "Title": (title or "").strip(),
        "Image Url": (img or "").strip(),
        "Video Url": (video or "").strip(),
        "BuyLink": (link or "").strip(),
        "Opening": "",
        "Strengths": "",
    }

def _read_queue(csv_path: str):
    rows = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows

def _write_queue(csv_path: str, rows):
    if not rows:
        # keep header even if empty, to be consistent
        header = ["ItemId","Title","Image Url","Video Url","BuyLink","Opening","Strengths"]
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(header)
        return
    header = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def _dedupe(existing, new_items):
    seen_ids = { (r.get("ItemId") or "").strip() for r in existing }
    seen_links = { (r.get("BuyLink") or "").strip() for r in existing }
    out = []
    for it in new_items:
        iid = (it.get("ItemId") or "").strip()
        ln  = (it.get("BuyLink") or "").strip()
        if (iid and iid in seen_ids) or (ln and ln in seen_links):
            continue
        out.append(it)
        if iid:
            seen_ids.add(iid)
        if ln:
            seen_links.add(ln)
    return out

def fetch_once(AE, pending_csv: str, keywords_path: str, max_per_keyword: int = 3):
    kws = read_keywords(keywords_path)
    if not kws:
        print(f"[{_now_il()}] [AUTO] No keywords – skipping cycle", flush=True)
        return 0

    added = 0
    existing = _read_queue(pending_csv)

    for kw in kws:
        raw = _call_ae_search(AE, kw, page_size=max_per_keyword)
        if not raw:
            print(f"[AUTO] No results for '{kw}'", flush=True)
            continue
        norm = [_norm_item(x) for x in raw]
        norm = [n for n in norm if n.get("BuyLink")]  # must have link
        to_add = _dedupe(existing, norm)[:max_per_keyword]
        if not to_add:
            continue
        existing.extend(to_add)
        added += len(to_add)

    if added:
        _write_queue(pending_csv, existing)
        print(f"[{_now_il()}] [AUTO] Added {added} items to queue", flush=True)
    else:
        print(f"[{_now_il()}] [AUTO] No new items to add", flush=True)
    return added

def start_auto_fetcher(AE, pending_csv: str, base_dir: str, flag_filename: str = "auto_fetch.enabled"):
    """
    Start a daemon thread that, if flag file exists AND AE is ready,
    fetches new items every AE_AUTO_FETCH_INTERVAL_MIN minutes.
    """
    FLAG = os.path.join(base_dir, flag_filename)
    KEYWORDS = os.path.join(base_dir, "keywords.txt")
    ensure_keywords_file(KEYWORDS)
    interval_min = int(os.getenv("AE_AUTO_FETCH_INTERVAL_MIN", "60"))
    max_per_kw = int(os.getenv("AE_AUTO_FETCH_MAX_PER_KEYWORD", "3"))

    def _loop():
        print(f"[AUTO] Fetcher thread started (interval={interval_min}m, max_per_kw={max_per_kw})", flush=True)
        while True:
            try:
                if not os.path.exists(FLAG):
                    time.sleep(10)
                    continue
                if AE is None:
                    print(f"[{_now_il()}] [AUTO] AE client not ready – skipping cycle", flush=True)
                else:
                    fetch_once(AE, pending_csv, KEYWORDS, max_per_kw)
            except Exception as e:
                print(f"[AUTO] cycle error: {e}", flush=True)
            time.sleep(max(30, interval_min*60))

    t = threading.Thread(target=_loop, name="AEAutoFetcher", daemon=True)
    t.start()
    return {"flag_path": FLAG, "keywords_path": KEYWORDS}
