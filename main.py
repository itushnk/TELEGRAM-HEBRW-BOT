# -*- coding: utf-8 -*-
import os, time, json, csv, re
from pathlib import Path
from urllib.parse import urlencode
import requests
import telebot
from telebot import types
from flask import Flask, request

# ========= ENV =========
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    print("[INIT][WARN] Missing TELEGRAM_BOT_TOKEN/BOT_TOKEN", flush=True)

WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL") or ""
WEBHOOK_BASE = os.getenv("TELEGRAM_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
RAILWAY_DOMAIN = os.getenv("RAILWAY_STATIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or ""
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") or ""
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET") or os.getenv("WEBHOOK_SECRET") or "secret"

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # optional, 0 = disabled
CURRENCY = os.getenv("CURRENCY", "₪")
TARGET_CHAT_ID_ENV = os.getenv("TARGET_CHAT_ID", "").strip()  # channel/group id (e.g., -1001234567890)
TARGET_CHAT_ID = int(TARGET_CHAT_ID_ENV) if TARGET_CHAT_ID_ENV and TARGET_CHAT_ID_ENV.lstrip("-").isdigit() else None

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PENDING_CSV = DATA_DIR / "pending.csv"
LOCK_PATH = DATA_DIR / "bot.lock"

# ========= BOT/WEB =========
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ========= HELPERS =========
def is_locked() -> bool:
    return LOCK_PATH.exists()

def set_locked(val: bool) -> None:
    try:
        if val:
            LOCK_PATH.write_text("off", encoding="utf-8")
        else:
            if LOCK_PATH.exists():
                LOCK_PATH.unlink()
    except Exception as e:
        print(f"[ERR] set_locked: {e}", flush=True)

def compute_webhook_url() -> str:
    if WEBHOOK_URL:
        return WEBHOOK_URL
    host = ""
    if WEBHOOK_BASE:
        host = WEBHOOK_BASE.strip().rstrip("/")
    elif RAILWAY_DOMAIN:
        host = RAILWAY_DOMAIN.strip().rstrip("/")
        if not host.startswith("http"):
            host = "https://" + host
    elif RENDER_URL:
        host = RENDER_URL.strip().rstrip("/")
    if host:
        return f"{host}/webhook/{WEBHOOK_SECRET}"
    return ""

def ensure_pending_csv():
    if not PENDING_CSV.exists():
        with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["item_id","title","url","price","image_url","ts"])

def append_to_pending(rows):
    ensure_pending_csv()
    with PENDING_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([r.get("id",""), r.get("title",""), r.get("url",""), r.get("price",""), r.get("image_url",""), int(time.time())])

def pending_count() -> int:
    if not PENDING_CSV.exists():
        return 0
    with PENDING_CSV.open("r", newline="", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)

def pop_next_pending():
    """Pop first item row from pending.csv (FIFO). Returns dict or None."""
    if not PENDING_CSV.exists():
        return None
    rows = []
    with PENDING_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if len(rows) <= 1:
        return None
    header = rows[0]
    item_row = rows[1]
    # rewrite minus the first data row
    with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows[2:]:
            w.writerow(r)
    keys = ["item_id","title","url","price","image_url","ts"]
    item = dict(zip(keys, item_row + [""]*(len(keys)-len(item_row))))
    return item

# Simple, resilient AE fallback via HTML
def ae_fallback_search(query: str, limit: int = 8, ship_to: str = "IL"):
    try:
        params = {"SearchText": query, "ShipCountry": ship_to, "SortType": "total_tranpro_desc", "g": "y"}
        url = "https://www.aliexpress.com/wholesale?" + urlencode(params, doseq=True)
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.aliexpress.com/"
        })
        cookies = {"xman_us_f": "x_lan=he_IL&x_locale=he_IL&region=IL&b_locale=he_IL"}
        r = sess.get(url, timeout=(10,20), cookies=cookies)
        r.raise_for_status()
        html = r.text
        items = []
        # Try embedded JSON blocks
        for pat in [r"window\.__AER_DATA__\s*=\s*(\{.*?\});", r"window\.runParams\s*=\s*(\{.*?\});"]:
            m = re.search(pat, html, re.S)
            if m:
                raw = m.group(1).strip().rstrip(";")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = None
                if data:
                    def walk(o):
                        if isinstance(o, dict):
                            pid = o.get("productId") or o.get("itemId") or o.get("id")
                            title = o.get("title") or o.get("productTitle")
                            url = o.get("productDetailUrl") or o.get("productUrl") or o.get("url")
                            img = o.get("productMainImageUrl") or o.get("imageUrl") or o.get("image")
                            price = o.get("appSalePrice") or o.get("salePrice") or o.get("price")
                            if pid and title and url:
                                items.append({"id": str(pid), "title": str(title), "url": str(url), "image_url": img or "", "price": price or ""})
                            for v in o.values():
                                walk(v)
                        elif isinstance(o, list):
                            for it in o: walk(it)
                    walk(data)
        if not items:
            # Anchor-based fallback
            for m in re.finditer(r'href="(https://www\.aliexpress\.com/item/[^"]+)"[^>]*>([^<]{10,120})</a>', html):
                url, title = m.group(1), m.group(2).strip()
                items.append({"id": str(abs(hash(url))), "title": title, "url": url, "image_url": "", "price": ""})

        # Unique & limit
        out, seen = [], set()
        for it in items:
            pid = it.get("id")
            if not pid or pid in seen: continue
            seen.add(pid); out.append(it)
            if len(out) >= limit: break
        return out
    except Exception as e:
        print(f"[AE][FALLBACK][ERR] {e}", flush=True)
        return []

# ======= UI =======
CATEGORIES = [
    ("gadgets", "📱 גאדג'טים"),
    ("fashion_men", "👔 אופנת גברים"),
    ("fashion_women", "👗 אופנת נשים"),
    ("home_tools", "🧰 כלי בית"),
    ("fitness", "🏃 ספורט וכושר"),
    ("beauty", "💄 ביוטי"),
]

def build_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🚀 פרסם עכשיו", callback_data="post_now"))
    kb.add(types.InlineKeyboardButton("🛒 שאיבה לפי קטגוריות", callback_data="cats"),
           types.InlineKeyboardButton("📜 מצב תור", callback_data="queue"))
    kb.add(types.InlineKeyboardButton("🔌 הפעלה", callback_data="on"),
           types.InlineKeyboardButton("🛑 כיבוי", callback_data="off"))
    return kb

def build_categories():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for cid, label in CATEGORIES:
        kb.add(types.InlineKeyboardButton(label, callback_data=f"ae_cat:{cid}"))
    kb.add(types.InlineKeyboardButton("⬅️ חזרה", callback_data="back"))
    return kb

def format_post(item: dict) -> str:
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    price = (item.get("price") or "").strip()
    # שמור על טקסט נקי בעברית + קריאה לפעולה
    parts = [f"🛍️ {title}"]
    if price:
        parts.append(f"💰 מחיר: {price} {CURRENCY}".strip())
    parts.append("👉 לחץ להזמנה בקישור מטה")
    return "\n\n".join(parts), url

def send_item(item: dict, chat_id: int):
    text, url = format_post(item)
    img = (item.get("image_url") or "").strip()
    kb = types.InlineKeyboardMarkup()
    if url:
        kb.add(types.InlineKeyboardButton("🛒 לקנייה עכשיו", url=url))
    if img:
        try:
            bot.send_photo(chat_id, img, caption=text, reply_markup=kb)
            return
        except Exception as e:
            print(f"[POST][WARN] send_photo failed: {e}", flush=True)
    bot.send_message(chat_id, text, reply_markup=kb)

# ======= Commands =======
@bot.message_handler(commands=["start","menu"])
def cmd_start(m):
    state = "כבוי" if is_locked() else "פעיל"
    bot.reply_to(m, f"שלום! מצב בוט: <b>{state}</b>", reply_markup=build_menu())

@bot.message_handler(commands=["on"])
def cmd_on(m):
    set_locked(False)
    bot.reply_to(m, "🔌 הבוט הופעל", reply_markup=build_menu())

@bot.message_handler(commands=["off"])
def cmd_off(m):
    set_locked(True)
    bot.reply_to(m, "🛑 הבוט כובה", reply_markup=build_menu())

@bot.message_handler(commands=["status"])
def cmd_status(m):
    bot.reply_to(m, f"📥 פריטים ממתינים: {pending_count()} | מצב בוט: {'כבוי' if is_locked() else 'פעיל'}")

@bot.message_handler(commands=["here"])
def cmd_here(m):
    bot.reply_to(m, f"chat_id: <code>{m.chat.id}</code>")

@bot.message_handler(commands=["post"])
def cmd_post(m):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "⛔ אין הרשאה לביצוע הפעולה הזו.")
        return
    if is_locked():
        bot.reply_to(m, "הבוט כבוי.")
        return
    item = pop_next_pending()
    if not item:
        bot.reply_to(m, "אין פריטים בתור.")
        return
    target = TARGET_CHAT_ID or m.chat.id
    send_item(item, target)
    bot.reply_to(m, f"✅ פורסם ל־{target}. נותרו בתור: {pending_count()}")

# ======= Callbacks =======
@bot.callback_query_handler(func=lambda c: True)
def on_cb(c):
    try:
        data = c.data or ""
        if data == "on":
            set_locked(False); bot.answer_callback_query(c.id, "🔌 הופעל"); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_menu()); return
        if data == "off":
            set_locked(True); bot.answer_callback_query(c.id, "🛑 כובה"); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_menu()); return
        if data == "queue":
            bot.answer_callback_query(c.id, f"בתור: {pending_count()}"); return
        if data == "cats":
            bot.answer_callback_query(c.id); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_categories()); return
        if data == "back":
            bot.answer_callback_query(c.id); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_menu()); return
        if data == "post_now":
            if is_locked():
                bot.answer_callback_query(c.id, "הבוט כבוי.", show_alert=True); return
            item = pop_next_pending()
            if not item:
                bot.answer_callback_query(c.id, "אין פריטים בתור", show_alert=True); return
            target = TARGET_CHAT_ID or c.message.chat.id
            send_item(item, target)
            bot.answer_callback_query(c.id, "✅ פורסם")
            bot.send_message(c.message.chat.id, f"✅ פורסם ל־{target}. נותרו בתור: {pending_count()}")
            return
        if data.startswith("ae_cat:"):
            if is_locked():
                bot.answer_callback_query(c.id, "הבוט כבוי.", show_alert=True); return
            cat = data.split(":",1)[1]
            bot.answer_callback_query(c.id, "⏳ שואב פריטים…")
            # map to simple query keywords
            qmap = {
                "gadgets":"gadgets",
                "fashion_men":"men compression shorts",
                "fashion_women":"women leggings",
                "home_tools":"home tools",
                "fitness":"fitness gear",
                "beauty":"beauty makeup"
            }
            query = qmap.get(cat, cat)
            items = ae_fallback_search(query, limit=8, ship_to="IL")
            if not items:
                bot.send_message(c.message.chat.id, "ℹ️ לא נמצאו פריטים כרגע, נסה שוב.")
            else:
                append_to_pending(items)
                bot.send_message(c.message.chat.id, f"✅ נוספו {len(items)} מוצרים. בתור: {pending_count()}")
            return
    except Exception as e:
        try:
            bot.answer_callback_query(c.id, f"שגיאה: {e}", show_alert=False)
        except Exception:
            pass

# ======= Webhook =======
@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        return "forbidden", 403
    if request.headers.get("content-type")=="application/json":
        upd = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([upd])
        return "OK", 200
    return "unsupported", 415

@app.route("/", methods=["GET"])
def root():
    return "OK", 200

def setup_webhook():
    if not BOT_TOKEN: 
        print("[WH][ERR] No token", flush=True); return
    # delete webhook first
    try:
        print("getWebhookInfo:", bot.get_webhook_info())
        bot.delete_webhook()
        print("deleteWebhook:", True)
    except Exception as e:
        print(f"[WH][WARN] deleteWebhook: {e}", flush=True)
    url = compute_webhook_url()
    if not url:
        print("[WH][WARN] No base URL (set TELEGRAM_WEBHOOK_URL or TELEGRAM_WEBHOOK_BASE/RAILWAY_STATIC_URL)", flush=True)
        return
    try:
        bot.set_webhook(url=url, allowed_updates=["message","callback_query"])
        print("setWebhook:", url)
    except Exception as e:
        print(f"[WH][ERR] set_webhook: {e}", flush=True)

def run_server():
    port = int(os.getenv("PORT", "8080"))
    host = "0.0.0.0"
    app.run(host=host, port=port, debug=False, threaded=True)

if __name__ == "__main__":
    # default ON unless BOT_START_LOCKED=1
    if os.getenv("BOT_START_LOCKED","0")!="1":
        set_locked(False)
        print("[BOOT] Cleared bot lock", flush=True)
    setup_webhook()
    run_server()
