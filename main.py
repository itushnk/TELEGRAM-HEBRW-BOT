# -*- coding: utf-8 -*-
"""
Telegram webhook bot with AliExpress affiliate-enforced links.
v7e: discovery fix — remove /af endpoints (404), add mobile search URLs,
     add he/aliexpress.us domains, and richer parsers (data-href, productId).
"""
import os, time, csv, random, re, threading
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

# Affiliate API creds
AE_APP_KEY = os.getenv("AE_API_APP_KEY", "").strip()
AE_APP_SECRET = os.getenv("AE_APP_SECRET", "").strip()
AE_TRACKING_ID = os.getenv("AE_TRACKING_ID", "").strip()
AE_AFF_SHORT_KEY = os.getenv("AE_AFF_SHORT_KEY","").strip()  # optional fallback

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
    if not PENDING_CSV.exists():
        return 0
    with PENDING_CSV.open("r", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)

def append_rows(rows):
    ensure_pending_csv()
    with PENDING_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([
                r.get("id",""),
                r.get("title",""),
                r.get("url",""),
                r.get("price",""),
                r.get("image_url",""),
                int(time.time()),
                "1" if r.get("aff_ok") else "0"
            ])

def pop_next_pending():
    if not PENDING_CSV.exists():
        return None
    with PENDING_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1:
        return None
    header, first, rest = rows[0], rows[1], rows[2:]
    with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rest:
            w.writerow(r)
    keys = ["item_id","title","url","price","image_url","ts","aff_ok"]
    if len(first) < len(keys):
        first = first + [""]*(len(keys)-len(first))
    return dict(zip(keys, first))

# ======= AliExpress: discovery (mobile + desktop + DDG) =======
_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]
def _sess():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(_UA_LIST),
        "Accept-Language":"en-US,en;q=0.9",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s

def _fetch_html(url, s, timeout=(7,10)):
    r = s.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text

# Accept any xx.aliexpress.(com|us|...)/item/....html and also protocol-relative links
_ITEM_RE = re.compile(r'(?:https?:)?//[a-z\-]*\.?aliexpress\.(?:com|us)/item/[^\s"<>]*?(\d{8,})\.html[^\s"<>]*', re.I)

def _parse_item_links(html):
    out, seen = [], set()
    # Normal absolute or protocol-relative item URLs
    for m in _ITEM_RE.finditer(html):
        url, pid = m.group(0), m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if url in seen: 
            continue
        seen.add(url)
        out.append({"id": pid, "url": url})
    # Mobile relative hrefs
    for m in re.finditer(r'href=["\'](/item/(\d{8,})\.html)["\']', html):
        pid = m.group(2); url = f"https://m.aliexpress.com/item/{pid}.html"
        if url in seen: continue
        seen.add(url); out.append({"id": pid, "url": url})
    # data-href attributes commonly used in grid items
    for m in re.finditer(r'data-href=["\'](//[^"\']*?/item/(\d{8,})\.html[^"\']*)["\']', html):
        url, pid = m.group(1), m.group(2)
        if url.startswith("//"): url = "https:" + url
        if url in seen: continue
        seen.add(url); out.append({"id": pid, "url": url})
    # productId JSON — reconstruct URL
    for m in re.finditer(r'["\']productId["\']\s*:\s*["\'](\d{8,})["\']', html):
        pid = m.group(1); url = f"https://www.aliexpress.com/item/{pid}.html"
        if url in seen: continue
        seen.add(url); out.append({"id": pid, "url": url})
    return out

def _scrape_meta(url, s):
    try:
        h = _fetch_html(url, s, timeout=(5,8))
        title = None
        m = re.search(r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', h)
        if m: title = m.group(1)
        m = re.search(r'<title>\s*([^<]+)\s*</title>', h)
        if (not title) and m: title = m.group(1)
        img = None
        m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', h)
        if m: img = m.group(1)
        pid_match = re.search(r'(\d{8,})\.html', url); pid = pid_match.group(1) if pid_match else ""
        return {"id": pid, "title": (title or "AliExpress product").strip(), "url": url, "image_url": img or "", "price": ""}
    except Exception as e:
        print(f"[META][WARN] {url} -> {e}", flush=True)
        return None

def discover(query, limit=12):
    s = _sess()
    q = quote_plus(query)
    urls = [
        f"https://m.aliexpress.com/search.htm?keywords={q}&g=y&SortType=total_tranpro_desc",
        f"https://m.aliexpress.com/search?keywords={q}&g=y&SortType=total_tranpro_desc",
        f"https://m.aliexpress.com/wholesale/{q}.html?g=y&SortType=total_tranpro_desc",
        f"https://he.aliexpress.com/w/wholesale-{q}.html?g=y&SortType=total_tranpro_desc",
        f"https://www.aliexpress.us/w/wholesale-{q}.html?g=y&SortType=total_tranpro_desc",
        f"https://www.aliexpress.com/w/wholesale-{q}.html?g=y&SortType=total_tranpro_desc",
        f"https://www.aliexpress.com/wholesale?SearchText={q}&g=y&SortType=total_tranpro_desc",
    ] + [  # search engine HTML fallbacks
        f"https://duckduckgo.com/html/?q={quote_plus('site:aliexpress.com/item ' + query)}&s={off}" for off in [0,30,60,90]
    ]
    found = []
    for u in urls:
        try:
            html = _fetch_html(u, s)
            links = _parse_item_links(html)
            found += links
            if links:
                print(f"[DISCOVER][HIT] {u} -> {len(links)} links", flush=True)
            else:
                print(f"[DISCOVER][MISS] {u}", flush=True)
            if len(found) >= limit*2:
                break
        except Exception as e:
            print(f"[DISCOVER][WARN] {u} -> {e}", flush=True)
    uniq, seen = [], set()
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
    if not (AE_APP_KEY and AE_APP_SECRET and AE_TRACKING_ID):
        return None
    try:
        from aliexpress_api import AliexpressApi, models
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
        print("[AEAPI] Ready", flush=True)
        return make
    except Exception as e:
        print(f"[AEAPI][WARN] {e}", flush=True)
        return None

def _s_click_fallback(url: str):
    if not AE_AFF_SHORT_KEY:
        return None
    base = "https://s.click.aliexpress.com/deep_link.htm"
    target = url
    if "?" in target:
        target += f"&shipCountry={quote_plus(SHIP_TO)}"
    else:
        target += f"?shipCountry={quote_plus(SHIP_TO)}"
    return f"{base}?aff_short_key={quote_plus(AE_AFF_SHORT_KEY)}&dl_target_url={quote_plus(target)}"

AFF_MAKER = _aliexpress_api_client()

def to_affiliate(url: str):
    u = (url or "").strip()
    if not u:
        return u, False
    if AFF_MAKER:
        link = AFF_MAKER(u)
        if link:
            print(f"[AFF] API OK -> {link[:80]}...", flush=True)
            return link, True
        else:
            print("[AFF] API FAIL", flush=True)
    link = _s_click_fallback(u)
    if link:
        print(f"[AFF] s.click OK -> {link[:80]}...", flush=True)
        return link, True
    print("[AFF] NO-AFF", flush=True)
    return u, False

# ======= UI =======
CATS = [
    ("gadgets","📱 גאדג'טים",["gadgets electronic gadget", "smart gadget", "electronics accessories", "usb gadget"]),
    ("fashion_men","👔 אופנת גברים",["men compression shorts running gym", "training tights men", "sportswear men"]),
    ("fashion_women","👗 אופנת נשים",["women leggings yoga gym", "women sport leggings", "yoga pants women"]),
    ("home_tools","🧰 כלי בית",["home tools hardware screwdriver drill set", "household tools set", "drill screwdriver set"]),
    ("fitness","🏃 ספורט וכושר",["fitness gear resistance band", "gym accessories", "sports equipment"]),
    ("beauty","💄 ביוטי",["beauty makeup cosmetic", "makeup brush set", "lipstick set"]),
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
    if price:
        parts.insert(1, f"💰 מחיר: {price} {CURRENCY}".strip())
    return "\n\n".join(parts)

def send_item(item, chat_id):
    text = format_post(item)
    url = (item.get("url") or "").strip()
    img = (item.get("image_url") or "").strip()
    btn_txt = "🛒 לקנייה עכשיו" + (" ✅ אפילייט" if item.get("aff_ok") in ("1",1,True) else "")
    kb = types.InlineKeyboardMarkup()
    if url:
        kb.add(types.InlineKeyboardButton(btn_txt, url=url))
    try:
        if img:
            bot.send_photo(chat_id, img, caption=text, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
    except Exception:
        bot.send_message(chat_id, text + "\n\n(תמונה לא נשלחה)", reply_markup=kb)

# ======= Commands =======
@bot.message_handler(commands=["start","menu"])
def cmd_start(m):
    bot.reply_to(m, f"שלום! מצב: {'כבוי' if is_locked() else 'פעיל'}", reply_markup=build_menu())

@bot.message_handler(commands=["on"])
def cmd_on(m):
    set_locked(False)
    bot.reply_to(m, "🔌 הופעל", reply_markup=build_menu())

@bot.message_handler(commands=["off"])
def cmd_off(m):
    set_locked(True)
    bot.reply_to(m, "🛑 כובה", reply_markup=build_menu())

@bot.message_handler(commands=["status"])
def cmd_status(m):
    bot.reply_to(m, f"📥 בתור: {pending_count()} | מצב: {'כבוי' if is_locked() else 'פעיל'}")

@bot.message_handler(commands=["post"])
def cmd_post(m):
    if is_locked():
        return bot.reply_to(m,"הבוט כבוי.")
    item = pop_next_pending()
    if not item:
        return bot.reply_to(m, "אין פריטים בתור.")
    target = TARGET_CHAT_ID or m.chat.id
    send_item(item, target)
    bot.reply_to(m, f"✅ פורסם. נותרו: {pending_count()}")

@bot.message_handler(commands=["aff_test"])
def cmd_aff_test(m):
    parts = (m.text or "").split(None,1)
    if len(parts) < 2:
        return bot.reply_to(m,"שימוש: /aff_test <url>")
    u = parts[1].strip()
    aff, ok = to_affiliate(u)
    bot.reply_to(m, f"{'✅' if ok else '⚠️ NO-AFF'}\n{aff}")

# ======= Callbacks =======
def _discover_many(queries, limit_each=6):
    res = []
    for q in queries:
        items = discover(q, limit=limit_each)
        res += items
        if len(res) >= 12:
            break
    return res[:12]

@bot.callback_query_handler(func=lambda c: True)
def on_cb(c):
    try:
        data = c.data or ""
        if data == "cats":
            bot.answer_callback_query(c.id)
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_cats())
            return
        if data == "back":
            bot.answer_callback_query(c.id)
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=build_menu())
            return
        if data == "queue":
            bot.answer_callback_query(c.id, f"בתור: {pending_count()}")
            return
        if data == "on":
            set_locked(False)
            bot.answer_callback_query(c.id,"🔌 הופעל")
            return
        if data == "off":
            set_locked(True)
            bot.answer_callback_query(c.id,"🛑 כובה")
            return
        if data == "post_now":
            if is_locked():
                return bot.answer_callback_query(c.id,"כבוי.", show_alert=True)
            item = pop_next_pending()
            if not item:
                return bot.answer_callback_query(c.id,"אין פריטים בתור", show_alert=True)
            send_item(item, TARGET_CHAT_ID or c.message.chat.id)
            bot.answer_callback_query(c.id,"✅ פורסם")
            return
        if data.startswith("ae:"):
            if is_locked():
                return bot.answer_callback_query(c.id,"כבוי.", show_alert=True)
            cid = data.split(":",1)[1]
            queries = next((qs for k,_,qs in CATS if k==cid), [cid])
            try:
                bot.answer_callback_query(c.id, "⏳ שואב פריטים…")
            except Exception:
                pass
            def work():
                try:
                    items = _discover_many(queries, limit_each=6)
                    affed = []
                    for it in items:
                        url_aff, ok = to_affiliate(it["url"])
                        it["url"] = url_aff
                        it["aff_ok"] = ok
                        if ok or not REQUIRE_AFFILIATE:
                            affed.append(it)
                    if not affed:
                        bot.send_message(c.message.chat.id, "ℹ️ לא נמצאו פריטים אפילייט כרגע, נסה שוב.")
                        return
                    append_rows(affed)
                    bot.send_message(c.message.chat.id, f"✅ נוספו {len(affed)} פריטים אפילייט. בתור: {pending_count()}")
                except Exception as e:
                    bot.send_message(c.message.chat.id, f"שגיאה בשאיבה: {e}")
            threading.Thread(target=work, daemon=True).start()
            return
    except Exception as e:
        try:
            bot.answer_callback_query(c.id, f"שגיאה: {e}")
        except Exception:
            pass

# ======= Webhook endpoints =======
@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        return "forbidden", 403
    payload = request.get_data(as_text=True) or ""
    def worker(data_text: str):
        try:
            upd = telebot.types.Update.de_json(data_text)
            bot.process_new_updates([upd])
        except Exception as e:
            print(f"[WH][ERR] {e}", flush=True)
    threading.Thread(target=worker, args=(payload,), daemon=True).start()
    return "OK", 200

@app.route("/_health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/", methods=["GET"])
def root():
    return "OK", 200

def setup_webhook():
    if not USE_WEBHOOK:
        print("[WH] USE_WEBHOOK=0 -> webhook disabled")
        return
    try:
        info = bot.get_webhook_info()
        print("getWebhookInfo:", info)
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
        print(f"[WH] set_webhook: {e}")

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
