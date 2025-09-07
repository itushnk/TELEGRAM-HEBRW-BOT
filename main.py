# -*- coding: utf-8 -*-
import os, time, json, csv, re, random
from pathlib import Path
from urllib.parse import urlencode, quote_plus
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

# ================= AliExpress Fallbacks ==================
_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

def _sess():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(_UA_LIST),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Referer": "https://www.aliexpress.com/"
    })
    return s

def _fetch_html(url, sess, timeout=(10,20)):
    r = sess.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    if "AliExpress.com - Maintaining" in r.text[:200] or "center/maintain" in r.url:
        raise RuntimeError("maintenance page")
    return r.text

def _parse_item_links_from_html(html):
    items = []
    for m in re.finditer(r'https?://(?:www|m)\.aliexpress\.com/item/[^\s"<>]*?(\d{8,})\.html[^\s"<>]*', html):
        url = m.group(0)
        pid = m.group(1)
        items.append({"id": pid, "url": url})
    # dedup
    out, seen = [], set()
    for it in items:
        if it["id"] in seen: continue
        seen.add(it["id"]); out.append(it)
    return out

def _scrape_item_meta(url, sess):
    try:
        h = _fetch_html(url, sess, timeout=(10,20))
        # title
        title = None
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{5,200})["\']', h, re.I)
        if m: title = m.group(1)
        if not title:
            m = re.search(r'<title>\s*([^<]{5,200})\s*</title>', h, re.I)
            if m: title = m.group(1)
        # image
        img = None
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', h, re.I)
        if m: img = m.group(1)
        return {
            "id": re.search(r'(\d{8,})\.html', url).group(1) if re.search(r'(\d{8,})\.html', url) else str(abs(hash(url))),
            "title": title or "AliExpress product",
            "url": url,
            "image_url": img or "",
            "price": ""
        }
    except Exception as e:
        print(f"[AE][META][WARN] {url} -> {e}", flush=True)
        return None

def ae_fallback_search(query: str, limit: int = 8, ship_to: str = "IL"):
    q = quote_plus(query)
    urls = [
        f"https://www.aliexpress.com/af/{q}.html?SearchText={q}&g=y&SortType=total_tranpro_desc",
        f"https://www.aliexpress.com/wholesale?SearchText={q}&g=y&SortType=total_tranpro_desc",
        f"https://m.aliexpress.com/wholesale/{q}.html?sortType=total_tranpro_desc",
    ]
    sess = _sess()
    total = []
    # pass 1: try aliexpress search pages
    for url in urls:
        try:
            html = _fetch_html(url, sess)
            links = _parse_item_links_from_html(html)
            total.extend(links)
        except Exception as e:
            print(f"[AE][FALLBACK][WARN] {url} -> {e}", flush=True)
    # if empty, pass 2: duckduckgo site search
    if not total:
        try:
            ddg_q = quote_plus(f"site:aliexpress.com/item {query}")
            ddg_url = f"https://duckduckgo.com/html/?q={ddg_q}"
            html = _fetch_html(ddg_url, sess, timeout=(10,20))
            links = _parse_item_links_from_html(html)
            total.extend(links)
            if not links:
                # try m.aliexpress on duckduckgo too
                ddg_q2 = quote_plus(f"site:m.aliexpress.com/item {query}")
                ddg_url2 = f"https://duckduckgo.com/html/?q={ddg_q2}"
                html2 = _fetch_html(ddg_url2, sess, timeout=(10,20))
                links2 = _parse_item_links_from_html(html2)
                total.extend(links2)
        except Exception as e:
            print(f"[AE][DDG][WARN] ddg -> {e}", flush=True)

    # build items with meta (title/image)
    uniq_urls, seen = [], set()
    for it in total:
        u = it["url"]
        if u in seen: continue
        seen.add(u); uniq_urls.append(u)
        if len(uniq_urls) >= limit: break

    items = []
    for u in uniq_urls:
        meta = _scrape_item_meta(u, sess)
        if meta: items.append(meta)

    print(f"[AE][FALLBACK] query='{query}' -> {len(items)} items", flush=True)
    return items

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

# ---- Admin utilities ----
@bot.message_handler(commands=["dump_pending"])
def cmd_dump_pending(m):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "⛔ אין הרשאה לפקודה זו.")
        return
    ensure_pending_csv()
    try:
        if not PENDING_CSV.exists() or pending_count() == 0:
            bot.reply_to(m, "אין פריטים בתור.")
            return
        with PENDING_CSV.open("rb") as f:
            bot.send_document(m.chat.id, f, visible_file_name="pending.csv", caption=f"📥 תור נוכחי: {pending_count()} פריטים")
    except Exception as e:
        bot.reply_to(m, f"שגיאה בשליחה: {e}")

@bot.message_handler(commands=["clear_pending"])
def cmd_clear_pending(m):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "⛔ אין הרשאה לפקודה זו.")
        return
    if not PENDING_CSV.exists():
        bot.reply_to(m, "לא קיים תור למחיקה.")
        return
    count_before = pending_count()
    ensure_pending_csv()
    try:
        with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["item_id","title","url","price","image_url","ts"])
        bot.reply_to(m, f"🧹 נמחקו {count_before} פריטים מהתור.")
    except Exception as e:
        bot.reply_to(m, f"שגיאה במחיקה: {e}")

# ---- NEW: test AE ----
@bot.message_handler(commands=["test_ae"])
def cmd_test_ae(m):
    parts = (m.text or "").split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "שימוש: /test_ae <שאילתא>, לדוגמה: /test_ae gadgets")
        return
    query = parts[1].strip()
    items = ae_fallback_search(query, limit=10, ship_to="IL")
    if not items:
        bot.reply_to(m, f"לא נמצאו פריטים עבור: {query}")
        return
    preview = "\n".join([f"- {it['title'][:60]}…" for it in items[:5]])
    bot.reply_to(m, f"נמצאו {len(items)} פריטים עבור: {query}\n{preview}")

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
