# -*- coding: utf-8 -*-
"""
Telegram bot (webhook) that queues and posts AliExpress products.
This build *enforces affiliate-only links* when AE_API_APP_KEY/AE_APP_SECRET/AE_TRACKING_ID are set.
If conversion fails and REQUIRE_AFFILIATE=1, the item is skipped.
"""
import os, time, csv, random, re
from pathlib import Path
from urllib.parse import quote_plus
import requests
import telebot
from telebot import types
from flask import Flask, request

# ======= ENV / CONFIG =======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
TELEGRAM_WEBHOOK_BASE = (os.getenv("TELEGRAM_WEBHOOK_BASE") or "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET") or os.getenv("WEBHOOK_SECRET") or "secret"
USE_WEBHOOK = os.getenv("USE_WEBHOOK","1") == "1"
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "").strip()  # e.g. -100...
TARGET_CHAT_ID = int(PUBLIC_CHANNEL) if PUBLIC_CHANNEL.lstrip("-").isdigit() else None
CURRENCY = os.getenv("AE_TARGET_CURRENCY", "₪")
SHIP_TO = os.getenv("AE_SHIP_TO_COUNTRY", "IL")
LANG = (os.getenv("AE_TARGET_LANGUAGE", "EN") or "EN").upper()
REQUIRE_AFFILIATE = os.getenv("REQUIRE_AFFILIATE","1") == "1"

# Affiliate API creds (Open Platform | Portals)
AE_APP_KEY = os.getenv("AE_API_APP_KEY", "").strip()
AE_APP_SECRET = os.getenv("AE_APP_SECRET", "").strip()
AE_TRACKING_ID = os.getenv("AE_TRACKING_ID", "").strip()  # sometimes called 'pid' or 'trackingId'
AE_GATEWAY_LIST = [u.strip() for u in (os.getenv("AE_GATEWAY_LIST","").split(",") if os.getenv("AE_GATEWAY_LIST") else [])]
AE_AFF_SHORT_KEY = os.getenv("AE_AFF_SHORT_KEY","").strip()  # optional deep_link fallback

# Timing
POST_DELAY_SECONDS = int(os.getenv("POST_DELAY_SECONDS","12") or "12")

# Storage
DATA_DIR = Path(os.getenv("DATA_DIR","data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PENDING_CSV = DATA_DIR / "pending.csv"
LOCK_PATH = DATA_DIR / "bot.lock"

# ======= Bot / Web =======
if not BOT_TOKEN:
    print("[BOOT][ERR] Missing bot token", flush=True)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ======= Helpers (lock & queue) =======
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
        print(f"[WARN] set_locked: {e}", flush=True)

def ensure_pending_csv():
    if not PENDING_CSV.exists():
        with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["item_id","title","url","price","image_url","ts","aff_ok"])

def pending_count():
    if not PENDING_CSV.exists(): return 0
    with PENDING_CSV.open("r", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f)-1)

def append_rows(rows):
    ensure_pending_csv()
    with PENDING_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([r.get("id",""), r.get("title",""), r.get("url",""), r.get("price",""), r.get("image_url",""), int(time.time()), "1" if r.get("aff_ok") else "0"])

def pop_next_pending():
    if not PENDING_CSV.exists(): return None
    with PENDING_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1: return None
    header, first, rest = rows[0], rows[1], rows[2:]
    with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header); [w.writerow(r) for r in rest]
    keys = ["item_id","title","url","price","image_url","ts","aff_ok"]
    return dict(zip(keys, first + [""]*(len(keys)-len(first))))

# ======= AliExpress: scraping fallback (for discovery only) =======
_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]
def _sess():
    s = requests.Session()
    s.headers.update({"User-Agent": random.choice(_UA_LIST), "Accept-Language":"en-US,en;q=0.9"})
    return s

def _fetch_html(url, s, timeout=(10,20)):
    r = s.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text

def _parse_item_links(html):
    out, seen = [], set()
    for m in re.finditer(r'https?://(?:www|m)\.aliexpress\.com/item/[^\s"<>]*?(\d{8,})\.html[^\s"<>]*', html):
        url, pid = m.group(0), m.group(1)
        if url in seen: continue
        seen.add(url)
        out.append({"id": pid, "url": url})
    return out

def _scrape_meta(url, s):
    try:
        h = _fetch_html(url, s, timeout=(8,15))
        title = None
        m = re.search(r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', h)
        if m: title = m.group(1)
        m = re.search(r'<title>\s*([^<]+)\s*</title>', h);  title = title or (m.group(1) if m else None)
        img = None
        m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', h);  img = m.group(1) if m else None
        return {"id": re.search(r'(\d{8,})\.html', url).group(1), "title": title or "AliExpress product", "url": url, "image_url": img or "", "price": ""}
    except Exception as e:
        print(f"[META][WARN] {url} -> {e}", flush=True); return None

def discover(query, limit=10):
    s = _sess()
    urls = [
        f"https://www.aliexpress.com/af/{quote_plus(query)}.html?g=y&SortType=total_tranpro_desc&page={p}" for p in [1,2,3]
    ] + [
        f"https://duckduckgo.com/html/?q={quote_plus('site:aliexpress.com/item ' + query)}&s={off}" for off in [0,30,60]
    ]
    found = []
    for u in urls:
        try:
            html = _fetch_html(u, s)
            found += _parse_item_links(html)
            if len(found) >= limit*2: break
        except Exception as e:
            print(f"[DISCOVER][WARN] {u} -> {e}", flush=True)
    uniq = []
    seen = set()
    for it in found:
        if it["url"] in seen: continue
        seen.add(it["url"]); uniq.append(it["url"])
        if len(uniq) >= limit: break
    items = []
    for u in uniq:
        m = _scrape_meta(u, s)
        if m: items.append(m)
    print(f"[DISCOVER] '{query}' -> {len(items)} items", flush=True)
    return items

# ======= Affiliate wrapping =======
def _aliexpress_api_client():
    """
    Returns callable: get_affiliate_link(url) -> str|None using python-aliexpress-api if creds exist.
    """
    if not (AE_APP_KEY and AE_APP_SECRET and AE_TRACKING_ID):
        return None
    try:
        from aliexpress_api import AliexpressApi, models
        # Map language/currency safely
        lang_map = {k:getattr(models.Language,k) for k in dir(models.Language) if k.isupper()}
        cur_map  = {k:getattr(models.Currency,k) for k in dir(models.Currency) if k.isupper()}
        lang = lang_map.get(LANG, models.Language.EN)
        cur  = cur_map.get(CURRENCY.upper(), models.Currency.USD)
        api = AliexpressApi(AE_APP_KEY, AE_APP_SECRET, lang, cur, AE_TRACKING_ID, session=None)
        def make(url: str):
            try:
                links = api.get_affiliate_links(url)
                if links and getattr(links[0], "promotion_link", None):
                    return links[0].promotion_link
            except Exception as e:
                print(f"[AEAPI][ERR] {e}", flush=True)
            return None
        return make
    except Exception as e:
        print(f"[AEAPI][WARN] python-aliexpress-api not available or failed: {e}", flush=True)
        return None

def _s_click_fallback(url: str):
    # Requires aff_short_key from Portals (optional)
    if not AE_AFF_SHORT_KEY: 
        return None
    base = "https://s.click.aliexpress.com/deep_link.htm"
    return f"{base}?aff_short_key={quote_plus(AE_AFF_SHORT_KEY)}&dl_target_url={quote_plus(url)}"

AFF_MAKER = _aliexpress_api_client()

def to_affiliate(url: str):
    """
    Return (url, aff_ok). Obeys REQUIRE_AFFILIATE:
    - Try official API (tracking id)
    - Fallback to s.click deep_link if AFF short key exists
    - If both fail: return original, aff_ok=False (and may be skipped later)
    """
    u = (url or "").strip()
    if not u: return u, False
    # 1) official API
    if AFF_MAKER:
        link = AFF_MAKER(u)
        if link: return link, True
    # 2) s.click fallback
    link = _s_click_fallback(u)
    if link: return link, True
    # 3) nothing
    return u, False

# ======= UI =======
CATS = [
    ("gadgets","📱 גאדג'טים","gadgets electronic gadget"),
    ("fashion_men","👔 אופנת גברים","men compression shorts running gym"),
    ("fashion_women","👗 אופנת נשים","women leggings yoga gym"),
    ("home_tools","🧰 כלי בית","home tools hardware screwdriver drill set"),
    ("fitness","🏃 ספורט וכושר","fitness gear resistance band"),
    ("beauty","💄 ביוטי","beauty makeup cosmetic"),
]

def build_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🛒 שאיבה לפי קטגוריות", callback_data="cats"))
    kb.add(types.InlineKeyboardButton("🚀 פרסם עכשיו", callback_data="post_now"),
           types.InlineKeyboardButton("📥 מצב תור", callback_data="queue"))
    kb.add(types.InlineKeyboardButton("🔌 הפעלה", callback_data="on"),
           types.InlineKeyboardButton("🛑 כיבוי", callback_data="off"))
    return kb

def build_cats():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for cid, label, _ in CATS:
        kb.add(types.InlineKeyboardButton(label, callback_data=f"ae:{cid}"))
    kb.add(types.InlineKeyboardButton("⬅️ חזרה", callback_data="back"))
    return kb

def format_post(item):
    title = (item.get("title") or "").strip()
    price = (item.get("price") or "").strip()
    parts = [f"🛍️ {title}", "👉 לחץ להזמנה בקישור מטה"]
    if price: parts.insert(1, f"💰 מחיר: {price} {CURRENCY}".strip())
    return "\n\n".join(parts)

def send_item(item, chat_id):
    text = format_post(item)
    url = (item.get("url") or "").strip()
    img = (item.get("image_url") or "").strip()
    # Button explicitly marked as affiliate (minimal change to structure)
    btn_txt = "🛒 לקנייה עכשיו" + (" ✅ אפילייט" if item.get("aff_ok") in ("1",1,True) else "")
    kb = types.InlineKeyboardMarkup()
    if url: kb.add(types.InlineKeyboardButton(btn_txt, url=url))
    try:
        if img:
            bot.send_photo(chat_id, img, caption=text, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as e:
        bot.send_message(chat_id, text + "\n\n(תמונה לא נשלחה)", reply_markup=kb)

# ======= Commands =======
@bot.message_handler(commands=["start","menu"])
def cmd_start(m):
    bot.reply_to(m, f"שלום! מצב: {'כבוי' if is_locked() else 'פעיל'}", reply_markup=build_menu())

@bot.message_handler(commands=["on"])
def cmd_on(m):
    set_locked(False); bot.reply_to(m, "🔌 הופעל", reply_markup=build_menu())

@bot.message_handler(commands=["off"])
def cmd_off(m):
    set_locked(True); bot.reply_to(m, "🛑 כובה", reply_markup=build_menu())

@bot.message_handler(commands=["status"])
def cmd_status(m):
    bot.reply_to(m, f"📥 בתור: {pending_count()} | מצב: {'כבוי' if is_locked() else 'פעיל'}")

@bot.message_handler(commands=["post"])
def cmd_post(m):
    if is_locked(): return bot.reply_to(m,"הבוט כבוי.")
    item = pop_next_pending()
    if not item: return bot.reply_to(m, "אין פריטים בתור.")
    target = TARGET_CHAT_ID or m.chat.id
    send_item(item, target)
    bot.reply_to(m, f"✅ פורסם. נותרו: {pending_count()}")

@bot.message_handler(commands=["aff_test"])
def cmd_aff_test(m):
    parts = (m.text or "").split(None,1)
    if len(parts)<2: return bot.reply_to(m,"שימוש: /aff_test <url>")
    u = parts[1].strip()
    aff, ok = to_affiliate(u)
    bot.reply_to(m, f"{'✅' if ok else '⚠️ NO-AFF'}\n{aff}")

# ======= Callbacks =======
@bot.callback_query_handler(func=lambda c: True)
def on_cb(c):
    try:
        data = c.data or ""
        if data == "cats":
            bot.answer_callback_query(c.id); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_cats()); return
        if data == "back":
            bot.answer_callback_query(c.id); bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_menu()); return
        if data == "queue":
            bot.answer_callback_query(c.id, f"בתור: {pending_count()}"); return
        if data == "on":
            set_locked(False); bot.answer_callback_query(c.id,"🔌 הופעל"); return
        if data == "off":
            set_locked(True); bot.answer_callback_query(c.id,"🛑 כובה"); return
        if data == "post_now":
            if is_locked(): return bot.answer_callback_query(c.id,"כבוי.", show_alert=True)
            item = pop_next_pending()
            if not item: return bot.answer_callback_query(c.id,"אין פריטים בתור", show_alert=True)
            send_item(item, TARGET_CHAT_ID or c.message.chat.id)
            bot.answer_callback_query(c.id,"✅ פורסם"); return
        if data.startswith("ae:"):
            if is_locked(): return bot.answer_callback_query(c.id,"כבוי.", show_alert=True)
            cid = data.split(":",1)[1]
            query = next((q for k,_,q in CATS if k==cid), cid)
            items = discover(query, limit=12)
            # Convert all to affiliate; enforce REQUIRE_AFFILIATE
            affed = []
            for it in items:
                url_aff, ok = to_affiliate(it["url"])
                it["url"] = url_aff
                it["aff_ok"] = ok
                if ok or not REQUIRE_AFFILIATE:
                    affed.append(it)
            if not affed:
                bot.answer_callback_query(c.id, "לא נמצאו פריטים אפילייט, נסה שוב", show_alert=True); return
            append_rows(affed)
            bot.answer_callback_query(c.id, f"נוספו {len(affed)} (אפילייט)", show_alert=False)
            bot.send_message(c.message.chat.id, f"✅ נוספו {len(affed)} פריטים אפילייט. בתור: {pending_count()}")
    except Exception as e:
        try: bot.answer_callback_query(c.id, f"שגיאה: {e}")
        except Exception: pass

# ======= Webhook endpoints =======
@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if secret != WEBHOOK_SECRET: return "forbidden", 403
    upd = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([upd])
    return "OK", 200

@app.route("/", methods=["GET"])
def root(): return "OK", 200

def setup_webhook():
    if not USE_WEBHOOK: 
        print("[WH] USE_WEBHOOK=0 -> webhook disabled"); return
    try:
        print("getWebhookInfo:", bot.get_webhook_info())
        bot.delete_webhook()
    except Exception as e:
        print(f"[WH] delete_webhook: {e}")
    if not TELEGRAM_WEBHOOK_BASE:
        print("[WH][WARN] TELEGRAM_WEBHOOK_BASE missing")
        return
    url = f"{TELEGRAM_WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
    try:
        bot.set_webhook(url, allowed_updates=["message","callback_query"])
        print("setWebhook:", url)
    except Exception as e:
        print(f"[WH][ERR] set_webhook: {e}")

def run_server():
    from waitress import serve
    port = int(os.getenv("PORT","8080"))
    print(f"[BOOT] Serving on 0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    if os.getenv("CLEAR_BOT_LOCK_ON_START","1") == "1":
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()
        print("[BOOT] Cleared bot lock")
    if os.getenv("BOT_ALWAYS_ON","1") == "1":
        set_locked(False)
    setup_webhook()
    run_server()
