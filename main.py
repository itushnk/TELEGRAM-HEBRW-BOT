# -*- coding: utf-8 -*-
import os, sys
os.environ.setdefault("PYTHONUNBUFFERED", "1")
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

import csv
import requests
import time
import telebot
from telebot import types
import threading
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import socket
import re

# ========= PERSISTENT DATA DIR =========
BASE_DIR = "."


# ========= CONFIG =========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")  # חובה ב-ENV
CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL", "@your_channel")  # יעד ציבורי ברירת מחדל
ADMIN_USER_IDS = set()  # מומלץ: {123456789}

# קבצים (בתיקיית DATA המתמשכת או לוקאלית)
DATA_CSV = "workfile.csv"        # קובץ המקור האחרון שהועלה
PENDING_CSV = os.path.join(BASE_DIR, "products_queue_managed.csv")  # תור הפוסטים

DELAY_FILE = os.path.join(BASE_DIR, "post_delay.txt")    # מרווח שידור
PUBLIC_PRESET_FILE  = os.path.join(BASE_DIR, "public_target.preset")
PRIVATE_PRESET_FILE = os.path.join(BASE_DIR, "private_target.preset")

# דגלים
SCHEDULE_FLAG_FILE = os.path.join(BASE_DIR, "schedule_enforced.flag")
CONVERT_NEXT_FLAG_FILE = os.path.join(BASE_DIR, "convert_next_usd_to_ils.flag")

# שער ברירת מחדל
USD_TO_ILS_RATE_DEFAULT = 3.55

# נעילה למופע יחיד
LOCK_PATH = os.environ.get("BOT_LOCK_PATH", os.path.join(BASE_DIR, "bot.lock"))

# ========= INIT =========
if not BOT_TOKEN:
    print("[WARN] BOT_TOKEN חסר – הבוט ירוץ אבל לא יוכל להתחבר לטלגרם עד שתקבע ENV.", flush=True)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
SESSION = requests.Session()

# === AliExpress Affiliate Client (inline) ===
class AliExpressAffiliateClient:
    """
    מינימום נדרש כדי שהבוט ירוץ. משתמש במפתחות מה-ENV.
    פעולות החיפוש כאן בסיסיות/דמה; בהמשך אפשר להחליף לקריאות API מלאות.
    """
    def __init__(self, app_key=None, app_secret=None, tracking_id=None):
        self.app_key = app_key or os.getenv("AE_APP_KEY")
        self.app_secret = app_secret or os.getenv("AE_APP_SECRET")
        self.tracking_id = tracking_id or os.getenv("AE_TRACKING_ID")
        self.lang = os.getenv("AE_TARGET_LANGUAGE", "HE")
        self.currency = os.getenv("AE_TARGET_CURRENCY", "ILS")
        self.ship_to = os.getenv("AE_SHIP_TO_COUNTRY", "IL")
        if not (self.app_key and self.app_secret):
            print("[WARN] AliExpress keys missing; set AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID", flush=True)

    def _ensure_ready(self):
        if not (self.app_key and self.app_secret and self.tracking_id):
            raise RuntimeError("Missing AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID")

    def search_products(self, keyword: str, page_size: int = 5):
        """
        TODO: לממש קריאה אמיתית ל-Affiliates API.
        כרגע: אם יש מפתחות – מחזיר רשימה ריקה (כדי לא לשבור זרימה).
        """
        self._ensure_ready()
        return []

    def generate_promotion_link(self, item_id: str):
        self._ensure_ready()
        return {"promotion_url": f"https://www.aliexpress.com/item/{item_id}.html"}

# === Affiliates Inline Panel (init) ===
try:
    AE = AliExpressAffiliateClient()  # Uses ENV: AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID
except Exception as e:
    AE = None
    print(f"[WARN] AliExpress client not initialized: {e}", flush=True)

def _require_ae(_msg_or_chat_id):
    try:
        _chat_id = _msg_or_chat_id.chat.id if hasattr(_msg_or_chat_id, "chat") else _msg_or_chat_id
    except Exception:
        _chat_id = _msg_or_chat_id if isinstance(_msg_or_chat_id, int) else None
    if AE is None:
        try:
            bot.send_message(_chat_id, "❌ AliExpress API לא מאותחל. ודא ENV: AE_APP_KEY, AE_APP_SECRET, AE_TRACKING_ID")
        except Exception:
            pass
        return False
    return True
SESSION.headers.update({"User-Agent": "TelegramPostBot/1.0"})

# === Auto-Fetcher (inline) ===
from urllib.parse import urlparse as _urlparse
def _now_il():
    try:
        return datetime.now(tz=IL_TZ)
    except Exception:
        return datetime.now()

def _is_url(u: str) -> bool:
    try:
        r = _urlparse((u or "").strip())
        return r.scheme in ("http", "https") and bool(r.netloc)
    except Exception:
        return False

def af_ensure_keywords_file(keywords_path: str):
    if not os.path.exists(keywords_path):
        with open(keywords_path, "w", encoding="utf-8") as f:
            f.write("# One keyword per line (comments start with #)\\n")
            f.write("bluetooth earbuds\\n")
            f.write("ssd 1tb\\n")
            f.write("kids toys\\n")
    return keywords_path

def af_read_keywords(keywords_path: str):
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

def af_call_search(AE, keyword: str, page_size: int = 5):
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

def af_norm_item(obj):
    get = lambda *names: next((obj.get(n) for n in names if isinstance(obj, dict) and obj.get(n) is not None), None)
    item_id = get("item_id", "itemId", "product_id", "productId", "aliExpressItemId", "target_id")
    title   = get("title", "subject", "name")
    img     = get("image_url", "imageUrl", "img_url", "picture", "main_image", "image", "pic_url")
    video   = get("video_url", "videoUrl")
    link    = get("promotion_link", "promotionUrl", "url", "link", "target_url")
    if not link and item_id:
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

def af_read_queue(csv_path: str):
    rows = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows

def af_write_queue(csv_path: str, rows):
    header = ["ItemId","Title","Image Url","Video Url","BuyLink","Opening","Strengths"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})

def af_dedupe(existing, new_items):
    seen_ids = { (r.get("ItemId") or "").strip() for r in existing }
    seen_links = { (r.get("BuyLink") or "").strip() for r in existing }
    out = []
    for it in new_items:
        iid = (it.get("ItemId") or "").strip()
        ln  = (it.get("BuyLink") or "").strip()
        if (iid and iid in seen_ids) or (ln and ln in seen_links):
            continue
        out.append(it)
        if iid: seen_ids.add(iid)
        if ln:  seen_links.add(ln)
    return out

def af_fetch_once(AE, pending_csv: str, keywords_path: str, max_per_keyword: int = 3):
    kws = af_read_keywords(keywords_path)
    if not kws:
        print(f"[{_now_il()}] [AUTO] No keywords – skipping cycle", flush=True)
        return 0

    existing = af_read_queue(pending_csv)
    added = 0
    for kw in kws:
        raw = af_call_search(AE, kw, page_size=max_per_keyword)
        if not raw:
            print(f"[AUTO] No results for '{kw}'", flush=True)
            continue
        norm = [af_norm_item(x) for x in raw if isinstance(x, dict)]
        norm = [n for n in norm if n.get("BuyLink")]
        to_add = af_dedupe(existing, norm)[:max_per_keyword]
        if not to_add:
            continue
        existing.extend(to_add)
        added += len(to_add)

    if added:
        af_write_queue(pending_csv, existing)
        print(f"[{_now_il()}] [AUTO] Added {added} items to queue", flush=True)
    else:
        print(f"[{_now_il()}] [AUTO] No new items to add", flush=True)
    return added

def af_start(AE, pending_csv: str, base_dir: str, flag_filename: str = "auto_fetch.enabled"):
    FLAG = os.path.join(base_dir, flag_filename)
    KEYWORDS = os.path.join(base_dir, "keywords.txt")
    af_ensure_keywords_file(KEYWORDS)
    interval_min = int(os.getenv("AE_AUTO_FETCH_INTERVAL_MIN", "60"))
    max_per_kw = int(os.getenv("AE_AUTO_FETCH_MAX_PER_KEYWORD", "3"))

    def _loop():
        print(f"[AUTO] Fetcher thread started (interval={interval_min}m, max_per_kw={max_per_kw})", flush=True)
        while True:
            try:
                if not os.path.exists(FLAG):
                    time.sleep(10)
                else:
                    if AE is None:
                        print(f"[{_now_il()}] [AUTO] AE client not ready – skipping", flush=True)
                    else:
                        af_fetch_once(AE, pending_csv, KEYWORDS, max_per_kw)
                    time.sleep(max(30, interval_min*60))
            except Exception as e:
                print(f"[AUTO] cycle error: {e}", flush=True)
                time.sleep(60)

    t = threading.Thread(target=_loop, name="AEAutoFetcher", daemon=True)
    t.start()
    return {"flag_path": FLAG, "keywords_path": KEYWORDS}

# expose a module-like namespace to reuse existing command handlers
class _AFNS:
    ensure_keywords_file = staticmethod(af_ensure_keywords_file)
    read_keywords = staticmethod(af_read_keywords)
    start_auto_fetcher = staticmethod(af_start)
    fetch_once = staticmethod(af_fetch_once)

ae_autofetcher = _AFNS()

IL_TZ = ZoneInfo("Asia/Jerusalem")

def translate_missing_fields(csv_path):
    import pandas as pd
    import openai
    api_key = getattr(openai, "api_key", None) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ לא נמצא מפתח OpenAI – דילוג על תרגום.", flush=True)
        return
    df = pd.read_csv(csv_path)
    changed = False

    for i, row in df.iterrows():
        desc = row.get("Product Desc", "")
        opening = str(row.get("Opening", "")).strip()
        title = str(row.get("Title", "")).strip()
        strengths = str(row.get("Strengths", "")).strip()

        if desc and (not opening or not title or not strengths):
            prompt = f"""
            תרגם את התיאור הבא לפוסט שיווקי בעברית עבור טלגרם:
            ---
            {desc}
            ---
            כתוב פתיח שיווקי קצר לעמודת Opening.
            כתוב תיאור מוצר מקוצר לעמודת Title.
            כתוב שלוש נקודות חוזקה בעמודת Strengths (עם אימוג'ים).

            ענה רק בפורמט הבא (הפרד בשורת רווח בין כל חלק):
            Opening: ...
            Title: ...
            Strengths: ...
            """

            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7
                )
                reply = response.choices[0].message.content.strip()

                # חילוץ הערכים
                for line in reply.splitlines():
                    if line.startswith("Opening:"):
                        df.at[i, "Opening"] = line.replace("Opening:", "").strip()
                        changed = True
                    elif line.startswith("Title:"):
                        df.at[i, "Title"] = line.replace("Title:", "").strip()
                        changed = True
                    elif line.startswith("Strengths:"):
                        df.at[i, "Strengths"] = line.replace("Strengths:", "").strip()
                        changed = True

            except Exception as e:
                print(f"שגיאה בתרגום שורה {i}: {e}")

    if changed:
        df.to_csv(csv_path, index=False)
        print("💾 שורות מתורגמות נשמרו.")
    else:
        print("✅ אין שורות שדורשות תרגום.")

csv_files = [f for f in os.listdir(BASE_DIR) if f.endswith('.csv')]
if csv_files:
    current_csv = os.path.join(BASE_DIR, csv_files[0])
    translate_missing_fields(current_csv)

# יעד נוכחי
CURRENT_TARGET = CHANNEL_ID

# “התעוררות חמה” ללולאת השידור
DELAY_EVENT = threading.Event()

# מצב בחירת יעד (באמצעות Forward)
EXPECTING_TARGET = {}  # dict[user_id] = "public"|"private"

# מצב העלאת CSV
EXPECTING_UPLOAD = set()  # user_ids שמצפים ל-CSV

# נעילה לפעולות על התור כדי למנוע כפילות בין הלולאה לכפתור ידני
FILE_LOCK = threading.Lock()


# ========= SINGLE INSTANCE LOCK =========

def acquire_single_instance_lock(lock_path: str):
    try:
        if os.name == "nt":
            import msvcrt
            f = open(lock_path, "w")
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                print("Another instance is running. Exiting.", flush=True)
                sys.exit(1)
            return f
        else:
            import fcntl
            f = open(lock_path, "w")
            try:
                fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                print("Another instance is running. Exiting.", flush=True)
                sys.exit(1)
            return f
    except Exception as e:
        print(f"[WARN] Could not acquire single-instance lock: {e}", flush=True)
        return None
# ========= WEBHOOK DIAGNOSTICS =========
def print_webhook_info():
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
        r = requests.get(url, timeout=10)
        print("getWebhookInfo:", r.json(), flush=True)
    except Exception as e:
        print(f"[WARN] getWebhookInfo failed: {e}", flush=True)

def force_delete_webhook():
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
        r = requests.get(url, params={"drop_pending_updates": True}, timeout=10)
        print("deleteWebhook:", r.json(), flush=True)
    except Exception as e:
        print(f"[WARN] deleteWebhook failed: {e}", flush=True)


# ========= HELPERS =========
def safe_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default

def norm_percent(value, decimals=1, empty_fallback=""):
    s = str(value).strip() if value is not None else ""
    if not s:
        return empty_fallback
    s = s.replace("%", "")
    try:
        f = float(s)
        return f"{round(f, decimals)}%"
    except Exception:
        return empty_fallback

def clean_price_text(s):
    if s is None:
        return ""
    s = str(s)
    for junk in ["ILS", "₪"]:
        s = s.replace(junk, "")
    out = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    return out.strip()

def normalize_row_keys(row):
    out = dict(row)
    if "ImageURL" not in out:
        out["ImageURL"] = out.get("Image Url", "") or out.get("ImageURL", "")
    if "Video Url" not in out:
        out["Video Url"] = out.get("Video Url", "")
    if "BuyLink" not in out:
        out["BuyLink"] = out.get("Promotion Url", "") or out.get("BuyLink", "")
    out["OriginalPrice"] = clean_price_text(out.get("OriginalPrice", "") or out.get("Origin Price", ""))
    out["SalePrice"]     = clean_price_text(out.get("SalePrice", "") or out.get("Discount Price", ""))
    disc = f"{out.get('Discount', '')}".strip()
    if disc and not disc.endswith("%"):
        try:
            disc = f"{int(round(float(disc)))}%"
        except Exception:
            pass
    out["Discount"] = disc
    out["Rating"] = norm_percent(out.get("Rating", "") or out.get("Positive Feedback", ""), decimals=1, empty_fallback="")
    if not str(out.get("Orders", "")).strip():
        out["Orders"] = str(out.get("Sales180Day", "")).strip()
    if "CouponCode" not in out:
        out["CouponCode"] = out.get("Code Name", "") or out.get("CouponCode", "")
    if "ItemId" not in out:
        out["ItemId"] = out.get("ProductId", "") or out.get("ItemId", "") or "ללא מספר"
    if "Opening" not in out:
        out["Opening"] = out.get("Opening", "") or ""
    if "Title" not in out:
        out["Title"] = out.get("Title", "") or out.get("Product Desc", "") or ""
    out["Strengths"] = out.get("Strengths", "")
    return out

def read_products(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [normalize_row_keys(r) for r in reader]
        return rows

def write_products(path, rows):
    base_headers = [
        "ItemId","ImageURL","Title","OriginalPrice","SalePrice","Discount",
        "Rating","Orders","BuyLink","CouponCode","Opening","Video Url","Strengths"
    ]
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=base_headers)
            w.writeheader()
        return
    headers = list(dict.fromkeys(base_headers + [k for r in rows for k in r.keys()]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def init_pending():
    if not os.path.exists(PENDING_CSV):
        src = read_products(DATA_CSV)
        write_products(PENDING_CSV, src)

# ---- PRESET HELPERS ----
def _save_preset(path: str, value):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(value))
    except Exception as e:
        print(f"[WARN] Failed to save preset {path}: {e}", flush=True)

def _load_preset(path: str):
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[WARN] Failed to load preset {path}: {e}", flush=True)
        return None

def resolve_target(value):
    try:
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if s.startswith("-"):
            return int(s)
        return s
    except Exception:
        return value

def check_and_probe_target(target):
    try:
        t = resolve_target(target)
        chat = bot.get_chat(t)
        try:
            me = bot.get_me()
            member = bot.get_chat_member(chat.id, me.id)
            status = getattr(member, "status", "")
            if status not in ("administrator", "creator"):
                return False, f"⚠️ הבוט אינו אדמין ביעד {chat.id}."
        except Exception as e_mem:
            print("[WARN] get_chat_member failed:", e_mem, flush=True)
        try:
            m = bot.send_message(chat.id, "🟢 בדיקת הרשאה (תימחק מיד).", disable_notification=True)
            try:
                bot.delete_message(chat.id, m.message_id)
            except Exception:
                pass
            return True, f"✅ יעד תקין: {chat.title or chat.id}"
        except Exception as e_send:
            return False, f"❌ לא הצלחתי לפרסם ביעד: {e_send}"
    except Exception as e:
        return False, f"❌ יעד לא תקין: {e}"


# ========= BROADCAST WINDOW =========
def should_broadcast(now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(tz=IL_TZ)
    else:
        now = now.astimezone(IL_TZ)
    wd = now.weekday()  # Mon=0 ... Sun=6 (אצלנו: ראשון=6)
    t = now.time()
    if wd in (6, 0, 1, 2, 3):
        return dtime(6, 0) <= t <= dtime(23, 59)
    if wd == 4:
        return dtime(6, 0) <= t <= dtime(17, 59)
    if wd == 5:
        return dtime(20, 15) <= t <= dtime(23, 59)
    return False

def is_schedule_enforced() -> bool:
    return os.path.exists(SCHEDULE_FLAG_FILE)

def set_schedule_enforced(enabled: bool) -> None:
    try:
        if enabled:
            with open(SCHEDULE_FLAG_FILE, "w", encoding="utf-8") as f:
                f.write("schedule=on")
        else:
            if os.path.exists(SCHEDULE_FLAG_FILE):
                os.remove(SCHEDULE_FLAG_FILE)
    except Exception as e:
        print(f"[WARN] Failed to set schedule mode: {e}", flush=True)

def is_quiet_now(now: datetime | None = None) -> bool:
    return not should_broadcast(now) if is_schedule_enforced() else False


# ========= SAFE EDIT =========
def safe_edit_message(bot, *, chat_id: int, message, new_text: str, reply_markup=None, parse_mode=None, cb_id=None, cb_info=None):
    try:
        curr_text = (message.text or message.caption or "")
        if curr_text == (new_text or ""):
            try:
                if reply_markup is not None:
                    bot.edit_message_reply_markup(chat_id, message.message_id, reply_markup=reply_markup)
                    if cb_id:
                        bot.answer_callback_query(cb_id)
                    return
                if cb_id:
                    bot.answer_callback_query(cb_id)
                return
            except Exception as e_rm:
                if "message is not modified" in str(e_rm):
                    if cb_id:
                        bot.answer_callback_query(cb_id)
                    return
        bot.edit_message_text(new_text, chat_id, message.message_id, reply_markup=reply_markup, parse_mode=parse_mode)
        if cb_id:
            bot.answer_callback_query(cb_id)
    except Exception as e:
        if "message is not modified" in str(e):
            if cb_id:
                bot.answer_callback_query(cb_id)
            return
        if cb_id and cb_info:
            bot.answer_callback_query(cb_id, cb_info + f" (שגיאה: {e})", show_alert=True)
        else:
            raise


# ========= POSTING =========
def format_post(product):
    item_id = product.get('ItemId', 'ללא מספר')
    image_url = product.get('ImageURL', '')
    title = product.get('Title', '')
    original_price = product.get('OriginalPrice', '')
    sale_price = product.get('SalePrice', '')
    discount = product.get('Discount', '')
    rating = product.get('Rating', '')
    orders = product.get('Orders', '')
    buy_link = product.get('BuyLink', '')
    coupon = product.get('CouponCode', '')

    opening = (product.get('Opening') or '').strip()
    strengths_src = (product.get("Strengths") or "").strip()

    rating_percent = rating if rating else "אין דירוג"
    orders_num = safe_int(orders, default=0)
    orders_text = f"{orders_num} הזמנות" if orders_num >= 50 else "פריט חדש לחברי הערוץ"
    discount_text = f"💸 חיסכון של {discount}!" if discount and discount != "0%" else ""
    coupon_text = f"🎁 קופון לחברי הערוץ בלבד: {coupon}" if str(coupon).strip() else ""

    lines = []
    if opening:
        lines.append(opening)
        lines.append("")
    if title:
        lines.append(title)
        lines.append("")

    if strengths_src:
        for part in [p.strip() for p in strengths_src.replace("|", "\n").replace(";", "\n").split("\n")]:
            if part:
                lines.append(part)
        lines.append("")

    price_line = f'💰 מחיר מבצע: <a href="{buy_link}">{sale_price} ש"ח</a> (מחיר מקורי: {original_price} ש"ח)'
    lines += [
        price_line,
        discount_text,
        f"⭐ דירוג: {rating_percent}",
        f"📦 {orders_text}",
        "🚚 משלוח חינם מעל 38 ש\"ח או 7.49 ש\"ח",
        "",
        coupon_text if coupon_text else "",
        "",
        f'להזמנה מהירה👈 <a href="{buy_link}">לחצו כאן</a>',
        "",
        f"מספר פריט: {item_id}",
        'להצטרפות לערוץ לחצו כאן👈 <a href="https://t.me/+LlMY8B9soOdhNmZk">קליק והצטרפתם</a>',
        "",
        "👇🛍הזמינו עכשיו🛍👇",
        f'<a href="{buy_link}">לחיצה וזה בדרך </a>',
    ]

    post = "\n".join([l for l in lines if l is not None and str(l).strip() != ""])
    return post, image_url

def post_to_channel(product):
    try:
        post_text, image_url = format_post(product)
        video_url = (product.get('Video Url') or "").strip()
        target = resolve_target(CURRENT_TARGET)
        if video_url.endswith('.mp4') and video_url.startswith("http"):
            resp = SESSION.get(video_url, timeout=20)
            resp.raise_for_status()
            bot.send_video(target, resp.content, caption=post_text)
        else:
            resp = SESSION.get(image_url, timeout=20)
            resp.raise_for_status()
            bot.send_photo(target, resp.content, caption=post_text)
    except Exception as e:
        print(f"[{datetime.now(tz=IL_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}] Failed to post: {e}", flush=True)


# ========= ATOMIC SEND =========

def send_next_locked(mode="manual"):
    """
    שולח את הפריט הראשון בתור. מתקדם בתור **רק** אם השליחה הצליחה.
    מחזיר True בהצלחה, False בכישלון.
    """
    try:
        with FILE_LOCK:
            rows = read_products(PENDING_CSV)
            if not rows:
                print(f"[{datetime.now(tz=IL_TZ)}] {mode}: queue empty", flush=True)
                return False
            product = rows[0]

        item_id = (product.get("ItemId") or "ללא מספר")
        title = (product.get("Title") or "").strip()[:120]
        print(f"[{datetime.now(tz=IL_TZ)}] {mode}: sending ItemId={item_id} | Title={title}", flush=True)

        # מנסה לפרסם – אם נכשל, תיזרק חריגה ולא נתקדם בתור
        post_to_channel(product)

        with FILE_LOCK:
            rows = read_products(PENDING_CSV)  # קריאה מחדש להגנה מקונקרנציה
            if rows:
                rows.pop(0)
                write_products(PENDING_CSV, rows)

        print(f"[{datetime.now(tz=IL_TZ)}] {mode}: sent & advanced queue", flush=True)
        return True

    except Exception as e:
        print(f"[{datetime.now(tz=IL_TZ)}] {mode}: send FAILED (not advancing): {e}", flush=True)
        return False

def write_auto_flag(value):
    with open(AUTO_FLAG_FILE, "w", encoding="utf-8") as f:
        f.write(value)

def get_auto_delay():
    now = datetime.now(IL_TZ).time()
    for start, end, delay in AUTO_SCHEDULE:
        if start <= now <= end:
            return delay
    return None

def load_delay_seconds(default_seconds: int = 1500) -> int:
    try:
        if os.path.exists(DELAY_FILE):
            with open(DELAY_FILE, "r", encoding="utf-8") as f:
                val = int(f.read().strip())
                if val > 0:
                    return val
    except Exception:
        pass
    return default_seconds

def save_delay_seconds(seconds: int) -> None:
    try:
        with open(DELAY_FILE, "w", encoding="utf-8") as f:
            f.write(str(seconds))
    except Exception as e:
        print(f"[WARN] Failed to save delay: {e}", flush=True)

POST_DELAY_SECONDS = load_delay_seconds(1500)  # 25 דקות


# ========= ADMIN =========
def _is_admin(msg) -> bool:
    if not ADMIN_USER_IDS:
        return True
    return msg.from_user and (msg.from_user.id in ADMIN_USER_IDS)


# ========= MERGE =========
def merge_from_data_into_pending():
    data_rows = read_products(DATA_CSV)
    pending_rows = read_products(PENDING_CSV)

    def key_of(r):
        item_id = (r.get("ItemId") or "").strip()
        title = (r.get("Title") or "").strip()
        buy = (r.get("BuyLink") or "").strip()
        return (item_id if item_id else None, title if not item_id else None, buy)

    existing_keys = {key_of(r) for r in pending_rows}
    added = 0
    already = 0

    for r in data_rows:
        k = key_of(r)
        if k in existing_keys:
            already += 1
            continue
        pending_rows.append(r)
        existing_keys.add(k)
        added += 1

    write_products(PENDING_CSV, pending_rows)
    return added, already, len(pending_rows)


# ========= DELETE HELPERS =========
def _key_of_row(r: dict):
    item_id = (r.get("ItemId") or "").strip()
    title   = (r.get("Title") or "").strip()
    buy     = (r.get("BuyLink") or "").strip()
    return (item_id if item_id else None, title if not item_id else None, buy)

def delete_source_csv_file():
    """
    מוחק את workfile.csv (משאיר קובץ ריק עם כותרות) — לא נוגע בתור.
    """
    with FILE_LOCK:
        write_products(DATA_CSV, [])
    return True

def delete_source_rows_from_pending():
    """
    קורא את workfile.csv ומסיר מהתור (pending.csv) את כל הרשומות שנוספו ממנו,
    לפי אותו מפתח מניעת-כפילויות (ItemId/Title/BuyLink).
    """
    with FILE_LOCK:
        src_rows = read_products(DATA_CSV)
        if not src_rows:
            return 0, 0

        src_keys = {_key_of_row(r) for r in src_rows}
        pending_rows = read_products(PENDING_CSV)
        if not pending_rows:
            write_products(PENDING_CSV, [])
            return 0, 0

        before = len(pending_rows)
        filtered = [r for r in pending_rows if _key_of_row(r) not in src_keys]
        removed = before - len(filtered)
        write_products(PENDING_CSV, filtered)
        return removed, len(filtered)


# ========= USD→ILS HELPERS =========
def _decode_csv_bytes(b: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1255", "iso-8859-8"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="ignore")

def _is_usd_price(raw_value: str) -> bool:
    s = (raw_value or "")
    if not isinstance(s, str):
        s = str(s)
    s_low = s.lower()
    return ("$" in s) or ("usd" in s_low)

def _extract_number(s: str) -> float | None:
    if s is None:
        return None
    s = str(s)
    m = re.search(r"([-+]?\d+(?:[.,]\d+)?)", s)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))

def _convert_price_text(raw_value: str, rate: float) -> str:
    num = _extract_number(raw_value)
    if num is None:
        return ""
    ils = round(num * rate)
    return str(int(ils))

def _rows_with_optional_usd_to_ils(rows_raw: list[dict], rate: float | None):
    out = []
    for r in rows_raw:
        rr = dict(r)
        if rate:
            orig_src = rr.get("OriginalPrice", rr.get("Origin Price", ""))
            sale_src = rr.get("SalePrice", rr.get("Discount Price", ""))

            if _is_usd_price(str(orig_src)):
                rr["OriginalPrice"] = _convert_price_text(orig_src, rate)
            if _is_usd_price(str(sale_src)):
                rr["SalePrice"] = _convert_price_text(sale_src, rate)
        out.append(normalize_row_keys(rr))
    return out


# ========= INLINE MENU =========
def inline_menu():
    kb = types.InlineKeyboardMarkup(row_width=3)

    # פעולות
    
    kb.add(
        types.InlineKeyboardButton("📢 פרסם עכשיו", callback_data="publish_now"),
        types.InlineKeyboardButton("⏱️ כל 20ד", callback_data="delay_1200"),
        types.InlineKeyboardButton("⏱️ כל 25ד", callback_data="delay_1500"),
        types.InlineKeyboardButton("⏱️ כל 30ד", callback_data="delay_1800"),
    )
    kb.add(types.InlineKeyboardButton("⚙️ מצב אוטומטי (החלפה)", callback_data="toggle_auto_mode"))
    kb.add(
        types.InlineKeyboardButton("📊 סטטוס שידור", callback_data="pending_status"),
        types.InlineKeyboardButton("🔄 טען/מזג מהקובץ", callback_data="reload_merge"),
        types.InlineKeyboardButton("🕒 מצב שינה (החלפה)", callback_data="toggle_schedule"),
    )

    # מרווחים
    kb.add(
        types.InlineKeyboardButton("⏱️ דקה", callback_data="delay_60"),
        types.InlineKeyboardButton("⏱️ 15ד", callback_data="delay_900"),
        types.InlineKeyboardButton("⏱️ 20ד", callback_data="delay_1200"),
        types.InlineKeyboardButton("⏱️ 25ד", callback_data="delay_1500"),
        types.InlineKeyboardButton("⏱️ 30ד", callback_data="delay_1800"),
    )

    # העלאת CSV
    kb.add(types.InlineKeyboardButton("📥 העלה CSV", callback_data="upload_source"))

    # המרת $→₪ לקובץ הבא בלבד
    kb.add(types.InlineKeyboardButton("₪ המרת $→₪ (3.55) לקובץ הבא", callback_data="convert_next"))

    # איפוס יזום מהקובץ הראשי
    kb.add(types.InlineKeyboardButton("🔁 חזור להתחלה מהקובץ", callback_data="reset_from_data"))

    
    kb.add(types.InlineKeyboardButton("⚙️ מצב אוטומטי (החלפה)", callback_data="toggle_auto_mode"))

    # מחיקות
    kb.add(
        types.InlineKeyboardButton("🗑️ מחק פריטי התור מהקובץ", callback_data="delete_source_from_pending"),
        types.InlineKeyboardButton("🧹 מחק את workfile.csv", callback_data="delete_source_file"),
    )

    # יעדים (שמורים)
    kb.add(
        types.InlineKeyboardButton("🎯 ציבורי (השתמש)", callback_data="target_public"),
        types.InlineKeyboardButton("🔒 פרטי (השתמש)", callback_data="target_private"),
    )
    # בחירה דרך Forward
    kb.add(
        types.InlineKeyboardButton("🆕 בחר ערוץ ציבורי", callback_data="choose_public"),
        types.InlineKeyboardButton("🆕 בחר ערוץ פרטי", callback_data="choose_private"),
    )
    # ביטול בחירה
    kb.add(types.InlineKeyboardButton("❌ בטל בחירת יעד", callback_data="choose_cancel"))

    kb.add(types.InlineKeyboardButton(
        f"מרווח: ~{POST_DELAY_SECONDS//60} דק׳ | יעד: {CURRENT_TARGET}", callback_data="noop_info"
    ))
    return kb


# ========= INLINE CALLBACKS =========
@bot.callback_query_handler(func=lambda c: True)
def on_inline_click(c):
    global POST_DELAY_SECONDS, CURRENT_TARGET
    if not _is_admin(c.message):
        bot.answer_callback_query(c.id, "אין הרשאה.", show_alert=True)
        return

    data = c.data or ""
    chat_id = c.message.chat.id

    if data == "publish_now":
        ok = send_next_locked("manual")
        if not ok:
            bot.answer_callback_query(c.id, "אין פוסטים ממתינים או שגיאה בשליחה.", show_alert=True)
            return
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="✅ נשלח הפריט הבא בתור.", reply_markup=inline_menu(), cb_id=c.id)

    elif data == "skip_one":
        with FILE_LOCK:
            pending = read_products(PENDING_CSV)
            if not pending:
                bot.answer_callback_query(c.id, "אין מה לדלג – התור ריק.", show_alert=True)
                return
            write_products(PENDING_CSV, pending[1:])
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="⏭ דילגתי על הפריט הבא בתור.", reply_markup=inline_menu(), cb_id=c.id)

    elif data == "list_pending":
        with FILE_LOCK:
            pending = read_products(PENDING_CSV)
        if not pending:
            bot.answer_callback_query(c.id, "אין פוסטים ממתינים ✅", show_alert=True)
            return
        preview = pending[:10]
        lines = []
        for i, p in enumerate(preview, start=1):
            title = str(p.get('Title',''))[:80]
            sale = p.get('SalePrice','')
            disc = p.get('Discount','')
            rating = p.get('Rating','')
            lines.append(f"{i}. {title}\n   מחיר מבצע: {sale} | הנחה: {disc} | דירוג: {rating}")
        more = len(pending) - len(preview)
        if more > 0:
            lines.append(f"...ועוד {more} בהמתנה")
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="📝 פוסטים ממתינים:\n\n" + "\n".join(lines),
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "pending_status":
        with FILE_LOCK:
            pending = read_products(PENDING_CSV)
        count = len(pending)
        now_il = datetime.now(tz=IL_TZ)
        schedule_line = "🕰️ מצב: מתוזמן (שינה פעיל)" if is_schedule_enforced() else "🟢 מצב: תמיד-פעיל"
        delay_line = f"⏳ מרווח נוכחי: {POST_DELAY_SECONDS//60} דק׳ ({POST_DELAY_SECONDS} שניות)"
        target_line = f"🎯 יעד נוכחי: {CURRENT_TARGET}"
        if count == 0:
            text = f"{schedule_line}\n{delay_line}\n{target_line}\nאין פוסטים ממתינים ✅"
        else:
            total_seconds = (count - 1) * POST_DELAY_SECONDS
            eta = now_il + timedelta(seconds=total_seconds)
            eta_str = eta.strftime("%Y-%m-%d %H:%M:%S %Z")
            next_eta = now_il.strftime("%Y-%m-%d %H:%M:%S %Z")
            status_line = "🎙️ שידור אפשרי עכשיו" if not is_quiet_now(now_il) else "⏸️ כרגע מחוץ לחלון השידור"
            text = (
                f"{schedule_line}\n"
                f"{status_line}\n"
                f"{delay_line}\n"
                f"{target_line}\n"
                f"יש כרגע <b>{count}</b> פוסטים ממתינים.\n"
                f"⏱️ השידור הבא (תיאוריה לפי מרווח): <b>{next_eta}</b>\n"
                f"🕒 שעת השידור המשוערת של האחרון: <b>{eta_str}</b>\n"
                f"(מרווח בין פוסטים: {POST_DELAY_SECONDS} שניות)"
            )
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=text, reply_markup=inline_menu(), parse_mode='HTML', cb_id=c.id)

    elif data == "reload_merge":
        added, already, total_after = merge_from_data_into_pending()
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"🔄 מיזוג הושלם.\nנוספו: {added}\nבעבר בתור: {already}\nסה\"כ בתור כעת: {total_after}",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "upload_source":
        EXPECTING_UPLOAD.add(getattr(c.from_user, "id", None))
        safe_edit_message(
            bot, chat_id=chat_id, message=c.message,
            new_text="שלח/י עכשיו קובץ CSV (כמסמך). הבוט ימפה עמודות, יעדכן workfile.csv וימזג אל התור.",
            reply_markup=inline_menu(), cb_id=c.id
        )

    elif data == "toggle_schedule":
        set_schedule_enforced(not is_schedule_enforced())
        state = "🕰️ מתוזמן (שינה פעיל)" if is_schedule_enforced() else "🟢 תמיד-פעיל"
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"החלפתי מצב לשידור: {state}",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data.startswith("delay_"):
        try:
            seconds = int(data.split("_", 1)[1])
            if seconds <= 0:
                raise ValueError("מרווח חייב להיות חיובי")
            POST_DELAY_SECONDS = seconds
            save_delay_seconds(seconds)
            DELAY_EVENT.set()
            mins = seconds // 60
            safe_edit_message(bot, chat_id=chat_id, message=c.message,
                              new_text=f"⏱️ עודכן מרווח: ~{mins} דק׳ ({seconds} שניות).",
                              reply_markup=inline_menu(), cb_id=c.id)
        except Exception as e:
            bot.answer_callback_query(c.id, f"שגיאה בעדכון מרווח: {e}", show_alert=True)

    elif data == "target_public":
        v = _load_preset(PUBLIC_PRESET_FILE)
        if v is None:
            bot.answer_callback_query(c.id, "לא הוגדר יעד ציבורי. בחר דרך '🆕 בחר ערוץ ציבורי'.", show_alert=True)
            return
        CURRENT_TARGET = resolve_target(v)
        ok, details = check_and_probe_target(CURRENT_TARGET)
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"🎯 עברתי לשדר ליעד הציבורי: {v}\n{details}",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "target_private":
        v = _load_preset(PRIVATE_PRESET_FILE)
        if v is None:
            bot.answer_callback_query(c.id, "לא הוגדר יעד פרטי. בחר דרך '🆕 בחר ערוץ פרטי'.", show_alert=True)
            return
        CURRENT_TARGET = resolve_target(v)
        ok, details = check_and_probe_target(CURRENT_TARGET)
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"🔒 עברתי לשדר ליעד הפרטי: {v}\n{details}",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "choose_public":
        EXPECTING_TARGET[c.from_user.id] = "public"
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=("שלח/י *Forward* של הודעה מאותו ערוץ **ציבורי** כדי לשמור אותו כיעד.\n\n"
                                    "טיפ: פוסט בערוץ → ••• → Forward → בחר/י את הבוט."),
                          reply_markup=inline_menu(), parse_mode='Markdown', cb_id=c.id)

    elif data == "choose_private":
        EXPECTING_TARGET[c.from_user.id] = "private"
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=("שלח/י *Forward* של הודעה מאותו ערוץ **פרטי** כדי לשמור אותו כיעד.\n\n"
                                    "חשוב: הוסף/י את הבוט כמנהל בערוץ הפרטי."),
                          reply_markup=inline_menu(), parse_mode='Markdown', cb_id=c.id)

    elif data == "choose_cancel":
        EXPECTING_TARGET.pop(getattr(c.from_user, "id", None), None)
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="ביטלתי את מצב בחירת היעד. אפשר להמשיך כרגיל.",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "convert_next":
        try:
            with open(CONVERT_NEXT_FLAG_FILE, "w", encoding="utf-8") as f:
                f.write(str(USD_TO_ILS_RATE_DEFAULT))
            safe_edit_message(
                bot, chat_id=chat_id, message=c.message,
                new_text=f"✅ הופעל: המרת מחירים מדולר לש\"ח בקובץ ה-CSV הבא בלבד (שער {USD_TO_ILS_RATE_DEFAULT}).",
                reply_markup=inline_menu(), cb_id=c.id
            )
        except Exception as e:
            bot.answer_callback_query(c.id, f"שגיאה בהפעלת המרה: {e}", show_alert=True)

    elif data == "reset_from_data":
        src = read_products(DATA_CSV)
        with FILE_LOCK:
            write_products(PENDING_CSV, src)
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"🔁 התור אופס ומתחיל מחדש ({len(src)} פריטים) מהקובץ הראשי.",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "delete_source_from_pending":
        removed, left = delete_source_rows_from_pending()
        safe_edit_message(
            bot, chat_id=chat_id, message=c.message,
            new_text=f"🗑️ הוסר מהתור: {removed} פריטים שנמצאו ב-workfile.csv\nנשארו בתור: {left}",
            reply_markup=inline_menu(), cb_id=c.id
        )


    
    elif data == "delay_1200":
        POST_DELAY_SECONDS = 1200
        save_delay_seconds(POST_DELAY_SECONDS)
        DELAY_EVENT.set()
        write_auto_flag("off")
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="⏱️ קצב שידור עודכן: כל 20 דקות (מצב ידני)",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "delay_1500":
        POST_DELAY_SECONDS = 1500
        save_delay_seconds(POST_DELAY_SECONDS)
        DELAY_EVENT.set()
        write_auto_flag("off")
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="⏱️ קצב שידור עודכן: כל 25 דקות (מצב ידני)",
                          reply_markup=inline_menu(), cb_id=c.id)

    elif data == "delay_1800":
        POST_DELAY_SECONDS = 1800
        save_delay_seconds(POST_DELAY_SECONDS)
        DELAY_EVENT.set()
        write_auto_flag("off")
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text="⏱️ קצב שידור עודכן: כל 30 דקות (מצב ידני)",
                          reply_markup=inline_menu(), cb_id=c.id)


    elif data == "toggle_auto_mode":
        current = read_auto_flag()
        new_mode = "off" if current == "on" else "on"
        write_auto_flag(new_mode)
        new_label = "🟢 מצב אוטומטי פעיל" if new_mode == "on" else "🔴 מצב ידני בלבד"
        safe_edit_message(bot, chat_id=chat_id, message=c.message,
                          new_text=f"החלפתי מצב שידור: {new_label}",
                          reply_markup=inline_menu(), cb_id=c.id)


    elif data == "delete_source_file":
        ok = delete_source_csv_file()
        msg_txt = "🧹 workfile.csv אופס לריק (נשמרו רק כותרות). התור לא שונה." if ok else "שגיאה במחיקת workfile.csv"
        safe_edit_message(
            bot, chat_id=chat_id, message=c.message,
            new_text=msg_txt, reply_markup=inline_menu(), cb_id=c.id
        )

    else:
        bot.answer_callback_query(c.id)


# ========= FORWARD HANDLER =========
@bot.message_handler(
    func=lambda m: EXPECTING_TARGET.get(getattr(m.from_user, "id", None)) is not None,
    content_types=['text', 'photo', 'video', 'document', 'animation', 'audio', 'voice']
)
def handle_forward_for_target(msg):
    mode = EXPECTING_TARGET.get(getattr(msg.from_user, "id", None))
    fwd = getattr(msg, "forward_from_chat", None)
    if not fwd:
        bot.reply_to(msg, "לא זיהיתי *הודעה מועברת מערוץ*. נסה/י שוב: העבר/י פוסט מהערוץ הרצוי.", parse_mode='Markdown')
        return

    chat_id = fwd.id
    username = fwd.username or ""
    target_value = f"@{username}" if username else chat_id

    if mode == "public":
        _save_preset(PUBLIC_PRESET_FILE, target_value)
        label = "ציבורי"
    else:
        _save_preset(PRIVATE_PRESET_FILE, target_value)
        label = "פרטי"

    global CURRENT_TARGET
    CURRENT_TARGET = resolve_target(target_value)
    ok, details = check_and_probe_target(CURRENT_TARGET)

    EXPECTING_TARGET.pop(msg.from_user.id, None)

    bot.reply_to(msg,
        f"✅ נשמר יעד {label}: {target_value}\n"
        f"{details}\n\nאפשר לעבור בין יעדים מהתפריט: 🎯/🔒"
    )


# ========= UPLOAD CSV =========
@bot.message_handler(commands=['upload_source'])
def cmd_upload_source(msg):
    if not _is_admin(msg):
        bot.reply_to(msg, "אין הרשאה.")
        return
    uid = getattr(msg.from_user, "id", None)
    if uid is None:
        bot.reply_to(msg, "שגיאה בזיהוי משתמש.")
        return
    EXPECTING_UPLOAD.add(uid)
    bot.reply_to(msg,
        "שלח/י עכשיו קובץ CSV (כמסמך). הבוט ימפה את העמודות אוטומטית, יעדכן את workfile.csv וימזג אל התור.\n"
        "לא נוגעים בתזמונים, ולא מאפסים את התור."
    )

@bot.message_handler(content_types=['document'])
def on_document(msg):
    uid = getattr(msg.from_user, "id", None)
    if uid not in EXPECTING_UPLOAD:
        return

    try:
        doc = msg.document
        filename = (doc.file_name or "").lower()
        if not filename.endswith(".csv"):
            bot.reply_to(msg, "זה לא נראה כמו CSV. נסה/י שוב עם קובץ .csv")
            return

        # הורדה
        file_info = bot.get_file(doc.file_id)
        file_bytes = bot.download_file(file_info.file_path)

        csv_text = _decode_csv_bytes(file_bytes)

        # קריאה RAW כדי לזהות $/USD לפני נורמליזציה
        from io import StringIO
        raw_reader = csv.DictReader(StringIO(csv_text))
        rows_raw = [dict(r) for r in raw_reader]

        # בדיקת דגל המרה לקובץ הבא
        convert_rate = None
        if os.path.exists(CONVERT_NEXT_FLAG_FILE):
            try:
                with open(CONVERT_NEXT_FLAG_FILE, "r", encoding="utf-8") as f:
                    convert_rate = float((f.read() or "").strip() or USD_TO_ILS_RATE_DEFAULT)
            except Exception:
                convert_rate = USD_TO_ILS_RATE_DEFAULT
            try:
                os.remove(CONVERT_NEXT_FLAG_FILE)
            except Exception:
                pass

        # המרה (אם נדרש) + נורמליזציה
        rows = _rows_with_optional_usd_to_ils(rows_raw, convert_rate)

        # כתיבה + מיזוג
        with FILE_LOCK:
            write_products(DATA_CSV, rows)
            # מיזוג ללא כפילויות
            pending_rows = read_products(PENDING_CSV)

            def key_of(r):
                item_id = (r.get("ItemId") or "").strip()
                title = (r.get("Title") or "").strip()
                buy = (r.get("BuyLink") or "").strip()
                return (item_id if item_id else None, title if not item_id else None, buy)

            existing_keys = {key_of(r) for r in pending_rows}
            added = 0
            already = 0
            for r in rows:
                k = key_of(r)
                if k in existing_keys:
                    already += 1
                    continue
                pending_rows.append(r)
                existing_keys.add(k)
                added += 1
            write_products(PENDING_CSV, pending_rows)
            total_after = len(pending_rows)

        extra_line = ""
        if convert_rate:
            extra_line = f"\n💱 בוצעה המרה לש\"ח בשער {convert_rate} לכל מחירי הדולר בקובץ זה."

        bot.reply_to(msg,
            "✅ הקובץ נקלט בהצלחה.\n"
            f"נוספו לתור: {added}\nכבר היו בתור/כפולים: {already}\nסה\"כ בתור כעת: {total_after}"
            + extra_line +
            "\n\nהשידור ממשיך בקצב שנקבע. אפשר לבדוק '📊 סטטוס שידור' בתפריט."
        )

    except Exception as e:
        bot.reply_to(msg, f"שגיאה בעיבוד הקובץ: {e}")
    finally:
        if uid in EXPECTING_UPLOAD:
            EXPECTING_UPLOAD.remove(uid)


# ========= TEXT COMMANDS =========
@bot.message_handler(commands=['cancel'])
def cmd_cancel(msg):
    uid = getattr(msg.from_user, "id", None)
    if uid is not None:
        EXPECTING_TARGET.pop(uid, None)
        EXPECTING_UPLOAD.discard(uid)
    bot.reply_to(msg, "בוטל מצב בחירת יעד/העלאה. שלח /start לתפריט.")

@bot.message_handler(commands=['list_pending'])
def list_pending(msg):
    with FILE_LOCK:
        pending = read_products(PENDING_CSV)
    if not pending:
        bot.reply_to(msg, "אין פוסטים ממתינים ✅")
        return
    preview = pending[:10]
    lines = []
    for i, p in enumerate(preview, start=1):
        title = str(p.get('Title',''))[:80]
        sale = p.get('SalePrice','')
        disc = p.get('Discount','')
        rating = p.get('Rating','')
        lines.append(f"{i}. {title}\n   מחיר מבצע: {sale} | הנחה: {disc} | דירוג: {rating}")
    more = len(pending) - len(preview)
    if more > 0:
        lines.append(f"...ועוד {more} בהמתנה")
    bot.reply_to(msg, "פוסטים ממתינים:\n\n" + "\n".join(lines))

@bot.message_handler(commands=['clear_pending'])
def clear_pending(msg):
    if not _is_admin(msg):
        bot.reply_to(msg, "אין הרשאה.")
        return
    with FILE_LOCK:
        write_products(PENDING_CSV, [])
    bot.reply_to(msg, "נוקה התור של הפוסטים הממתינים 🧹")

@bot.message_handler(commands=['reset_pending'])
def reset_pending(msg):
    if not _is_admin(msg):
        bot.reply_to(msg, "אין הרשאה.")
        return
    src = read_products(DATA_CSV)
    with FILE_LOCK:
        write_products(PENDING_CSV, src)
    bot.reply_to(msg, "התור אופס מהקובץ הראשי והכול נטען מחדש 🔄")

@bot.message_handler(commands=['skip_one'])
def skip_one(msg):
    if not _is_admin(msg):
        bot.reply_to(msg, "אין הרשאה.")
        return
    with FILE_LOCK:
        pending = read_products(PENDING_CSV)
        if not pending:
            bot.reply_to(msg, "אין מה לדלג – אין פוסטים ממתינים.")
            return
        write_products(PENDING_CSV, pending[1:])
    bot.reply_to(msg, "דילגתי על הפוסט הבא ✅")

@bot.message_handler(commands=['peek_next'])
def peek_next(msg):
    with FILE_LOCK:
        pending = read_products(PENDING_CSV)
    if not pending:
        bot.reply_to(msg, "אין פוסטים ממתינים ✅")
        return
    nxt = pending[0]
    txt = "<b>הפריט הבא בתור:</b>\n\n" + "\n".join([f"<b>{k}:</b> {v}" for k,v in nxt.items()])
    bot.reply_to(msg, txt, parse_mode='HTML')

@bot.message_handler(commands=['peek_idx'])
def peek_idx(msg):
    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(msg, "שימוש: /peek_idx N  (לדוגמה: /peek_idx 3)")
        return
    idx = int(parts[1])
    with FILE_LOCK:
        pending = read_products(PENDING_CSV)
    if not pending:
        bot.reply_to(msg, "אין פוסטים ממתינים ✅")
        return
    if idx < 1 or idx > len(pending):
        bot.reply_to(msg, f"אינדקס מחוץ לטווח. יש כרגע {len(pending)} פוסטים בתור.")
        return
    item = pending[idx-1]
    txt = f"<b>פריט #{idx} בתור:</b>\n\n" + "\n".join([f"<b>{k}:</b> {v}" for k,v in item.items()])
    bot.reply_to(msg, txt, parse_mode='HTML')

@bot.message_handler(commands=['pending_status'])
def pending_status(msg):
    with FILE_LOCK:
        pending = read_products(PENDING_CSV)
    count = len(pending)
    now_il = datetime.now(tz=IL_TZ)
    schedule_line = "🕰️ מצב: מתוזמן (שינה פעיל)" if is_schedule_enforced() else "🟢 מצב: תמיד-פעיל"
    delay_line = f"⏳ מרווח נוכחי: {POST_DELAY_SECONDS//60} דק׳ ({POST_DELAY_SECONDS} שניות)"
    target_line = f"🎯 יעד נוכחי: {CURRENT_TARGET}"
    if count == 0:
        bot.reply_to(msg, f"{schedule_line}\n{delay_line}\n{target_line}\nאין פוסטים ממתינים ✅")
        return
    total_seconds = (count - 1) * POST_DELAY_SECONDS
    eta = now_il + timedelta(seconds=total_seconds)
    eta_str = eta.strftime("%Y-%m-%d %H:%M:%S %Z")
    next_eta = now_il.strftime("%Y-%m-%d %H:%M:%S %Z")
    status_line = "🎙️ שידור אפשרי עכשיו" if not is_quiet_now(now_il) else "⏸️ כרגע מחוץ לחלון השידור"
    msg_text = (
        f"{schedule_line}\n"
        f"{status_line}\n"
        f"{delay_line}\n"
        f"{target_line}\n"
        f"יש כרגע <b>{count}</b> פוסטים ממתינים.\n"
        f"⏱️ השידור הבא (תיאוריה לפי מרווח): <b>{next_eta}</b>\n"
        f"🕒 שעת השידור המשוערת של האחרון: <b>{eta_str}</b>\n"
        f"(מרווח בין פוסטים: {POST_DELAY_SECONDS} שניות)"
    )
    bot.reply_to(msg, msg_text, parse_mode='HTML')


# ========= HEALTH & START =========
@bot.message_handler(commands=['ping'])
def cmd_ping(msg):
    bot.reply_to(msg, "pong ✅")

@bot.message_handler(commands=['start', 'help', 'menu'])
def cmd_start(msg):
    try:
        uid = getattr(msg.from_user, "id", None)
        if uid is not None:
            EXPECTING_TARGET.pop(uid, None)
            EXPECTING_UPLOAD.discard(uid)
    except Exception:
        pass
    print(f"Instance={socket.gethostname()} | User={msg.from_user.id if msg.from_user else 'N/A'} sent /start", flush=True)
    bot.send_message(msg.chat.id, "בחר פעולה:", reply_markup=inline_menu())

@bot.message_handler(func=lambda m: isinstance(m.text, str) and m.text.strip().lower() in ('/start', 'start'))
def start_fallback(msg):
    cmd_start(msg)


# ========= SENDER LOOP =========

def auto_post_loop():
    if not os.path.exists(SCHEDULE_FLAG_FILE):
        set_schedule_enforced(True)
    init_pending()

    while True:
        if read_auto_flag() != "on":
            print(f"[{datetime.now(tz=IL_TZ)}] מצב ידני – שינה 5 שניות", flush=True)
            DELAY_EVENT.wait(timeout=5)
            DELAY_EVENT.clear()
            continue

        delay = get_auto_delay()
        if delay is None:
            print(f"[{datetime.now(tz=IL_TZ)}] מחוץ לשעות שידור – שינה 60 שניות", flush=True)
            DELAY_EVENT.wait(timeout=60)
            DELAY_EVENT.clear()
            continue

        with FILE_LOCK:
            pending = read_products(PENDING_CSV)
        if not pending:
            print(f"[{datetime.now(tz=IL_TZ)}] התור ריק – שינה 30 שניות", flush=True)
            DELAY_EVENT.wait(timeout=30)
            DELAY_EVENT.clear()
            continue

        send_next_locked("auto")
        print(f"[{datetime.now(tz=IL_TZ)}] פורסם. המתנה {delay} שניות", flush=True)
        DELAY_EVENT.wait(timeout=delay)
        DELAY_EVENT.clear()

    if not os.path.exists(SCHEDULE_FLAG_FILE):
        set_schedule_enforced(True)
    init_pending()

    while True:
        if is_quiet_now():
            now_il = datetime.now(tz=IL_TZ)
            print(f"[{now_il}] quiet hours ON – sleeping 30s", flush=True)
            DELAY_EVENT.wait(timeout=30)
            DELAY_EVENT.clear()
            continue

        with FILE_LOCK:
            pending = read_products(PENDING_CSV)
        if not pending:
            print(f"[{datetime.now(tz=IL_TZ)}] queue empty – sleeping 30s", flush=True)
            DELAY_EVENT.wait(timeout=30)
            DELAY_EVENT.clear()
            continue

        send_next_locked("loop")

        print(f"[{datetime.now(tz=IL_TZ)}] sleeping for {POST_DELAY_SECONDS}s (or until delay changed)", flush=True)
        DELAY_EVENT.wait(timeout=POST_DELAY_SECONDS)
        DELAY_EVENT.clear()


# ========= DEBUG LOG =========
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'animation', 'audio', 'voice', 'sticker'])
def _debug_log_everything(msg):
    try:
        uid = getattr(msg.from_user, "id", None)
        uname = f"@{msg.from_user.username}" if getattr(msg.from_user, "username", None) else uid
        kind = (msg.content_type or "unknown")
        txt = (msg.text or msg.caption or "")
        txt = txt[:80].replace("\n", " ")
        print(f"[DBG] inbound {kind} from {uname}: {txt}", flush=True)
    except Exception:
        pass


# ========= MAIN =========
@bot.message_handler(commands=['aff_test'])
def _aff_test(msg):
    try:
        # נבדוק שהמפתחות טעונים ונחזיר תשובה קצרה
        if AE is None:
            bot.reply_to(msg, "❌ AE: הלקוח לא מאותחל (חסר קובץ/מחלקה).")
            return
        # בדיקת מפתחות
        try:
            url = AE.generate_promotion_link("1005001234567890")  # בדיקת חתימה/ENV בלבד
            ok = bool(url.get("promotion_url"))
        except Exception as e:
            bot.reply_to(msg, f"❌ AE: {e}")
            return
        bot.reply_to(msg, "✅ AliExpress API: מפתחות נקלטו ונגישים.")
    except Exception as e:
        bot.reply_to(msg, f"❌ שגיאת בדיקה: {e}")



if __name__ == "__main__":
    print(f"Instance: {socket.gethostname()}", flush=True)
    try:
        me = bot.get_me()
        print(f"Bot: @{me.username} ({me.id})", flush=True)
    except Exception as e:
        print("getMe failed:", e, flush=True)

    _lock_handle = acquire_single_instance_lock(LOCK_PATH)
    if _lock_handle is None:
        print("Another instance is running (lock failed). Exiting.", flush=True)
        sys.exit(1)

    print_webhook_info()
    try:
        force_delete_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        try:
            bot.remove_webhook()
        except Exception as e2:
            print(f"[WARN] remove_webhook failed: {e2}", flush=True)
    print_webhook_info()

    t = threading.Thread(target=auto_post_loop, daemon=True)
    t.start()

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            msg = str(e)
            wait = 30 if "Conflict: terminated by other getUpdates request" in msg else 5
            print(f"[{datetime.now(tz=IL_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}] Polling error: {e}. Retrying in {wait}s...", flush=True)
            time.sleep(wait)


@bot.message_handler(commands=['toggle_mode'])
def toggle_mode(msg):
    if not _is_admin(msg):
        return
    mode = read_auto_flag()
    new_mode = "off" if mode == "on" else "on"
    write_auto_flag(new_mode)
    bot.reply_to(msg, f"✅ מצב אוטומטי עודכן ל: {'פעיל 🟢' if new_mode == 'on' else 'כבוי 🔴'}")



# ========= AI TRANSLATION VIA OPENAI =========
import openai

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("[WARN] מפתח OpenAI לא הוגדר – תרגום לא יהיה זמין.")
def translate_text_gpt(prompt):
    api_key = getattr(openai, "api_key", None) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OpenAI API key is missing.")
    openai.api_key = api_key
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "אתה מתרגם מומחה לעברית שיווקית"},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()

def translate_missing_fields(csv_path):
    if not OPENAI_API_KEY:
        print("[ERROR] אין מפתח OpenAI – דילוג על תרגום.")
        return

    updated_rows = []
    with open(csv_path, 'r', encoding='utf-8', newline='') as infile:
        reader = list(csv.DictReader(infile))
        fieldnames = reader[0].keys() if reader else []
        for row in reader:
            desc = row.get("ProductDesc", "").strip()
            needs_translation = any(not row.get(col, "").strip() for col in ["Opening", "Title", "Strengths"])
            if not desc or not needs_translation:
                updated_rows.append(row)
                continue

            prompt = f'''
הפריט הבא מופיע באתר קניות. נא לנסח פוסט שיווקי לטלגרם לפי ההוראות:

1. כתוב משפט פתיחה שיווקי, מצחיק או מגרה שמתאים למוצר (עד 15 מילים, שורת פתיחה בלבד).
2. כתוב תיאור שיווקי קצר של המוצר (שורה אחת עד שתיים).
3. הוסף 3 שורות עם יתרונות או תכונות של המוצר, כולל אימוג'ים מתאימים.

הנה תיאור המוצר:
"{desc}"
'''

            try:
                print(f"[GPT] 🧠 מתרגם שורה: {desc[:40]}...")
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "אתה עוזר שיווקי מומחה בכתיבה שיווקית בעברית"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.8
                )
                reply = response['choices'][0]['message']['content'].strip()
                print("[GPT ✅] הצלחה בתרגום!")
                lines = [line.strip() for line in reply.splitlines() if line.strip()]
                row["Opening"] = lines[0] if len(lines) > 0 else ""
                row["Title"] = lines[1] if len(lines) > 1 else ""
                row["Strengths"] = "\n".join(lines[2:5]) if len(lines) >= 5 else ""
                print(f"[AI] שורה עודכנה: {row.get('ProductDesc', '')[:30]}...")
            except Exception as e:
                print(f"[GPT ❌] שגיאה בתרגום: {str(e)}")
                print(f"[ERROR] שגיאה בתרגום AI: {e}")
            updated_rows.append(row)

    # כתיבה חזרה לקובץ
    with open(csv_path, 'w', encoding='utf-8', newline='') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)
    print("[✓] הסתיים תרגום אוטומטי של שדות חסרים.")


if __name__ == "__main__":
    translate_missing_fields(PENDING_CSV)  # הפעלת תרגום אוטומטי לשורות חסרות

# === Affiliates Inline Panel (UI) ===
def build_aff_panel():
    kb = _tb_types.InlineKeyboardMarkup(row_width=2) if _tb_types else None
    if kb is None:
        return None
    kb.add(
        _tb_types.InlineKeyboardButton("בדיקת API ✅", callback_data="aff:test"),
        _tb_types.InlineKeyboardButton("העשרת CSV 🔗", callback_data="aff:enrich"),
    )
    kb.add(
        _tb_types.InlineKeyboardButton("דילים חמים 🔥", callback_data="aff:hot")
    )
    return kb

@bot.message_handler(commands=['aff','aff_panel'])
def aff_panel_cmd(msg):
    if not _is_admin(msg):
        return
    if not _require_ae(msg):
        return
    kb = build_aff_panel()
    bot.send_message(msg.chat.id, "בחר פעולה:", reply_markup=kb)


# === Affiliates Inline Panel (callbacks) ===
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("aff:"))
def aff_callbacks(c):
    if not _is_admin(c.message):
        return bot.answer_callback_query(c.id, "אין הרשאה")
    if not _require_ae(c.message):
        return bot.answer_callback_query(c.id, "API לא מאותחל")

    data = c.data
    if data == "aff:test":
        return _aff_do_test(c)
    if data == "aff:enrich":
        return _aff_do_enrich(c)
    if data == "aff:hot":
        return _aff_ask_hot_params(c)

def _aff_do_test(c):
    try:
        AE.query_products(keywords="test", page_no=1, page_size=1)
        bot.answer_callback_query(c.id, "בדיקה הצליחה ✅", show_alert=False)
        bot.send_message(c.message.chat.id, "✅ AliExpress API מחובר ועובד (חתימה/ENV תקינים).", reply_markup=build_aff_panel())
    except Exception as e:
        bot.answer_callback_query(c.id, "שגיאה", show_alert=False)
        bot.send_message(c.message.chat.id, f"❌ בדיקת API נכשלה: {e}")

def _aff_do_enrich(c):
    try:
        in_path = globals().get('DATA_CSV', 'data/workfile.csv')
        out_path = in_path  # in-place
        changed = AE.enrich_csv(in_path, out_path, rate_limit_sec=0.6)

        if 'merge_from_data_into_pending' in globals():
            added, already, total_after = merge_from_data_into_pending()
            txt = (f"✅ העשרה הושלמה.\nעודכנו {changed} שורות.\n"
                   f"נוספו לתור: {added} | כפולים: {already} | סה״כ בתור: {total_after}")
        else:
            txt = f"✅ העשרה הושלמה (עודכנו {changed} שורות) — merge לתור לא זמין."

        bot.answer_callback_query(c.id, "בוצע ✅", show_alert=False)
        bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=build_aff_panel())
    except Exception as e:
        bot.answer_callback_query(c.id, "שגיאה", show_alert=False)
        bot.send_message(c.message.chat.id, f"❌ שגיאה בהעשרה: {e}")

def _aff_ask_hot_params(c):
    kb = _tb_types.InlineKeyboardMarkup() if _tb_types else None
    if kb:
        for kw in ("Bluetooth", "Headphones", "Power Bank", "LED Light"):
            kb.add(_tb_types.InlineKeyboardButton(f"{kw} ×10", callback_data=f"aff:hot_go:{kw}:10"))
    msg = bot.send_message(
        c.message.chat.id,
        "שלח/י מילת מפתח וכמות, למשל:\n`Bluetooth 10`\n\nאו הקש על אחד המקצרים:",
        parse_mode="Markdown",
        reply_markup=kb
    )
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("aff:hot_go:"))
def _aff_hot_go_cb(c):
    _, _, kw, cnt_str = c.data.split(":", 3)
    try:
        count = int(cnt_str)
    except:
        count = 10
    _aff_do_hot(c.message.chat.id, kw, count)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: m.text and any(m.text.lower().startswith(p) for p in ("/hot ", "/aff_hot ")) is False)
def _aff_hot_free_text(m):
    if not _is_admin(m):
        return
    text = m.text.strip()
    if any(w in text.lower() for w in ("bluetooth", "headphones", "power", "led")) and any(ch.isdigit() for ch in text):
        parts = text.replace("|", " ").split()
        kw = " ".join(p for p in parts if not p.isdigit()) or "Bluetooth"
        nums = [int(p) for p in parts if p.isdigit()]
        count = nums[0] if nums else 10
        if AE is None:
            return bot.reply_to(m, "❌ API לא מאותחל. ודא ENV.")
        _aff_do_hot(m.chat.id, kw, count)

def _aff_do_hot(chat_id, keyword: str, count: int):
    if AE is None:
        return bot.send_message(chat_id, "❌ API לא מאותחל. ודא ENV.")
    try:
        items, page = [], 1
        while len(items) < count and page < 50:
            batch = AE.query_products(
                keywords=keyword,
                page_no=page,
                page_size=min(20, count - len(items)),
                min_discount=40,
                min_rating=4.6
            )
            if not batch:
                break
            items.extend(batch)
            page += 1
            _time_aff.sleep(0.3)

        if not items:
            return bot.send_message(chat_id, f"לא נמצאו פריטים עבור '{keyword}'.")

        mapped = []
        for p in items:
            try:
                promo = AE.generate_affiliate_link(p["detail_url"]) or p["detail_url"]
                rating_pct = f"{round(float(p.get('rating', 0.0)) * 20, 1)}%" if p.get("rating") else ""
                mapped.append({
                    "ItemId": str(p.get("product_id", "")),
                    "ImageURL": p.get("image", ""),
                    "Title": p.get("title", ""),
                    "OriginalPrice": p.get("orig_price", ""),
                    "SalePrice": p.get("sale_price", ""),
                    "Discount": p.get("discount", ""),
                    "Rating": rating_pct,
                    "Orders": p.get("orders", ""),
                    "BuyLink": promo,
                    "CouponCode": "",
                    "Opening": "",
                    "Video Url": "",
                    "Strengths": "",
                })
                _time_aff.sleep(0.15)
            except Exception:
                continue

        DATA_CSV = globals().get('DATA_CSV', 'data/workfile.csv')
        PENDING_CSV = globals().get('PENDING_CSV', 'data/pending.csv')

        if not all(name in globals() for name in ('FILE_LOCK','read_products','write_products')):
            import csv, os
            os.makedirs("data", exist_ok=True)
            out_path = "data/hot.csv"
            headers = ["ItemId","ImageURL","Title","OriginalPrice","SalePrice","Discount","Rating","Orders","BuyLink","CouponCode","Opening","Video Url","Strengths"]
            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=headers)
                w.writeheader(); w.writerows(mapped)
            return bot.send_message(chat_id, f"✅ נמצאו {len(mapped)} פריטים. נשמרו ל־{out_path} (פונקציות תור לא אותרו).")

        with FILE_LOCK:
            pending_rows = read_products(PENDING_CSV)

            def key_of(r):
                item_id = (r.get("ItemId") or "").strip()
                title = (r.get("Title") or "").strip()
                buy = (r.get("BuyLink") or "").strip()
                return (item_id if item_id else None, title if not item_id else None, buy)

            existing = {key_of(r) for r in pending_rows}
            added = 0
            for r in mapped:
                k = key_of(r)
                if k in existing:
                    continue
                pending_rows.append(r)
                existing.add(k)
                added += 1

            write_products(PENDING_CSV, pending_rows)
            total_after = len(pending_rows)

        kb = _tb_types.InlineKeyboardMarkup() if _tb_types else None
        if kb:
            kb.add(_tb_types.InlineKeyboardButton("עוד דילים 🔁", callback_data="aff:hot"),
                   _tb_types.InlineKeyboardButton("לוח בקרה ↩️", callback_data="aff:test"))
        bot.send_message(chat_id, f"✅ נוספו לתור {added} פריטים חדשים ({len(items)} נמצאו) עבור '{keyword}'.\nסה״כ בתור: {total_after}", reply_markup=kb)

    except Exception as e:
        bot.send_message(chat_id, f"❌ שגיאה בשליפת דילים: {e}")
