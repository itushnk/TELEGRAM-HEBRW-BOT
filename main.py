# -*- coding: utf-8 -*-
"""
AliExpress affiliate Telegram bot (v8):
• Discovery by many categories/sub-niches
• Filters: ship_to_country=IL, orders>=1000, sort by highest orders
• Coupon injection when available
• Post builder to your template (as per the sample you sent)
Requires:
  - python-aliexpress-api (wrapper)
  - pyTelegramBotAPI + Flask or Polling (webhook optional)
Environment:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL (e.g. -1001234567890 or @yourchannel)
  AE_KEY, AE_SECRET, AE_TRACKING_ID
  TARGET_CURRENCY=ILS (default), TARGET_LANGUAGE=HE (default)
  IL_MIN_ORDERS=1000 (default)
"""
import os, time, json, math, random, threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from flask import Flask, request
import telebot
from telebot import types

# ===== Env =====
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN","")
CHANNEL = os.getenv("TELEGRAM_CHANNEL","")  # @name or chat id
AE_KEY = os.getenv("AE_KEY","")
AE_SECRET = os.getenv("AE_SECRET","")
AE_TRACKING_ID = os.getenv("AE_TRACKING_ID","")
TARGET_CCY = os.getenv("TARGET_CURRENCY","ILS")
TARGET_LANG = os.getenv("TARGET_LANGUAGE","HE")
IL_MIN_ORDERS = int(os.getenv("IL_MIN_ORDERS","1000"))
CHANNEL_JOIN_URL = os.getenv("CHANNEL_JOIN_URL", "https://t.me/+LlMY8B9soOdhNmZk")

USE_WEBHOOK = os.getenv("USE_WEBHOOK","1") == "1"
PORT = int(os.getenv("PORT","8080"))
HOST = os.getenv("HOST","0.0.0.0")
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE_URL","")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","/webhook/secret")

# Normalize webhook path (allow any string, ensure leading slash)
from urllib.parse import quote

def _normalize_route(p: str) -> str:
    if not p:
        return "/webhook/secret"
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    return p

WEBHOOK_ROUTE = _normalize_route(WEBHOOK_SECRET)
# Encoded version for URL composing (Telegram requires ASCII URL)
WEBHOOK_ROUTE_ENC = "/" + "/".join(quote(seg, safe="") for seg in WEBHOOK_ROUTE.strip("/").split("/"))


BASE_DIR = Path(os.getenv("BOT_DATA_DIR","./data"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
CATEGORIES_PATH = Path(os.getenv("CATEGORIES_PATH", "categories.json"))

# ===== Telegram =====
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML") if BOT_TOKEN else None
app = Flask(__name__)

# ===== AliExpress wrapper =====
aliexpress = None
try:
    from aliexpress_api import AliexpressApi, models
    aliexpress = AliexpressApi(AE_KEY, AE_SECRET, getattr(models.Language, TARGET_LANG, models.Language.EN),
                               getattr(models.Currency, TARGET_CCY, models.Currency.USD),
                               AE_TRACKING_ID)
except Exception as e:
    print(f"[AE] Wrapper init failed: {e}")

# ===== Helpers =====
def fmt_price(v: str|float|int) -> str:
    try:
        x = float(str(v).replace(',',''))
        return f"{x:.2f} ש\"ח"
    except Exception:
        return f"{v} ש\"ח"

def pct(a: float, b: float) -> Optional[int]:
    try:
        if b and b>0:
            return int(round(100*(1 - a/b)))
    except Exception:
        return None

def star_to_pct(star: Optional[float]) -> Optional[float]:
    if star is None: return None
    try:
        return round(min(max(star,0),5)*20.0, 1)
    except:
        return None

def choose_best_orders(item: Any) -> int:
    # Prefer last 30-day volume if present; else total sales/orders field if provided by wrapper
    for field in ("lastest_volume", "last_volume", "volume", "orders", "sale_count", "product_sale"):
        v = getattr(item, field, None) if hasattr(item, field) else item.get(field) if isinstance(item, dict) else None
        if v is None: continue
        try:
            return int(v)
        except: 
            try:
                return int(float(str(v).replace(',','')))
            except:
                pass
    return 0

def extract_coupon(item: Any) -> Optional[str]:
    # Many APIs expose coupon fields differently; try common ones
    fields = []
    if hasattr(item, "coupon_start_time") or hasattr(item,"coupon_amount"):
        amt = getattr(item,"coupon_amount",None)
        if amt:
            fields.append(f"🎁 קופון לחברי הערוץ בלבד: {amt}")
    for k in ("coupon","coupon_amount","coupon_value","coupon_info","coupon_link","coupon_start_time"):
        v = getattr(item,k,None) if hasattr(item,k) else item.get(k) if isinstance(item,dict) else None
        if v:
            if isinstance(v,(int,float)): fields.append(f"🎁 קופון לחברי הערוץ בלבד: {v}")
            elif isinstance(v,str): fields.append(f"🎁 קופון לחברי הערוץ בלבד: {v}")
    if fields:
        return fields[0]
    return None

def get_aff_link(product_url: str) -> str:
    # Try official wrapper
    if aliexpress:
        try:
            links = aliexpress.get_affiliate_links(product_url)
            if links and getattr(links[0], "promotion_link", None):
                return links[0].promotion_link
        except Exception as e:
            print(f"[AE] affiliate_links failed: {e}")
    return product_url

def build_post_he(item: Any, aff_url: str) -> str:
    # Extract fields from wrapper object
    title = getattr(item, "product_title", None) or getattr(item, "title", "מוצר")
    sale = getattr(item, "target_sale_price", None) or getattr(item, "sale_price", None) or getattr(item, "app_sale_price", None)
    orig = getattr(item, "target_original_price", None) or getattr(item, "original_price", None)
    orders = choose_best_orders(item)
    rating = None
    for f in ("evaluate_rate", "product_average_rating", "avg_rating", "evaluate_score"):
        v = getattr(item,f,None) if hasattr(item,f) else item.get(f) if isinstance(item,dict) else None
        if v:
            try:
                rating = float(v)
                break
            except: pass
    rating_pct = star_to_pct(rating) if rating is not None and rating<=5.0 else (float(rating) if rating else None)

    # Shipping
    shipping_line = "🚚 משלוח חינם/מחושב בקופה"
    for k in ("freight_template_id","logistics_info","estimated_delivery_time"):
        if hasattr(item,k) or (isinstance(item,dict) and k in item):
            shipping_line = "🚚 משלוח זמין לישראל (עלות מחושבת בקופה)"
            break

    # Savings
    saving_pct = pct(float(sale) if sale else 0.0, float(orig) if orig else 0.0)

    # Template (based on your sample)
    # CTA + Description are left simple; often you run a translator upstream
    cta = f"🏗️ {title[:28]} — בשלט!"
    desc = f"🚜 {title}"
    strengths = [
        "🧲 הנעה ‎4WD‎ לעבירות משופרת",
        "🔦 תאורת ‎LED‎ לאקשן גם בערב",
        "🎮 שלט ‎2.4G‎ יציב, קל להפעלה",
    ]

    lines = []
    lines.append(cta)
    lines.append("")
    lines.append(desc)
    lines.append("")
    lines.extend(strengths)
    if sale:
        lines.append(f"💰 מחיר מבצע: {fmt_price(sale)} ({aff_url})" + (f" (מחיר מקורי: {fmt_price(orig)})" if orig else ""))
    if saving_pct is not None:
        lines.append(f"💸 חיסכון של {saving_pct}%!")
    if rating_pct is not None:
        lines.append(f"⭐️ דירוג: {rating_pct}%")
    if orders:
        lines.append(f"📦 {orders} הזמנות")
    lines.append(shipping_line)
    lines.append(f"להזמנה מהירה👈 לחצו כאן ({aff_url})")
    product_id = getattr(item, "product_id", None) or getattr(item, "item_id", None) or getattr(item, "app_sale_price_id", None) or "—"
    lines.append(f"מספר פריט: {product_id}")
    lines.append(f"להצטרפות לערוץ לחצו כאן👈 קליק והצטרפתם ({CHANNEL_JOIN_URL})")
    lines.append("👇🛍הזמינו עכשיו🛍👇")
    lines.append(f"לחיצה וזה בדרך ({aff_url})")

    # Coupon (optional line near the end)
    coup = extract_coupon(item)
    if coup: lines.insert(-2, coup)

    return "\n".join(lines)

def search_keyword(keyword: str, size:int=50, page:int=1) -> List[Any]:
    """Use official wrapper. Sort by highest recent orders; ship to IL; then filter orders>=1000"""
    results: List[Any] = []
    if not aliexpress:
        print("[AE] Not initialized; cannot query API")
        return results
    try:
        # Many wrappers accept sort keys like LAST_VOLUME_DESC (highest recent orders)
        response = aliexpress.get_products(
            keywords=keyword,
            sort='LAST_VOLUME_DESC',
            ship_to_country='IL',
            page_no=page,
            page_size=size
        )
        products = getattr(response, "products", [])
        for p in products:
            if choose_best_orders(p) >= IL_MIN_ORDERS:
                results.append(p)
    except Exception as e:
        print(f"[AE] get_products failed: {e}")
    return results

def discover_from_categories(max_per_sub:int=10) -> List[Any]:
    """Iterate categories.json → collect candidates"""
    try:
        cfg = json.loads(Path(CATEGORIES_PATH).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[CFG] categories.json read error: {e}")
        return []
    bag = []
    for cat in cfg:
        for sub in cat.get("sub", []):
            for kw in sub.get("keywords", []):
                found = search_keyword(kw, size=50, page=1)
                # already sorted by orders desc; keep up to max_per_sub
                bag.extend(found[:max_per_sub])
                time.sleep(0.4 + random.random()*0.3)
    # Dedupe by product_id
    seen = set()
    uniq = []
    for p in bag:
        pid = getattr(p,"product_id",None) or getattr(p,"item_id",None) or getattr(p,"app_sale_price_id",None)
        if pid and pid in seen: continue
        if pid: seen.add(pid)
        uniq.append(p)
    # Sort final by orders desc again
    uniq.sort(key=lambda x: choose_best_orders(x), reverse=True)
    return uniq

def post_to_channel(text: str):
    if not bot or not CHANNEL:
        print("[TG] Missing bot token or channel id")
        return
    try:
        bot.send_message(CHANNEL, text)
    except Exception as e:
        print(f"[TG] send_message failed: {e}")

# ===== Routes / Commands =====
@app.route("/", methods=["GET"])
def root():
    return "OK v8", 200

@app.route(WEBHOOK_ROUTE, methods=["POST"])
def webhook():
    if not bot:
        return "No bot", 500
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@bot.message_handler(commands=["start","help"])
def cmd_start(m):
    bot.reply_to(m, "היי! /discover כדי לאתר מוצרים חדשים (1000+ הזמנות, משלוח לישראל) ואז /post כדי לפרסם.")

@bot.message_handler(commands=["discover"])
def cmd_discover(m):
    bot.reply_to(m, "מאתר נישות ומוצרים...")
    items = discover_from_categories(max_per_sub=10)
    Path(BASE_DIR/"last_discover.json").write_text(json.dumps([p.__dict__ for p in items], ensure_ascii=False, indent=2), encoding="utf-8")
    bot.reply_to(m, f"נמצאו {len(items)} מוצרים מתאימים עם 1000+ הזמנות ושילוח לישראל. /post כדי לפרסם דוגמה.")

@bot.message_handler(commands=["post"])
def cmd_post(m):
    try:
        raw = json.loads((BASE_DIR/"last_discover.json").read_text(encoding="utf-8"))
    except Exception:
        bot.reply_to(m,"אין תוצאות אחרונות. הרץ /discover קודם.")
        return
    if not raw:
        bot.reply_to(m,"אין פריטים מתאימים כרגע.")
        return
    # Pick best by orders
    raw.sort(key=lambda x: choose_best_orders(x), reverse=True)
    top = raw[0]
    class Obj: pass
    obj = Obj(); obj.__dict__.update(top)
    url = getattr(obj,"product_url",None) or getattr(obj,"product_detail_url",None) or getattr(obj,"product_main_url", None) or f"https://aliexpress.com/item/{getattr(obj,'product_id','')}"
    aff = get_aff_link(url)
    text = build_post_he(obj, aff)
    post_to_channel(text)
    bot.reply_to(m, "נשלח לערוץ ✅")

def setup_webhook():
    if not bot: 
        print("[BOOT] Bot token missing")
        return
    if USE_WEBHOOK and WEBHOOK_BASE:
        wh = WEBHOOK_BASE.rstrip("/") + WEBHOOK_ROUTE_ENC
        try:
            bot.remove_webhook()
        except Exception: pass
        ok = bot.set_webhook(url=wh, allowed_updates=["message","callback_query"])
        print("setWebhook:", wh, ok)
    else:
        print("Webhook disabled; polling not implemented in this minimal build.")

def serve():
    from waitress import serve as wserve
    print(f"[BOOT] Serving on {HOST}:{PORT}")
    wserve(app, host=HOST, port=PORT)

if __name__ == "__main__":
    setup_webhook()
    serve()
