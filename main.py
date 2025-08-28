# -*- coding: utf-8 -*-
"""
main.py — גרסה יציבה ומלאה:
- AliExpress Affiliate Client אמיתי (HMAC-SHA256, /sync)
- מניעת ריבוי אינסטנסים (409) ע"י נעילת socket
- בדיקת טוקן (401) ועצירה נקייה
- תור CSV עם ניהול בסיסי (עיון/מחיקה) + processed.csv
- תפריט /start עם כפתורים: פרסם עכשיו, מצב תור, שינוי דיליי, מצב אוטומטי, טען מחדש, בדיקת AliExpress, ניהול תור, משיכת מוצרים
- לולאת שידור אוטומטי אחידה עם דיליי, "שעות שקטות" אופציונליות
- נרמול טקסט ואימוג'ים (NFC) לכל הפלט
"""


import os, sys, csv, json, time, socket, threading, unicodedata, hmac, hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List
import datetime as dt
import requests
import json
# safe fcntl import
try:
    import fcntl  # POSIX only
except Exception:
    fcntl = None  # fallback for non-POSIX or missing module
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ---- Single-instance lock path (always defined, after BOT_TOKEN & imports) ----
_tok_for_lock = BOT_TOKEN if 'BOT_TOKEN' in globals() else os.getenv("BOT_TOKEN", "")
try:
    RUN_LOCK_PATH = os.getenv("RUN_LOCK_PATH") or f"/tmp/tg-bot-{hashlib.sha1((_tok_for_lock or '').encode('utf-8')).hexdigest()[:8]}.lock"
except Exception:
    RUN_LOCK_PATH = os.getenv("RUN_LOCK_PATH") or "/tmp/tg-bot.lock"

# ========= פלט מיידי ללוגים =========
os.environ.setdefault("PYTHONUNBUFFERED", "1")
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ========= תלותי טלגרם =========
import telebot
from telebot import types

# ========= קונפיג/נתיבים =========
BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")

# ========= Persistent Config (channel & admin) =========
CONFIG_PATH = os.path.join(BASE_DIR, "config.json") if "BASE_DIR" in globals() else os.path.join(os.getcwd(), "config.json")

def _cfg_load() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _cfg_save(cfg: dict) -> None:
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        print(f"[{now_str()}] Failed saving config: {e}", flush=True)

GLOBAL_CONFIG = _cfg_load()

def is_admin(user_id: int) -> bool:
    try:
        env_admin = os.getenv("ADMIN_USER_ID", "").strip()
        if env_admin:
            try:
                if int(env_admin) == int(user_id):
                    return True
            except Exception:
                pass
        owner = GLOBAL_CONFIG.get("admin_user_id")
        if owner is not None:
            return int(owner) == int(user_id)
        # If not set, allow first setter to become admin
        return True
    except Exception:
        return False

def ensure_admin(user_id: int):
    # Set first admin if not set
    if GLOBAL_CONFIG.get("admin_user_id") is None:
        GLOBAL_CONFIG["admin_user_id"] = int(user_id)
        _cfg_save(GLOBAL_CONFIG)

def set_channel_binding(kind: str, value: str, setter_id: int):
    # kind: "public" or "private"
    GLOBAL_CONFIG["channel_type"] = "public" if kind == "public" else "private"
    GLOBAL_CONFIG["channel_id"] = str(value)
    ensure_admin(setter_id)
    _cfg_save(GLOBAL_CONFIG)

def get_current_channel_id():
    # preference: config file → env → empty
    cid = (GLOBAL_CONFIG.get("channel_id") or os.getenv("CHANNEL_ID", "")).strip()
    return cid

def parse_chat_id(cid: str):
    cid = str(cid).strip()
    if not cid:
        return cid
    if cid.startswith("@"):
        return cid  # public channel username
    # numeric id (private/public)
    try:
        return int(cid)
    except Exception:
        return cid
EMPTY_QUEUE_SLEEP = int(os.getenv("EMPTY_QUEUE_SLEEP", "300"))  # seconds to wait when queue is empty
os.makedirs(BASE_DIR, exist_ok=True)

QUEUE_CSV     = os.path.join(BASE_DIR, "queue.csv")       # תור מוצרים לפרסום
PROCESSED_CSV = os.path.join(BASE_DIR, "processed.csv")   # מה שפורסם
STATE_JSON    = os.path.join(BASE_DIR, "state.json")      # index/delay/auto
LOCK_FILE     = os.path.join(BASE_DIR, "bot.lock")        # קובץ נעילה
AUTO_FLAG_FILE= os.path.join(BASE_DIR, "auto_mode.flag")  # on/off
KEYWORDS_TXT  = os.path.join(BASE_DIR, "keywords.txt")
AE_LAST_REQ_JSON = os.path.join(BASE_DIR, "ae_last_request.json")
AE_LAST_RES_JSON = os.path.join(BASE_DIR, "ae_last_response.json")
UPLOADS_DIR   = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

REQUIRED_UPLOAD_COLUMNS = [
    "ProductId","Image Url","Video Url","Product Desc","Origin Price","Discount Price","Discount","Currency",
    "Direct linking commission rate (%)","Estimated direct linking commission","Indirect linking commission rate (%)",
    "Estimated indirect linking commission","Sales180Day","Positive Feedback","Promotion Url","Code Name","Code Start Time",
    "Code End Time","Code Value","Code Quantity","Code Minimum Spend"
]
    # מילות חיפוש לאוטו-פצ'ר (אופציונלי)

TZ = ZoneInfo("Asia/Jerusalem")

# ========= משתני סביבה =========
BOT_TOKEN   = (os.environ.get("BOT_TOKEN") or "").strip()
CHANNEL_ID  = (os.environ.get("CHANNEL_ID") or "").strip()   # "@yourchannel" או chat_id מספרי
JOIN_LINK   = (os.environ.get("JOIN_LINK") or "").strip()
DEFAULT_DELAY_SEC = int(os.environ.get("POST_DELAY_SECONDS", "1200"))  # ברירת מחדל 20 דקות

# Quiet hours (לא חובה): פורמט "HH:MM"
QUIET_START = (os.environ.get("QUIET_START_HHMM") or "").strip()  # למשל "23:00"
QUIET_END   = (os.environ.get("QUIET_END_HHMM") or "").strip()    # למשל "07:00"
QUIET_WEEKEND = (os.environ.get("QUIET_WEEKEND", "false").lower() in ("1","true","yes","on"))

# AliExpress env
AE_APP_KEY    = (os.environ.get("AE_APP_KEY") or "").strip()
AE_APP_SECRET = (os.environ.get("AE_APP_SECRET") or "").strip()
AE_TRACKING_ID= (os.environ.get("AE_TRACKING_ID") or "").strip()
AE_TARGET_LANGUAGE = (os.environ.get("AE_TARGET_LANGUAGE") or "HE").strip()
AE_TARGET_CURRENCY = (os.environ.get("AE_TARGET_CURRENCY") or "ILS").strip()
AE_SHIP_TO_COUNTRY = (os.environ.get("AE_SHIP_TO_COUNTRY") or "IL").strip()

# ========= בדיקת טוקן =========
if not BOT_TOKEN:
    print("FATAL: חסר BOT_TOKEN בסביבת ההרצה. עצירה.", flush=True)
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ========= מניעת ריבוי אינסטנסים (409) =========
try:
    _lock_fp = open(LOCK_FILE, "w")
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock_socket.bind(("127.0.0.1", 58765))  # אם תפוס, תהליך אחר כבר רץ
    _lock_socket.listen(1)
except OSError:
    print("Another instance is already running (port lock busy). Exiting to avoid 409.", flush=True)
    sys.exit(0)
except Exception as e:
    print(f"WARNING: lock init issue: {e}", flush=True)

# ========= כלי עזר =========
FILE_LOCK = threading.RLock()
DELAY_EVENT = threading.Event()

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")

def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def read_state() -> Dict[str, Any]:
    st = {"index": 0, "auto": True, "delay": DEFAULT_DELAY_SEC}
    if os.path.exists(STATE_JSON):
        try:
            with open(STATE_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            st.update({k: data.get(k, st[k]) for k in st.keys()})
        except Exception as e:
            print(f"[{now_str()}] read_state error: {e}", flush=True)
    return st

def write_state(st: Dict[str, Any]) -> None:
    try:
        with open(STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[{now_str()}] write_state error: {e}", flush=True)

def read_auto_flag() -> str:
    try:
        with open(AUTO_FLAG_FILE, "r", encoding="utf-8") as f:
            v = (f.read() or "").strip().lower()
            return "on" if v == "on" else "off"
    except FileNotFoundError:
        return "on"

def write_auto_flag(value: str) -> None:
    with open(AUTO_FLAG_FILE, "w", encoding="utf-8") as f:
        f.write("on" if str(value).lower() == "on" else "off")

def parse_hhmm(s: str) -> Optional[int]:
    try:
        hh, mm = s.split(":")
        return int(hh) * 60 + int(mm)
    except Exception:
        return None

def is_weekend(today: datetime) -> bool:
    # יום שישי=4, שבת=5 (Python weekday: Monday=0)
    return today.weekday() in (4, 5)

def is_quiet_now() -> bool:
    now = datetime.now(TZ)
    if QUIET_WEEKEND and is_weekend(now):
        return True
    start_m = parse_hhmm(QUIET_START) if QUIET_START else None
    end_m   = parse_hhmm(QUIET_END) if QUIET_END else None
    if start_m is None or end_m is None:
        return False
    cur_m = now.hour * 60 + now.minute
    if start_m <= end_m:
        return start_m <= cur_m < end_m
    else:
        # טווח שחוצה חצות
        return cur_m >= start_m or cur_m < end_m

def get_auto_delay() -> Optional[int]:
    # אם שעות שקטות — None; אחרת דיליי נוכחי
    if is_quiet_now():
        return None
    st = read_state()
    return max(60, int(st.get("delay", DEFAULT_DELAY_SEC)))

def read_csv_rows(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)

def write_csv_rows(path: str, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    if not rows and not fieldnames:
        # ריק לגמרי — נמחוק את הקובץ אם קיים
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return
    if fieldnames is None:
        if rows:
            # איחוד מפתחות לשימור שדות
            keys = set()
            for r in rows:
                keys.update(r.keys())
            fieldnames = list(keys)
        else:
            fieldnames = ["ProductId","Image Url","Product Desc","Opening","Title","Strengths","Promotion Url"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def read_queue() -> List[Dict[str, Any]]:
    with FILE_LOCK:
        return read_csv_rows(QUEUE_CSV)

def append_processed(row: Dict[str, Any]) -> None:
    with FILE_LOCK:
        exists = os.path.exists(PROCESSED_CSV)
        # שומר את כל השדות שקיימים בשורה
        fieldnames = list(row.keys())
        with open(PROCESSED_CSV, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if not exists:
                w.writeheader()
            w.writerow(row)

def append_to_queue(rows: List[Dict[str, Any]]) -> int:
    with FILE_LOCK:
        existing = read_queue()
        # Union of keys between existing and new rows
        keys = set()
        for r in existing:
            keys.update(r.keys())
        for r in rows:
            keys.update(r.keys())
        fieldnames = list(keys) if keys else ["ProductId","Image Url","Product Desc","Opening","Title","Strengths","Promotion Url"]
        all_rows = existing + rows
        write_csv_rows(QUEUE_CSV, all_rows, fieldnames=fieldnames)
        return len(rows)

# ========= AliExpress Affiliate Client =========
SESSION = None
try:
    import requests
    SESSION = requests.Session()
except Exception:
    SESSION = None
    pass  # נשתמש ב-requests כשיהיה זמין

API_ENDPOINTS = ["https://api-sg.aliexpress.com/sync", "https://api-sg.aliexpress.com/rest", "https://api.aliexpress.com/sync"]

# Ensure requests session has a UA to avoid anti-bot filters
if SESSION is not None:
    try:
        SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AE-Bot/1.0)", "Accept": "application/json"})
    except Exception:
        pass

class AliExpressAffiliateClient:
    """
    לקוח אפיליאייטים עם נסיונות endpoint/חתימה/טיימסטמפ וגם פרמטרים חלופיים (trackingId/tracking_id, pageNo/page_no וכו').
    כותב את הקריאה/תשובה האחרונות לקבצים: ae_last_request.json / ae_last_response.json
    """
    _METHODS = ["aliexpress.affiliate.product.query", "aliexpress.affiliate.product.search"]
    _ENDPOINTS = API_ENDPOINTS

    def __init__(self, app_key: Optional[str] = None, app_secret: Optional[str] = None, tracking_id: Optional[str] = None):
        self.app_key = (app_key or AE_APP_KEY)
        self.app_secret = (app_secret or AE_APP_SECRET)
        self.tracking_id = (tracking_id or AE_TRACKING_ID)
        self.lang = AE_TARGET_LANGUAGE
        self.currency = AE_TARGET_CURRENCY
        self.ship_to = AE_SHIP_TO_COUNTRY
        if not (self.app_key and self.app_secret and self.tracking_id):
            print("[WARN] AliExpress keys missing; set AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID", flush=True)

    def _ensure_ready(self):
        if not (self.app_key and self.app_secret and self.tracking_id):
            raise RuntimeError("Missing AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID")

    def _sign_hmac_sha256(self, params: Dict[str, Any]) -> str:
        base = "&".join(f"{k}={params[k]}" for k in sorted(params))
        import hmac, hashlib
        return hmac.new(self.app_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest().upper()

    def _sign_md5(self, params: Dict[str, Any]) -> str:
        base = "".join(f"{k}{params[k]}" for k in sorted(params))
        import hashlib
        return hashlib.md5((self.app_secret + base + self.app_secret).encode("utf-8")).hexdigest().upper()

    def _http(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if SESSION is None:
            raise RuntimeError("requests not available in this environment.")
        # write request snapshot
        try:
            with open(AE_LAST_REQ_JSON, "w", encoding="utf-8") as f:
                json.dump({"endpoint": endpoint, "params": params}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        r = SESSION.get(endpoint, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        # write response snapshot
        try:
            with open(AE_LAST_RES_JSON, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return data

    def _call_once(self, endpoint: str, method: str, biz_params: Dict[str, Any], sign_method: str, ts_mode: str) -> Dict[str, Any]:
        frame = {
            "app_key": self.app_key,
            "method": method,
            "format": "json",
            "sign_method": "HmacSHA256" if sign_method.lower() == "hmac" else "md5",
            "timestamp": (int(time.time()*1000) if ts_mode == "ms" else int(time.time())),
            "v": "1.0",
        }
        merged = {**frame, **{k: v for k, v in biz_params.items() if v is not None}}
        sign_params = {k: merged[k] for k in merged if k != "sign"}
        merged["sign"] = (self._sign_hmac_sha256(sign_params) if sign_method.lower()=="hmac" else self._sign_md5(sign_params))
        data = self._http(endpoint, merged)
        if isinstance(data, dict):
            data.setdefault("_debug", {})["endpoint"] = endpoint
            data["_debug"]["sign_method_used"] = frame["sign_method"]
            data["_debug"]["timestamp_mode"] = ts_mode
            data["_debug"]["method"] = method
        return data

    def _call_permutations(self, base_params: Dict[str, Any]) -> Dict[str, Any]:
        tries_sm_ts = [("hmac","ms"), ("hmac","s"), ("md5","s")]
        # param permutations
        param_variants = []
        for track_key in ("trackingId","tracking_id"):
            for page_no in ("pageNo","page_no"):
                for page_sz in ("pageSize","page_size"):
                    for ship_key in ("ship_to","shipTo","ship_to_country"):
                        p = dict(base_params)
                        p[track_key] = base_params.get("trackingId") or base_params.get("tracking_id")
                        p[page_no] = base_params.get("pageNo") or base_params.get("page_no") or 1
                        p[page_sz] = base_params.get("pageSize") or base_params.get("page_size") or 10
                        p[ship_key] = base_params.get("ship_to") or base_params.get("ship_to_country") or self.ship_to
                        # remove canonical keys to avoid duplicates inside the same dict
                        for k in ("trackingId","tracking_id","pageNo","page_no","pageSize","page_size","ship_to","ship_to_country","shipTo"):
                            if k not in (track_key, page_no, page_sz, ship_key) and k in p:
                                del p[k]
                        param_variants.append(p)

        last_data = None
        for ep in self._ENDPOINTS:
            for method in self._METHODS:
                for pv in param_variants:
                    for sign_m, ts_m in tries_sm_ts:
                        try:
                            data = self._call_once(ep, method, pv, sign_m, ts_m)
                            dstr = json.dumps(data, ensure_ascii=False)[:600].lower()
                            if any(x in dstr for x in ["signature", "sign", "invalid", "does not conform", "auth", "permission denied"]):
                                last_data = data
                                continue
                            return data
                        except Exception as e:
                            last_data = {"error": str(e), "_debug": {"endpoint": ep, "method": method, "variant": pv}}
                            continue
        return last_data or {}

    def _extract_items(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        def dig(d, path):
            cur = d
            for p in path:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(p)
            return cur
        for path in [
            ("resp_result", "result", "products"),
            ("resp_result", "result", "items"),
            ("result", "products"),
            ("result", "items"),
            ("items",),
        ]:
            v = dig(data, path)
            if isinstance(v, list):
                return v
        return []

    def search_products(self, keyword: str, page_size: int = 5) -> Dict[str, Any]:
        self._ensure_ready()
        base_params = {
            "trackingId": self.tracking_id,
            "keywords": keyword,
            "pageNo": 1,
            "pageSize": page_size,
            "target_language": self.lang,
            "target_currency": self.currency,
            "ship_to": self.ship_to,
        }
        data = self._call_permutations(base_params)

        # fallback to EN/USD
        items = self._extract_items(data)
        if not items:
            data_fb = self._call_permutations({**base_params, "target_language": "EN", "target_currency": "USD"})
            items = self._extract_items(data_fb)
            if items:
                data = data_fb

        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            pid   = it.get("productId") or it.get("product_id") or it.get("target_id") or it.get("itemId")
            title = it.get("product_title") or it.get("title") or it.get("subject") or it.get("name")
            image = it.get("image") or it.get("image_url") or it.get("main_image") or it.get("imageUrl")
            promo = it.get("promotion_link") or it.get("promotionUrl") or it.get("target_url")
            if not promo and pid:
                promo = f"https://www.aliexpress.com/item/{pid}.html"
            out.append({"productId": pid, "title": title, "imageUrl": image, "promotionUrl": promo})

        # surface errors
        if not out and isinstance(data, dict):
            for k in ("resp_msg","message","msg","errorMessage","error_message","error"):
                if k in data and data[k]:
                    return {"items": [], "error": str(data[k]), "_debug": data.get("_debug", {})}
            for k in ("resp_code","code","status"):
                if k in data and str(data[k]) not in ("0","200","OK","ok"):
                    return {"items": [], "error": f"code={data[k]}", "_debug": data.get("_debug", {})}
        return {"items": out, "_debug": data.get("_debug", {}) if isinstance(data, dict) else {}}

    def generate_promotion_link(self, item_id: str) -> Dict[str, Any]:
        self._ensure_ready()
        return {"promotion_url": f"https://www.aliexpress.com/item/{item_id}.html"}

AE = AliExpressAffiliateClient()

# ========= בניית פוסט =========
def build_post(row: Dict[str, Any]) -> str:
    opening = nfc((row.get("Opening") or "").strip() or "דיל חם נחת לערוץ! 🔥")
    # Prefer Title else Product Desc
    title_src = (row.get("Title") or row.get("Product Desc") or "").strip()
    title = nfc(title_src[:140])
    link    = (row.get("Promotion Url") or "").strip()
    item_id = (row.get("ProductId") or "ללא מספר").strip()

    # Strengths (optional free text)
    strengths_field = nfc((row.get("Strengths") or "").strip())
    strengths_lines: List[str] = []
    if strengths_field:
        for part in _re.split(r'[|\r\n]+', strengths_field):
            p = nfc(part.strip())
            if p:
                strengths_lines.append(p)


    # Enrich with price/coupon info from uploaded columns if present
    currency = (row.get("Currency") or "").strip()
    origin_price = (row.get("Origin Price") or "").strip()
    discount_price = (row.get("Discount Price") or "").strip()
    discount_pct = (row.get("Discount") or "").strip()
    code_name = (row.get("Code Name") or "").strip()
    code_val  = (row.get("Code Value") or "").strip()
    code_min  = (row.get("Code Minimum Spend") or "").strip()
    code_start= (row.get("Code Start Time") or "").strip()
    code_end  = (row.get("Code End Time") or "").strip()

    # Price line
    price_lines = []
    if discount_price and currency:
        price_lines.append(f"💸 מחיר אחרי הנחה: {discount_price} {currency}")
    if origin_price and currency:
        price_lines.append(f"🟡 מחיר קודם: <s>{origin_price} {currency}</s>")
    if discount_pct:
        price_lines.append(f"🔻 הנחה: {discount_pct}")

    # Coupon line
    def parse_dt(s: str):
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"]
        for fmt in fmts:
            try:
                return dt.datetime.strptime(s, fmt).replace(tzinfo=TZ)
            except Exception:
                pass
        return None

    coupon_lines = []
    if code_val:
        valid = ""
        dt_start = parse_dt(code_start) if code_start else None
        dt_end = parse_dt(code_end) if code_end else None
        now = dt.datetime.now(TZ)
        if dt_start and dt_end:
            if dt_start <= now <= dt_end:
                valid = f"(תקף עד {dt_end.strftime('%d.%m.%Y %H:%M')})"
            elif now < dt_start:
                valid = f"(יתחיל ב־{dt_start.strftime('%d.%m.%Y %H:%M')})"
            else:
                valid = "(פג תוקף)"
        elif dt_end:
            valid = f"(עד {dt_end.strftime('%d.%m.%Y %H:%M')})"
        if code_min:
            coupon_lines.append(f"🎟️ קופון: {code_val} {valid} | מינימום רכישה: {code_min}")
        else:
            coupon_lines.append(f"🎟️ קופון: {code_val} {valid}")

    # Ensure at least 3 bullets
    while len(strengths_lines) < 3:
        if price_lines:
            strengths_lines.append(price_lines.pop(0))
        elif coupon_lines:
            strengths_lines.append(coupon_lines.pop(0))
        else:
            strengths_lines.append("✨ יתרון בולט של המוצר")

    purchase_line = f'<a href="{link}">להזמנה מהירה לחצו כאן👉</a>' if link else ""
    join_line = f'<a href="{JOIN_LINK}">להצטרפות לערוץ לחצו עליי👉</a>' if JOIN_LINK else ""

    parts = [opening, "", title, ""] + strengths_lines[:3] + [""]
    # Append remaining price/coupon lines if any
    parts += price_lines
    parts += coupon_lines
    if purchase_line:
        parts.append(purchase_line)
    parts.append(f"מספר פריט: {nfc(item_id)}")
    if join_line:
        parts.append(join_line)
    return nfc("\n".join(parts))

def try_post_row(row: Dict[str, Any]) -> bool:
    msg = build_post(row)
    try:
        if not get_current_channel_id():
            print("WARNING: חסר CHANNEL_ID/הגדרה בקובץ config (הגדר דרך ⚙️ הגדרות ערוץ או משתנה סביבה) — לא ניתן לשלוח לערוץ.", flush=True)
            return False
        bot.send_message(parse_chat_id(get_current_channel_id()), msg, disable_web_page_preview=False)
        img = (row.get("Image Url") or "").strip()
        if img:
            bot.send_photo(parse_chat_id(get_current_channel_id()), img)
        return True
    except telebot.apihelper.ApiTelegramException as e:
        print(f"[{now_str()}] Telegram API error: {e}", flush=True)
        return False
    except Exception as e:
        print(f"[{now_str()}] post error: {e}", flush=True)
        return False

def post_next_from_queue() -> (bool, str):
    st = read_state()
    with FILE_LOCK:
        q = read_queue()
        if not q:
            return False, "התור ריק בקובץ queue.csv"
        idx = int(st.get("index", 0))
        if idx >= len(q):
            return False, "הגענו לסוף התור."
        row = q[idx]
        ok = try_post_row(row)
        if ok:
            append_processed(row)
            st["index"] = idx + 1
            write_state(st)
            return True, f"פורסם פריט #{st['index']} מתוך {len(q)}"
        else:
            return False, "שליחה נכשלה (ראה לוג)."


# ========= אבחון AliExpress =========
@bot.message_handler(commands=["ae_diag"])
def cmd_ae_diag(m: types.Message):
    lines = []
    try:
        ak = (AE_APP_KEY or "")
        tid = (AE_TRACKING_ID or "")
        lines.append("בדיקת הגדרות AliExpress:")
        lines.append(f"• app_key: {ak[:3]}***{ak[-3:] if len(ak)>6 else ''}")
        lines.append(f"• tracking_id: {tid[:3]}***{tid[-3:] if len(tid)>6 else ''}")
        lines.append(f"• target_language/currency: {AE_TARGET_LANGUAGE}/{AE_TARGET_CURRENCY}")
        lines.append(f"• ship_to: {AE_SHIP_TO_COUNTRY}")
        lines.append("מבצע קריאת בדיקה...")

        try:
            res = AE.search_products("test", page_size=1)
            items = res.get("items", [])
            dbg = res.get("_debug", {})
            if items:
                lines.append("✅ חיפוש החזיר תוצאה אחת לפחות.")
            else:
                lines.append("⚠️ אין תוצאות. ייתכן שזו מגבלת חשבון/מעקב או שגיאת חתימה.")
            if dbg:
                lines.append(f"debug: sign={dbg.get('sign_method_used')} ts={dbg.get('timestamp_mode')} ep={dbg.get('endpoint')}")
            if res.get("error"):
                lines.append(f"server hint: {res.get('error')}")
        except Exception as e:
            lines.append(f"❌ שגיאת קריאת API: {e}")
    except Exception as e:
        lines.append(f"שגיאה פנימית: {e}")

    bot.reply_to(m, nfc("\n".join(lines)))








@bot.message_handler(commands=["queue_status"])
def cmd_queue_status(m: types.Message):
    try:
        rows = read_queue()
        n = len(rows)
        preview = []
        for i, r in enumerate(rows[:3], start=1):
            pid = r.get("ProductId","")
            title = r.get("Title") or r.get("Product Desc") or ""
            preview.append(f"{i}. {title[:40]} (#{pid})")
        if not preview:
            bot.reply_to(m, nfc("התור ריק."))
            return
        bot.reply_to(m, nfc("כמות בתור: " + str(n) + "\n" + "\n".join(preview)))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאת בדיקת תור: {e}"))


@bot.message_handler(commands=["version"])
def cmd_version(m: types.Message):
    bot.reply_to(m, nfc("גרסה: v2025-08-28T21:25:48"))


@bot.message_handler(commands=["env_channels"])
def cmd_env_channels(m: types.Message):
    try:
        cid_cfg = GLOBAL_CONFIG.get("channel_id")
        cid_env = os.getenv("CHANNEL_ID", "")
        ctype = GLOBAL_CONFIG.get("channel_type")
        owner = GLOBAL_CONFIG.get("admin_user_id")
        bot.reply_to(m, nfc(f"config.channel_id={cid_cfg}\nenv.CHANNEL_ID={cid_env}\nchannel_type={ctype}\nadmin_user_id={owner}"))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאת env: {e}"))


def build_main_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("🗂️ ניהול תור"), types.KeyboardButton("➕ משוך מוצרים"))
    kb.add(types.KeyboardButton("📤 העלאת קובץ"), types.KeyboardButton("🚀 פרסם עכשיו"))
    kb.add(types.KeyboardButton("🔥 מוצרים חמים"), types.KeyboardButton("🔎 מוצר לפי ID"))
    kb.add(types.KeyboardButton("⚙️ הגדרות ערוץ"))
    return kb

@bot.message_handler(commands=["menu"])
def cmd_menu(m: types.Message):
    kb = build_main_menu_kb()
    bot.reply_to(m, nfc("תפריט ראשי"), reply_markup=kb)

@bot.message_handler(commands=["settings"])
def cmd_settings(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, nfc("אין הרשאה לפתוח הגדרות ערוץ."))
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(types.KeyboardButton("🔓 קבע ערוץ ציבורי (@username)"))
    kb.add(types.KeyboardButton("🔒 קבע ערוץ פרטי (שלח הודעה מהערוץ)"))
    kb.add(types.KeyboardButton("ℹ️ מצב ערוץ נוכחי"))
    bot.reply_to(m, nfc("בחר/י פעולה לערוץ:"), reply_markup=kb)


@bot.message_handler(commands=["set_public"])
def cmd_set_public(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, nfc("אין הרשאה.")); return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].startswith("@"):
        bot.reply_to(m, nfc("שימוש: /set_public @channel_username"))
        return
    set_channel_binding("public", parts[1], m.from_user.id)
    bot.reply_to(m, nfc(f"הוגדר ערוץ ציבורי: {parts[1]}"))

@bot.message_handler(commands=["set_private"])
def cmd_set_private(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, nfc("אין הרשאה.")); return
    parts = (m.text or "").split()
    if len(parts) < 2:
        bot.reply_to(m, nfc("שימוש: /set_private -100xxxxxxxxxx  (או שלח הודעה מועברת מהערוץ דרך הכפתור)"))
        return
    val = parts[1].strip()
    try:
        if not val.startswith("-100"):
            raise ValueError("Chat ID צריך להתחיל ב-100-")
        int(val)  # validate numeric
    except Exception:
        bot.reply_to(m, nfc("Chat ID לא תקין. דוגמה: -1001234567890"))
        return
    set_channel_binding("private", val, m.from_user.id)
    bot.reply_to(m, nfc(f"הוגדר ערוץ פרטי: {val}"))

# ========= תפריט /start =========
def make_main_kb() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    row1 = [types.KeyboardButton("🚀 פרסם עכשיו"), types.KeyboardButton("📜 מצב תור")]
    row2 = [types.KeyboardButton("⏱️ שינוי דיליי"), types.KeyboardButton("🔁 מצב אוטומטי")]
    row3 = [types.KeyboardButton("🔄 טען מחדש את התור"), types.KeyboardButton("🧪 בדיקת AliExpress"), types.KeyboardButton("🛠️ אבחון AliExpress")]
    row4 = [types.KeyboardButton("🗂️ ניהול תור"), types.KeyboardButton("➕ משוך מוצרים"), types.KeyboardButton("📤 העלאת קובץ")]
    kb.add(*row1); kb.add(*row2); kb.add(*row3); kb.add(*row4)
    kb.add(types.KeyboardButton("⚙️ הגדרות ערוץ"))
    kb.add(types.KeyboardButton("🔥 מוצרים חמים"))
    kb.add(types.KeyboardButton("🔎 מוצר לפי ID"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(m: types.Message):
    st = read_state()
    write_auto_flag("on" if st.get("auto", True) else "off")
    delay = int(st.get("delay", DEFAULT_DELAY_SEC))
    kb = make_main_kb()
    bot.send_message(
        m.chat.id,
        nfc(
            "ברוך הבא 👋\n"
            f"מצב אוטומטי: {'פעיל' if read_auto_flag()=='on' else 'כבוי'}\n"
            f"דיליי נוכחי: {delay//60} דק׳ ({delay} שניות)\n"
            f"שעות שקטות: {'מוגדר' if (QUIET_START and QUIET_END) else 'לא מוגדר'}\n"
            "בחר פעולה:"
        ),
        reply_markup=kb
    )

@bot.message_handler(func=lambda msg: msg.text == "🚀 פרסם עכשיו")
def on_post_now(m: types.Message):
    ok, info = post_next_from_queue()
    bot.reply_to(m, nfc(("✅ " if ok else "❌ ") + info))

@bot.message_handler(func=lambda msg: msg.text == "📜 מצב תור")
def on_queue_status(m: types.Message):
    st = read_state()
    qlen = len(read_queue())
    idx = int(st.get("index", 0))
    left = max(0, qlen - idx)
    bot.reply_to(m, nfc(f"בתור: {qlen} | פורסמו: {idx} | נשארו: {left}"))

@bot.message_handler(func=lambda msg: msg.text == "🔄 טען מחדש את התור")
def on_reload_queue(m: types.Message):
    st = read_state()
    q = read_queue()
    if int(st.get("index", 0)) > len(q):
        st["index"] = 0
        write_state(st)
    bot.reply_to(m, nfc(f"התור נטען מחדש. פריטים בקובץ: {len(q)}"))

@bot.message_handler(func=lambda msg: msg.text == "🔁 מצב אוטומטי")
def on_toggle_auto(m: types.Message):
    st = read_state()
    new_auto = not st.get("auto", True)
    st["auto"] = new_auto
    write_state(st)
    write_auto_flag("on" if new_auto else "off")
    DELAY_EVENT.set()
    bot.reply_to(m, nfc(f"מצב אוטומטי כעת: {'פעיל' if new_auto else 'כבוי'}"))

@bot.message_handler(func=lambda msg: msg.text == "⏱️ שינוי דיליי")
def on_change_delay(m: types.Message):
    bot.reply_to(m, nfc("שלח מספר שניות (למשל 1200) או דקות עם m (למשל 20m):"))

@bot.message_handler(regexp=r"^\s*\d+\s*(m|M)?\s*$")
def on_delay_value(m: types.Message):
    text = m.text.strip()
    minutes = text.lower().endswith("m")
    num = int(text[:-1]) if minutes else int(text)
    sec = num * 60 if minutes else num
    st = read_state()
    st["delay"] = max(60, sec)  # מינימום דקה
    write_state(st)
    DELAY_EVENT.set()
    bot.reply_to(m, nfc(f"דיליי עודכן ל-{st['delay']//60} דק׳ ({st['delay']} שניות)"))

# ========= בדיקת AliExpress =========
@bot.message_handler(func=lambda msg: msg.text == "🧪 בדיקת AliExpress")
@bot.message_handler(func=lambda msg: msg.text == "🛠️ אבחון AliExpress")
def on_test_ae(m: types.Message):
    msg = bot.reply_to(m, nfc("שלח מילת חיפוש קצרה (למשל: bluetooth speaker):"))
    bot.register_next_step_handler(msg, do_test_ae_keyword)

def do_test_ae_keyword(m: types.Message):
    kw = (m.text or "").strip()
    if not kw:
        bot.reply_to(m, nfc("לא התקבלה מילת חיפוש"))
        return
    try:
        res = AE.search_products(kw, page_size=5)
        items = res.get("items", [])
        if not items:
            bot.reply_to(m, nfc(f"לא נמצאו פריטים ל: {kw}"))
            return
        lines = [f"נמצאו {len(items)} תוצאות ל־“{kw}”:", ""]
        for it in items[:5]:
            title = nfc(it.get("title") or "")
            pid = it.get("productId") or it.get("product_id") or ""
            lines.append(f"• {title} (ID: {pid})")
        bot.reply_to(m, nfc("\n".join(lines)))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאה בבדיקה: {e}"))


# ========= העלאת קובץ ידנית =========
AWAITING_UPLOAD = {}

@bot.message_handler(func=lambda msg: msg.text == "📤 העלאת קובץ")
def on_upload_prompt(m: types.Message):
    AWAITING_UPLOAD[m.chat.id] = True
    bot.reply_to(m, nfc("שלח/י כעת קובץ CSV עם העמודות הבאות (אפשר גם TSV):\n" + ", ".join(REQUIRED_UPLOAD_COLUMNS)))

@bot.message_handler(content_types=['document'])
def on_document_upload(m: types.Message):
    want = AWAITING_UPLOAD.get(m.chat.id, False)
    filename = m.document.file_name or ""
    ext = (filename.split(".")[-1] if "." in filename else "").lower()
    if not want and ext not in ("csv","tsv","txt"):
        # not in upload mode and not recognized
        return
    try:
        file_info = bot.get_file(m.document.file_id)
        data = bot.download_file(file_info.file_path)
        ts = dt.datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        save_path = os.path.join(UPLOADS_DIR, f"{ts}-{filename or 'upload.csv'}")
        with open(save_path, "wb") as f:
            f.write(data)
        added, msg = import_products_from_csv(save_path)
        try:
            DELAY_EVENT.set()
        except Exception:
            pass
        bot.reply_to(m, nfc(f"נטען הקובץ {filename}. נוספו {added} פריטים לתור.\n{msg}"))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאה בקליטת הקובץ: {e}"))
    finally:
        AWAITING_UPLOAD[m.chat.id] = False

def import_products_from_csv(path: str) -> (int, str):
    # Detect delimiter
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            sample = f.read(4096)
        delim = ","
        if "\t" in sample and sample.count("\t") > sample.count(","):
            delim = "\t"
        else:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                delim = dialect.delimiter
            except Exception:
                pass
        # Read rows
        rows = []
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=delim)
            headers = [h.strip() for h in reader.fieldnames or []]
            # Validate required columns
            missing = [c for c in REQUIRED_UPLOAD_COLUMNS if c not in headers]
            warn = ""
            if missing:
                warn = "אזהרה: חסרות העמודות הבאות: " + ", ".join(missing)
            for r in reader:
                # Normalize keys
                r = { (k.strip() if k else k): (v.strip() if isinstance(v,str) else v) for k,v in r.items() }
                # Map to queue format + keep extras
                mapped = {
                    "ProductId": r.get("ProductId",""),
                    "Image Url": r.get("Image Url",""),
                    "Product Desc": r.get("Product Desc",""),
                    "Opening": "",
                    "Title": r.get("Product Desc",""),
                    "Strengths": "",
                    "Promotion Url": r.get("Promotion Url",""),
                }
                # Keep all original extra fields
                for k,v in r.items():
                    if k not in mapped:
                        mapped[k] = v
                rows.append(mapped)
        if not rows:
            return 0, (warn or "לא נמצאו שורות תקינות.")
        added = append_to_queue(rows)
        try:
            print(f"[{now_str()}] Import: {len(rows)} read, {added} appended to queue.csv", flush=True)
        except Exception:
            pass
        return added, (warn or "OK")
    except Exception as e:
        return 0, f"שגיאה בקריאת CSV: {e}"


# ========= בדיקות AliExpress (חמים / לפי ID / URL) =========
@bot.message_handler(func=lambda msg: msg.text == "🔥 מוצרים חמים")
def on_hot_products(m: types.Message):
    try:
        res = AE.hot_products(page_size=10)
        items = res.get("items", [])
        if not items:
            hint = ""
            if res.get("error"):
                hint = f"\n(רמז מהשרת: {res.get('error')})"
            dbg = res.get("_debug") or {}
            if dbg:
                hint += f"\n[debug ep={dbg.get('endpoint')} sign={dbg.get('sign_method_used')} ts={dbg.get('timestamp_mode')}]"
            bot.reply_to(m, nfc("לא נמצאו פריטים חמים." + hint))
            return
        rows = []
        for it in items:
            rows.append({
                "ProductId": it.get("productId") or "",
                "Image Url": it.get("imageUrl") or "",
                "Product Desc": it.get("title") or "",
                "Opening": "",
                "Title": it.get("title") or "",
                "Strengths": "",
                "Promotion Url": it.get("promotionUrl") or "",
            })
        added = append_to_queue(rows)
        bot.reply_to(m, nfc(f"נוספו {added} פריטים חמים לתור."))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאת חיפוש חמים: {e}"))

@bot.message_handler(func=lambda msg: msg.text == "🔎 מוצר לפי ID")
def on_prompt_id(m: types.Message):
    msg = bot.reply_to(m, nfc("שלח/י עכשיו ProductId (מספר, למשל 4001234567890) או הדבק כתובת מוצר AliExpress."))
    bot.register_next_step_handler(msg, on_receive_id_or_url)

def on_receive_id_or_url(m: types.Message):
    txt = (m.text or "").strip()
    pid = None
    import re as _re
    mobj = _re.search(r"/item/(\d+)\.html", txt)
    if mobj:
        pid = mobj.group(1)
    elif txt.isdigit():
        pid = txt
    if not pid:
        bot.reply_to(m, nfc("לא זוהה ProductId תקין."))
        return
    try:
        data = AE.get_product_detail(pid)
        items = []
        for path in [
            ("resp_result","result","result"),
            ("result","result"),
            ("result","items"),
            ("items",),
        ]:
            cur = data
            for pth in path:
                if isinstance(cur, dict):
                    cur = cur.get(pth)
                else:
                    cur = None
                    break
            if isinstance(cur, list):
                items = cur
                break
        if not items and isinstance(data, dict) and isinstance(data.get("result"), dict):
            items = [data.get("result")]

        if not items:
            hint = ""
            if isinstance(data, dict):
                for k in ("resp_msg","message","msg","errorMessage","error_message","error"):
                    if data.get(k):
                        hint = f"\n(רמז מהשרת: {data.get(k)})"; break
                dbg = data.get("_debug") or {}
                if dbg:
                    hint += f"\n[debug ep={dbg.get('endpoint')} sign={dbg.get('sign_method_used')} ts={dbg.get('timestamp_mode')}]"
            bot.reply_to(m, nfc(f"לא הוחזרו פרטים עבור המוצר {pid}.{hint}"))
            return

        it = items[0] if isinstance(items, list) else items
        row = {
            "ProductId": it.get("productId") or it.get("target_id") or pid,
            "Image Url": it.get("image") or it.get("image_url") or it.get("product_main_image_url") or "",
            "Product Desc": it.get("product_title") or it.get("title") or it.get("subject") or "",
            "Opening": "",
            "Title": it.get("product_title") or it.get("title") or it.get("subject") or "",
            "Strengths": "",
            "Promotion Url": it.get("promotion_link") or it.get("promotionUrl") or it.get("target_url") or f"https://www.aliexpress.com/item/{pid}.html",
        }
        added = append_to_queue([row])
        bot.reply_to(m, nfc(f"פרטי מוצר {pid} נוספו לתור ({added})."))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאת פירוט מוצר: {e}"))


def publish_next() -> bool:
    """
    Pulls first row from queue and posts it to the channel. Returns True if posted.
    Reuses the same logic used by the poster loop.
    """
    rows = read_queue()
    if not rows:
        return False
    row = rows[0]
    # try to post
    try:
        text = build_post(row)
        image = (row.get("Image Url") or "").strip()
        link  = (row.get("Promotion Url") or "").strip()
        # If there's an image URL, try photo; else send text
        if image:
            bot.send_photo(parse_chat_id(get_current_channel_id()), image, caption=text, parse_mode="HTML", disable_web_page_preview=True)
        else:
            bot.send_message(parse_chat_id(get_current_channel_id()), text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print(f"[{now_str()}] Failed to post: {e}", flush=True)
        return False
    # remove the posted row from queue
    rest = rows[1:]
    write_csv_rows(QUEUE_CSV, rest, fieldnames=list(rows[0].keys()))
    return True



from telebot import types as _types  # alias for inline keyboards

def open_inline_settings(chat_id: int):
    ikb = _types.InlineKeyboardMarkup(row_width=1)
    ikb.add(
        _types.InlineKeyboardButton("🔓 קבע ערוץ ציבורי (@username)", callback_data="set_public_inline"),
        _types.InlineKeyboardButton("🔒 קבע ערוץ פרטי (Forward מהערוץ)", callback_data="set_private_inline"),
        _types.InlineKeyboardButton("ℹ️ מצב ערוץ נוכחי", callback_data="status_inline")
    )
    bot.send_message(chat_id, nfc("הגדרות ערוץ (Inline) — בחר פעולה:"), reply_markup=ikb)

@bot.message_handler(commands=["settings"])
def cmd_settings(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, nfc("אין הרשאה לפתוח הגדרות ערוץ.")); return
    open_inline_settings(m.chat.id)

@bot.callback_query_handler(func=lambda c: c.data in ("set_public_inline","set_private_inline","status_inline"))
def on_settings_inline(c: types.CallbackQuery):
    try:
        if not is_admin(c.from_user.id):
            bot.answer_callback_query(c.id, "אין הרשאה", show_alert=True); return
        if c.data == "status_inline":
            cid = get_current_channel_id() or "(לא הוגדר)"
            ctype = GLOBAL_CONFIG.get("channel_type") or ("public" if str(cid).startswith("@") else "private" if str(cid).startswith("-100") else "(לא ידוע)")
            owner = GLOBAL_CONFIG.get("admin_user_id")
            bot.answer_callback_query(c.id, "נשלח מצב לערוץ")
            bot.send_message(c.message.chat.id, nfc(f"ערוץ נוכחי: {cid}\nסוג: {ctype}\nמנהל: {owner if owner else '(לא הוגדר)'}"))
        elif c.data == "set_public_inline":
            msg = bot.send_message(c.message.chat.id, nfc("שלח/י עכשיו @שם_הערוץ הציבורי (למשל @best_deals)."))
            bot.register_next_step_handler(msg, on_set_public_value)
        elif c.data == "set_private_inline":
            msg = bot.send_message(c.message.chat.id, nfc("העבר/י כעת הודעה מהערוץ (Forward) כדי לזהות את ה-Chat ID."))
            bot.register_next_step_handler(msg, on_set_private_value)
    except Exception as e:
        bot.answer_callback_query(c.id, f"שגיאה: {e}", show_alert=True)

# ========= Channel Settings =========
@bot.message_handler(func=lambda msg: msg.text == "⚙️ הגדרות ערוץ")
def on_channel_settings(m: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(types.KeyboardButton("🔓 קבע ערוץ ציבורי (@username)"))
    kb.add(types.KeyboardButton("🔒 קבע ערוץ פרטי (שלח הודעה מהערוץ)"))
    kb.add(types.KeyboardButton("ℹ️ מצב ערוץ נוכחי"))
    bot.reply_to(m, nfc("בחר/י פעולה לערוץ:"), reply_markup=kb)

@bot.message_handler(func=lambda msg: msg.text == "ℹ️ מצב ערוץ נוכחי")
def on_channel_status(m: types.Message):
    cid = get_current_channel_id() or "(לא הוגדר)"
    ctype = GLOBAL_CONFIG.get("channel_type") or ("public" if str(cid).startswith("@") else "private" if str(cid).startswith("-100") else "(לא ידוע)")
    owner = GLOBAL_CONFIG.get("admin_user_id")
    bot.reply_to(m, nfc(f"ערוץ נוכחי: {cid}\nסוג: {ctype}\nמנהל: {owner if owner else '(לא הוגדר)'}"))

@bot.message_handler(func=lambda msg: msg.text == "🔓 קבע ערוץ ציבורי (@username)")
def on_set_public_prompt(m: types.Message):
    bot.reply_to(m, nfc("שלח/י עכשיו @שם_הערוץ הציבורי (למשל @best_deals)."))
    bot.register_next_step_handler(m, on_set_public_value)

def on_set_public_value(m: types.Message):
    try:
        if not is_admin(m.from_user.id):
            bot.reply_to(m, nfc("אין הרשאה לביצוע פעולה זו."))
            return
        txt = (m.text or "").strip()
        if not txt.startswith("@") or " " in txt or len(txt) < 3:
            bot.reply_to(m, nfc("שם משתמש לא תקין. שלח/י שוב במבנה @channel_name."))
            return
        set_channel_binding("public", txt, m.from_user.id)
        bot.reply_to(m, nfc(f"הוגדר ערוץ ציבורי: {txt}\nודא/י שהבוט אדמין בערוץ עם הרשאת פרסום."))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאה בהגדרת ערוץ ציבורי: {e}"))

@bot.message_handler(func=lambda msg: msg.text == "🔒 קבע ערוץ פרטי (שלח הודעה מהערוץ)")
def on_set_private_prompt(m: types.Message):
    bot.reply_to(m, nfc("מעולה. העבר/י כעת הודעה מהערוץ שאליו ננעל. (Forward מהערוץ לכאן)"))
    bot.register_next_step_handler(m, on_set_private_value)

def _extract_channel_id_from_message(msg: types.Message):
    # Try multiple attributes because Telegram can populate different fields
    try:
        if getattr(msg, "forward_from_chat", None) and getattr(msg.forward_from_chat, "type", "") == "channel":
            return msg.forward_from_chat.id, getattr(msg.forward_from_chat, "title", None)
        if getattr(msg, "sender_chat", None) and getattr(msg.sender_chat, "type", "") == "channel":
            return msg.sender_chat.id, getattr(msg.sender_chat, "title", None)
        # If user posts directly from channel via bot (rare), msg.chat might be the channel
        if getattr(msg, "chat", None) and getattr(msg.chat, "type", "") == "channel":
            return msg.chat.id, getattr(msg.chat, "title", None)
    except Exception:
        pass
    return None, None

def on_set_private_value(m: types.Message):
    try:
        if not is_admin(m.from_user.id):
            bot.reply_to(m, nfc("אין הרשאה לביצוע פעולה זו."))
            return
        cid, title = _extract_channel_id_from_message(m)
        if not cid:
            bot.reply_to(m, nfc("לא הצלחתי לזהות את הערוץ מההודעה. ודא/י שזה Forward אמיתי מהערוץ."))
            return
        set_channel_binding("private", str(cid), m.from_user.id)
        bot.reply_to(m, nfc(f"הוגדר ערוץ פרטי: {cid}\nכותרת: {title or ''}\nהבוט חייב להיות אדמין בערוץ."))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאה בהגדרת ערוץ פרטי: {e}"))

# ========= משיכת מוצרים לתור =========
@bot.message_handler(func=lambda msg: msg.text == "➕ משוך מוצרים")
def on_fetch_to_queue(m: types.Message):
    msg = bot.reply_to(m, nfc("שלח מילת חיפוש ונמשוך עד 10 פריטים לתור:"))
    bot.register_next_step_handler(msg, do_fetch_keyword)

def do_fetch_keyword(m: types.Message):
    kw = (m.text or "").strip()
    if not kw:
        bot.reply_to(m, nfc("לא התקבלה מילת חיפוש"))
        return
    try:
        res = AE.search_products(kw, page_size=10)
        items = res.get("items", [])
        if not items:
            hint = ""
            if res.get("error"):
                hint = f"\n(רמז מהשרת: {res.get('error')})"
            dbg = res.get("_debug") or {}
            if dbg:
                hint += f"\n[debug sign={dbg.get('sign_method_used')} ts={dbg.get('timestamp_mode')}]"
            bot.reply_to(m, nfc(f"לא נמצאו פריטים ל: {kw}{hint}\nטיפים: נסו מילת חיפוש באנגלית, או ודאו שה-Tracking ID תקין."))
            return
        rows = []
        for it in items:
            rows.append({
                "ProductId": it.get("productId") or it.get("product_id") or "",
                "Image Url": it.get("imageUrl") or it.get("image") or "",
                "Product Desc": it.get("title") or "",
                "Opening": "",
                "Title": it.get("title") or "",
                "Strengths": "",
                "Promotion Url": it.get("promotionUrl") or it.get("promotion_url") or "",
            })
        added = append_to_queue(rows)
        bot.reply_to(m, nfc(f"נוספו {added} פריטים לתור מתוך החיפוש ל־“{kw}”"))
    except Exception as e:
        bot.reply_to(m, nfc(f"שגיאה במשיכה: {e}"))
# ========= ניהול תור (עיון/מחיקה) =========
BROWSE_INDEX: Dict[int, int] = {}  # chat_id -> index להצגה

@bot.message_handler(func=lambda msg: msg.text == "🗂️ ניהול תור")
def on_manage_queue(m: types.Message):
    BROWSE_INDEX[m.chat.id] = 0
    return send_queue_preview(m.chat.id)

def make_queue_inline_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("⬅️ הקודם", callback_data="queue_prev"),
        types.InlineKeyboardButton("➡️ הבא", callback_data="queue_next"),
    )
    kb.add(types.InlineKeyboardButton("🗑️ מחק פריט זה", callback_data="queue_del"))
    return kb

def format_queue_item(i: int, total: int, row: Dict[str, Any]) -> str:
    pid = row.get("ProductId") or ""
    title = row.get("Title") or row.get("Product Desc") or ""
    link = row.get("Promotion Url") or ""
    return nfc(
        f"פריט {i+1}/{total}\n"
        f"ID: {pid}\n"
        f"Title: {title[:120]}\n"
        f"Link: {link}"
    )

def send_queue_preview(chat_id: int):
    q = read_queue()
    if not q:
        bot.send_message(chat_id, nfc("התור ריק"))
        return
    i = BROWSE_INDEX.get(chat_id, 0)
    i = max(0, min(i, len(q)-1))
    BROWSE_INDEX[chat_id] = i
    row = q[i]
    bot.send_message(chat_id, format_queue_item(i, len(q), row), reply_markup=make_queue_inline_kb())

@bot.callback_query_handler(func=lambda c: c.data in ("queue_prev","queue_next","queue_del"))
def on_queue_cb(c: types.CallbackQuery):
    q = read_queue()
    if not q:
        bot.answer_callback_query(c.id, nfc("התור ריק"))
        bot.edit_message_text(nfc("התור ריק"), chat_id=c.message.chat.id, message_id=c.message.message_id)
        return
    i = BROWSE_INDEX.get(c.message.chat.id, 0)
    if c.data == "queue_prev":
        i = max(0, i-1)
        BROWSE_INDEX[c.message.chat.id] = i
        bot.edit_message_text(
            format_queue_item(i, len(q), q[i]),
            chat_id=c.message.chat.id, message_id=c.message.message_id,
            reply_markup=make_queue_inline_kb()
        )
        bot.answer_callback_query(c.id)
    elif c.data == "queue_next":
        i = min(len(q)-1, i+1)
        BROWSE_INDEX[c.message.chat.id] = i
        bot.edit_message_text(
            format_queue_item(i, len(q), q[i]),
            chat_id=c.message.chat.id, message_id=c.message.message_id,
            reply_markup=make_queue_inline_kb()
        )
        bot.answer_callback_query(c.id)
    elif c.data == "queue_del":
        with FILE_LOCK:
            q = read_queue()
            if not q:
                bot.answer_callback_query(c.id, nfc("התור ריק"))
                return
            i = BROWSE_INDEX.get(c.message.chat.id, 0)
            i = max(0, min(i, len(q)-1))
            removed = q.pop(i)
            # שמור סדר שדות קיים
            fieldnames = list(removed.keys()) if removed else (list(q[0].keys()) if q else None)
            write_csv_rows(QUEUE_CSV, q, fieldnames=fieldnames)
            # עדכון אינדקס תצוגה
            if i >= len(q):
                i = max(0, len(q)-1)
            BROWSE_INDEX[c.message.chat.id] = i
        if q:
            bot.edit_message_text(
                format_queue_item(i, len(q), q[i]),
                chat_id=c.message.chat.id, message_id=c.message.message_id,
                reply_markup=make_queue_inline_kb()
            )
        else:
            bot.edit_message_text(nfc("התור ריק"), chat_id=c.message.chat.id, message_id=c.message.message_id)
        bot.answer_callback_query(c.id, nfc("נמחק"))

# ========= לולאת שידור אוטומטי =========
def poster_loop():
    print(f"[{now_str()}] 🤖 Bot started with delay of {DEFAULT_DELAY_SEC} seconds", flush=True)
    while True:
        auto_on = (read_auto_flag() == "on") and read_state().get("auto", True)
        if not auto_on:
            # ידני
            time.sleep(5)
            continue
        delay = get_auto_delay()
        if delay is None:
            print(f"[{now_str()}] מחוץ לשעות שידור – שינה 60 שניות", flush=True)
            DELAY_EVENT.wait(timeout=60)
            DELAY_EVENT.clear()
            continue
        ok, info = post_next_from_queue()
        print(f"[{now_str()}] Auto-post: {info}", flush=True)
        DELAY_EVENT.wait(timeout=delay if ok else EMPTY_QUEUE_SLEEP)
        DELAY_EVENT.clear()

# ========= main =========

# ========= Webhook / Polling selection =========
USE_WEBHOOK = (os.environ.get("USE_WEBHOOK", "false").lower() in ("1","true","yes","on"))
WEBHOOK_BASE_URL = (os.environ.get("WEBHOOK_BASE_URL") or "").rstrip("/")  # e.g., https://your-app.up.railway.app
WEBHOOK_SECRET = (os.environ.get("WEBHOOK_SECRET") or "").strip()
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # unique path; secret header adds security

if USE_WEBHOOK:
    from flask import Flask, request, abort
    app = Flask(__name__)

    @app.route("/", methods=["GET"])
    def root_ok():
        return "OK", 200

    @app.route(WEBHOOK_PATH, methods=["POST"])
    def telegram_webhook():
        # Optional: verify Telegram secret header
        if WEBHOOK_SECRET:
            secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret_hdr != WEBHOOK_SECRET:
                return abort(403)
        try:
            data = request.get_data().decode("utf-8")
            update = telebot.types.Update.de_json(data)
        except Exception:
            return abort(400)
        bot.process_new_updates([update])
        return "OK", 200


# ========= Dual-mode: Webhook if available, else Polling =========
USE_WEBHOOK = (os.environ.get("USE_WEBHOOK", "false").lower() in ("1","true","yes","on"))
WEBHOOK_BASE_URL = (os.environ.get("WEBHOOK_BASE_URL") or "").rstrip("/")
WEBHOOK_SECRET = (os.environ.get("WEBHOOK_SECRET") or "").strip()
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

def run_webhook():
    from flask import Flask, request, abort
    app = Flask(__name__)

    @app.route("/", methods=["GET"])
    def root_ok():
        return "OK", 200

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return "ok", 200

    @app.route(WEBHOOK_PATH, methods=["POST"])
    def telegram_webhook():
        if WEBHOOK_SECRET:
            secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret_hdr != WEBHOOK_SECRET:
                return abort(403)
        try:
            data = request.get_data().decode("utf-8")
            update = telebot.types.Update.de_json(data)
        except Exception:
            return abort(400)
        bot.process_new_updates([update])
        return "OK", 200

    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    full_url = WEBHOOK_BASE_URL + WEBHOOK_PATH
    bot.set_webhook(url=full_url, secret_token=(WEBHOOK_SECRET or None))
    port = int(os.environ.get("PORT", "8080"))
    print(f"[{now_str()}] 🌐 Webhook listening on :{port} at {full_url}", flush=True)
    from waitress import serve as _serve
    _serve(app, host="0.0.0.0", port=port)

def run_polling():
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except telebot.apihelper.ApiTelegramException as e:
        print(f"[{now_str()}] Polling error: {e}", flush=True)
        print(f"[{now_str()}] TIP: Use Webhook (set USE_WEBHOOK=true + WEBHOOK_BASE_URL) or ensure single instance.", flush=True)

def main():

    # ---- Single-instance lock (same host) ----
    try:
        os.makedirs(os.path.dirname(RUN_LOCK_PATH), exist_ok=True)
        global _RUN_LOCK_FH
        if fcntl is None:
            raise RuntimeError("fcntl not available")
        _RUN_LOCK_FH = open(RUN_LOCK_PATH, "w")
        fcntl.flock(_RUN_LOCK_FH.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _RUN_LOCK_FH.write(str(os.getpid()))
        _RUN_LOCK_FH.flush()
        print(f"[{now_str()}] Lock acquired: {RUN_LOCK_PATH}", flush=True)
    except BlockingIOError:
        print(f"[{now_str()}] Another instance already holds lock {RUN_LOCK_PATH}. Exiting.", flush=True)
        raise SystemExit(1)
    except Exception as e:
        print(f"[{now_str()}] WARNING: lock setup failed: {e}. Continuing without single-instance guard.", flush=True)
    t = threading.Thread(target=poster_loop, daemon=True)
    t.start()
    if USE_WEBHOOK and WEBHOOK_BASE_URL:
        print(f"[{now_str()}] Mode: WEBHOOK", flush=True)
        run_webhook()
    else:
        if USE_WEBHOOK and not WEBHOOK_BASE_URL:
            print(f"[{now_str()}] USE_WEBHOOK set but WEBHOOK_BASE_URL missing → falling back to POLLING.", flush=True)
        else:
            print(f"[{now_str()}] Mode: POLLING", flush=True)
        run_polling()

if __name__ == "__main__":
    main()