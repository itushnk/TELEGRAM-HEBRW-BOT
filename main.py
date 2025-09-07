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
import requests
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

# ===== Runtime Config (persisted) =====
CONFIG_PATH = BASE_DIR / "config.json"
_runtime = {"CHANNEL": CHANNEL, "IL_MIN_ORDERS": IL_MIN_ORDERS}

def _load_runtime():
    global CHANNEL, IL_MIN_ORDERS, _runtime
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _runtime.update(data)
            CHANNEL = data.get("CHANNEL", CHANNEL)
            IL_MIN_ORDERS = int(data.get("IL_MIN_ORDERS", IL_MIN_ORDERS))
            print(f"[CFG] Loaded runtime overrides: CHANNEL={CHANNEL!r} IL_MIN_ORDERS={IL_MIN_ORDERS}")
    except Exception as e:
        print("[CFG] load error:", e)

def _save_runtime():
    try:
        CONFIG_PATH.write_text(json.dumps(_runtime, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[CFG] Saved runtime", CONFIG_PATH)
    except Exception as e:
        print("[CFG] save error:", e)

_load_runtime()

# ===== Runtime Config (persisted) EXT =====
POLL_STARTED = False

# extend defaults
_runtime.update({
    "USE_WEBHOOK": USE_WEBHOOK,
    "WEBHOOK_BASE": WEBHOOK_BASE,
    "FREE_SHIPPING_ONLY": False,
    "MAX_SHIP_ILS": None
})

def _to_float(v):
    try:
        if v is None or v == "None":
            return None
        return float(str(v).replace("₪","").replace("ש\"ח","").replace(",","").strip())
    except Exception:
        return None

def _apply_runtime():
    global USE_WEBHOOK, WEBHOOK_BASE, FREE_SHIPPING_ONLY, MAX_SHIP_ILS
    USE_WEBHOOK = bool(_runtime.get("USE_WEBHOOK", USE_WEBHOOK))
    WEBHOOK_BASE = _runtime.get("WEBHOOK_BASE", WEBHOOK_BASE)
    FREE_SHIPPING_ONLY = bool(_runtime.get("FREE_SHIPPING_ONLY", False))
    MAX_SHIP_ILS = _to_float(_runtime.get("MAX_SHIP_ILS"))

def _load_runtime_ext():
    _apply_runtime()

def _save_and_apply():
    _save_runtime()
    _apply_runtime()

_load_runtime_ext()



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

def _load_categories() -> list:
    try:
        return json.loads(Path(CATEGORIES_PATH).read_text(encoding="utf-8"))
    except Exception as e:
        print("[CFG] categories read error:", e)
        return []

def _save_categories(data: list):
    try:
        Path(CATEGORIES_PATH).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print("[CFG] categories write error:", e)
        return False


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


def _as_dict(obj):
    try:
        d = dict(obj.__dict__)
        for k in list(d.keys()):
            try:
                json.dumps(d[k])
            except Exception:
                d[k] = str(d[k])
        return d
    except Exception:
        return {}

def shipping_ok(item) -> bool:
    free_flag = None
    for k in ("free_shipping","is_free_shipping","freeFreight"):
        v = getattr(item,k,None) if hasattr(item,k) else item.get(k) if isinstance(item,dict) else None
        if isinstance(v, bool):
            free_flag = v
            break
        if isinstance(v, str):
            if v.lower() in ("true","yes","1"): free_flag = True
            elif v.lower() in ("false","no","0"): free_flag = False
    ship_cost = None
    for k in ("shipping_price","freight_amount","local_carrier_fee","post_fee","logistics_amount"):
        v = getattr(item,k,None) if hasattr(item,k) else item.get(k) if isinstance(item,dict) else None
        if v is None: continue
        try:
            ship_cost = float(str(v).replace(",",""))
            break
        except Exception:
            pass
    if _runtime.get("FREE_SHIPPING_ONLY", False):
        if free_flag is True:
            return True
        if ship_cost is not None and ship_cost <= 0.0001:
            return True
        return False
    max_v = _to_float(_runtime.get("MAX_SHIP_ILS"))
    if max_v is not None and ship_cost is not None:
        return ship_cost <= max_v
    return True

def _templates_index():
    data = _load_categories()
    idx = {}
    for cat in data:
        for sub in cat.get("sub", []):
            tpl = sub.get("template", {})
            if tpl:
                idx[(cat.get("name"), sub.get("name"))] = tpl
    return idx

def topic_from(item_dict: dict):
    return item_dict.get("_topic")

def strengths_for_topic(topic):
    default = [
        "🧲 הנעה ‎4WD‎ לעבירות משופרת",
        "🔦 תאורת ‎LED‎ לאקשן גם בערב",
        "🎮 שלט ‎2.4G‎ יציב, קל להפעלה",
    ]
    if not topic: return default
    idx = _templates_index()
    tpl = idx.get((topic.get("cat"), topic.get("sub")))
    if not tpl: return default
    s = tpl.get("strengths")
    if isinstance(s, list) and len(s) >= 3:
        return s[:3]
    return default


def _tpl_for(topic):
    if not topic: 
        return {}
    idx = _templates_index()
    return idx.get((topic.get("cat"), topic.get("sub")), {})

def _short_title(t: str, n: int = 28) -> str:
    try:
        return (t[:n] + ("…" if len(t) > n else ""))
    except Exception:
        return t

def cta_for_topic(title: str, topic):
    tpl = _tpl_for(topic)
    cta_tpl = tpl.get("cta")
    if isinstance(cta_tpl, str) and cta_tpl.strip():
        # placeholders: {title}, {title_28}, {sub}
        return cta_tpl.format(title=title, title_28=_short_title(title, 28), sub=topic.get("sub") if topic else "")
    if topic and topic.get("sub"):
        return f"🏷️ {topic.get('sub')} — {_short_title(title,28)}"
    return f"🏗️ {_short_title(title,28)} — בשלט!"

def shipping_line_override(topic):
    tpl = _tpl_for(topic)
    return tpl.get("ship_line")

def warranty_line_override(topic):
    tpl = _tpl_for(topic)
    return tpl.get("warranty_line")


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

def build_post_he(item: Any, aff_url: str, topic: dict|None=None) -> str:
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
    # Template override for shipping line (if provided)
    tpl_ship = shipping_line_override(topic)
    if isinstance(tpl_ship, str) and tpl_ship.strip():
        shipping_line = tpl_ship

    # Savings
    saving_pct = pct(float(sale) if sale else 0.0, float(orig) if orig else 0.0)

    # Template (based on your sample)
    # CTA + Description are left simple; often you run a translator upstream
    cta = cta_for_topic(title, topic)
    desc = f"🚜 {title}"
    strengths = strengths_for_topic(topic)

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
    # Optional: warranty/return note
    wln = warranty_line_override(topic)
    if isinstance(wln, str) and wln.strip():
        lines.append(wln)
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
            if choose_best_orders(p) >= IL_MIN_ORDERS and shipping_ok(p):
                results.append(p)
    except Exception as e:
        print(f"[AE] get_products failed: {e}")
    return results


def discover_from_categories(max_per_sub:int=10) -> list[dict]:
    cfg = _load_categories()
    bag = []
    for cat in cfg:
        for sub in cat.get("sub", []):
            for kw in sub.get("keywords", []):
                found = search_keyword(kw, size=50, page=1)
                kept = [p for p in found if shipping_ok(p)]
                for p in kept[:max_per_sub]:
                    d = _as_dict(p)
                    d["_topic"] = {"cat": cat.get("name"), "sub": sub.get("name"), "kw": kw}
                    bag.append(d)
                time.sleep(0.4 + random.random()*0.3)
    seen = set(); uniq = []
    for d in bag:
        pid = d.get("product_id") or d.get("item_id") or d.get("app_sale_price_id")
        if pid and pid in seen: 
            continue
        if pid: seen.add(pid)
        uniq.append(d)
    uniq.sort(key=lambda x: choose_best_orders(x), reverse=True)
    return uniq


def post_to_channel(text: str, chat_id: int|str|None=None):
    if not bot:
        print("[TG] Missing bot token")
        return
    target = CHANNEL or chat_id
    if not target:
        print("[TG] No CHANNEL and no chat_id fallback; set TELEGRAM_CHANNEL or call from a chat")
        return
    try:
        bot.send_message(target, text)
        if not CHANNEL:
            print(f"[TG] Sent via fallback to chat_id={target}")
    except Exception as e:
        print(f"[TG] send_message failed: {e}")

# ===== Routes / Commands =====
@app.route("/", methods=["GET"])
def root():
    return "OK v8b", 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200

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
    Path(BASE_DIR/"last_discover.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
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
    topic = top.get("_topic")
    class Obj: pass
    obj = Obj(); obj.__dict__.update(top)
    url = getattr(obj,"product_url",None) or getattr(obj,"product_detail_url",None) or getattr(obj,"product_main_url", None) or f"https://aliexpress.com/item/{getattr(obj,"product_id","")}"
    aff = get_aff_link(url)
    text = build_post_he(obj, aff, topic=topic)
    post_to_channel(text, chat_id=m.chat.id)
    bot.reply_to(m, "נשלח ✅ (אם CHANNEL לא הוגדר — נשלח לצ׳אט הנוכחי)")


@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.reply_to(m, (
        "פקודות זמינות:\n"
        "/discover – איתור מוצרים לפי קטגוריות\n"
        "/discover_kw <מילים> – חיפוש ישיר לפי מילות מפתח\n"
        "/post – פרסום המוצר הטופ\n"
        "/config – הצגת קונפיג נוכחי\n"
        "/webhookinfo – סטטוס וובהוק מטלגרם\n"
        "/setchannel <@channel או id> – הגדרת יעד פרסום\n"
        "/minorders <מספר> – קביעת מינימום הזמנות (בררת מחדל 1000)\n"
        "/aetest <keyword> – בדיקת מפתחות AliExpress ומהדגם חיפוש\n"
        "/webmode polling|webhook <base> – החלפת מצב קבלת עדכונים\n"
        "/ship free|any – מסנן משלוח חינם בלבד\n"
        "/shipmax <₪> – תקרת עלות משלוח\n"
        "/catlist – סיכום קטגוריות ותתי-נישות\n"
        "/catadd <קטגוריה>|<תת>|<kw1,kw2> – הוספת תת-נישה ומילות חיפוש\n"
    ))

@bot.message_handler(commands=["webhookinfo"])
def cmd_webhookinfo(m):
    try:
        info = bot.get_webhook_info()
        bot.reply_to(m, f"WebhookInfo:\nurl={info.url}\nhas_custom_certificate={info.has_custom_certificate}\npending_update_count={info.pending_update_count}")
    except Exception as e:
        bot.reply_to(m, f"WebhookInfo error: {e}")

@bot.message_handler(commands=["setchannel"])
def cmd_setchannel(m):
    global CHANNEL, _runtime
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(m, "שימוש: /setchannel @שם_ערוץ או chat id (למשל -1001234567890)")
        return
    target = args[1].strip()
    CHANNEL = target
    _runtime["CHANNEL"] = CHANNEL
    _save_runtime()
    bot.reply_to(m, f"CHANNEL עודכן ל: {CHANNEL}")

@bot.message_handler(commands=["minorders"])
def cmd_minorders(m):
    global IL_MIN_ORDERS, _runtime
    args = m.text.split()
    if len(args) < 2 or not args[1].isdigit():
        bot.reply_to(m, "שימוש: /minorders 1500")
        return
    IL_MIN_ORDERS = int(args[1])
    _runtime["IL_MIN_ORDERS"] = IL_MIN_ORDERS
    _save_runtime()
    bot.reply_to(m, f"IL_MIN_ORDERS עודכן ל: {IL_MIN_ORDERS}")

@bot.message_handler(commands=["aetest"])
def cmd_aetest(m):
    if not aliexpress:
        bot.reply_to(m, "ספריית AliExpress לא מאותחלת. בדוק AE_KEY/AE_SECRET/AE_TRACKING_ID.")
        return
    kw = m.text.split(maxsplit=1)
    keyword = kw[1] if len(kw) > 1 else "rc car 4wd"
    try:
        resp = aliexpress.get_products(keywords=keyword, sort='LAST_VOLUME_DESC', ship_to_country='IL', page_size=1)
        prods = getattr(resp, "products", [])
        if not prods:
            bot.reply_to(m, f"לא נמצאו מוצרים עבור '{keyword}'. בדוק הרשאות/מפתחות.")
            return
        p = prods[0]
        title = getattr(p, "product_title", None) or getattr(p, "title", "מוצר")
        orders = 0
        for f in ("lastest_volume","volume","orders","sale_count"):
            if hasattr(p,f):
                try:
                    orders = int(getattr(p,f))
                    break
                except: pass
        bot.reply_to(m, f"OK: {title}\norders≈{orders}\nproduct_id={getattr(p,'product_id',None)}")
    except Exception as e:
        bot.reply_to(m, f"API error: {e}")

@bot.message_handler(commands=["catlist"])
def cmd_catlist(m):
    data = _load_categories()
    total_sub = sum(len(c.get("sub",[])) for c in data)
    total_kw = sum(len(s.get("keywords",[])) for c in data for s in c.get("sub",[]))
    bot.reply_to(m, f"קטגוריות: {len(data)} | תתי-נישות: {total_sub} | מילות חיפוש: {total_kw}")

@bot.message_handler(commands=["catadd"])
def cmd_catadd(m):
    # Syntax: /catadd <cat>|<sub>|<kw1,kw2,...>
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or "|" not in args[1]:
        bot.reply_to(m, "שימוש: /catadd קטגוריה|תת-נישה|מילת חיפוש1,מילת חיפוש2")
        return
    try:
        cat, sub, kws = [x.strip() for x in args[1].split("|", 2)]
        kw_list = [k.strip() for k in kws.split(",") if k.strip()]
        data = _load_categories()
        # find or create category
        cat_obj = next((c for c in data if c.get("name")==cat), None)
        if not cat_obj:
            cat_obj = {"name": cat, "sub": []}
            data.append(cat_obj)
        sub_obj = next((s for s in cat_obj["sub"] if s.get("name")==sub), None)
        if not sub_obj:
            sub_obj = {"name": sub, "keywords": []}
            cat_obj["sub"].append(sub_obj)
        existing = set(sub_obj.get("keywords", []))
        for k in kw_list:
            if k not in existing:
                sub_obj["keywords"].append(k)
        if _save_categories(data):
            bot.reply_to(m, f"נוסף ✔️ {cat} › {sub} | {', '.join(kw_list)}")
        else:
            bot.reply_to(m, "שמירה נכשלה")
    except Exception as e:
        bot.reply_to(m, f"שגיאה: {e}")

@bot.message_handler(commands=["discover_kw"])
def cmd_discover_kw(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(m, "שימוש: /discover_kw <מילות חיפוש>")
        return
    kw = args[1].strip()
    items = search_keyword(kw, size=50, page=1)
    if not items:
        bot.reply_to(m, "לא נמצאו תוצאות עם הסינון (IL + 1000+ הזמנות).")
        return
    rows = []
    for p in items:
        d = _as_dict(p)
        d["_topic"] = {"cat":"KW","sub":"KW","kw": kw}
        rows.append(d)
    Path(BASE_DIR/"last_discover.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    bot.reply_to(m, f"נמצאו {len(rows)} מוצרים עבור '{kw}'. /post כדי לפרסם דוגמה.")


@bot.message_handler(commands=["webmode"])
def cmd_webmode(m):
    args = m.text.split()
    if len(args) < 2 or args[1] not in ("polling","webhook"):
        bot.reply_to(m, "שימוש: /webmode polling | /webmode webhook <base_url>")
        return
    mode = args[1]
    global _runtime, POLL_STARTED
    if mode == "polling":
        _runtime["USE_WEBHOOK"] = False
        _save_and_apply()
        try:
            bot.remove_webhook()
        except Exception: pass
        if not POLL_STARTED:
            def _poll():
                global POLL_STARTED
                POLL_STARTED = True
                try:
                    bot.infinity_polling(timeout=30, long_polling_timeout=30, allowed_updates=["message","callback_query"])
                except Exception as e:
                    print("[POLL] exited:", e)
                    POLL_STARTED = False
            import threading
            threading.Thread(target=_poll, daemon=True).start()
        bot.reply_to(m, "מצב עודכן ל: POLLING (וובהוק הוסר).")
    else:
        if len(args) < 3:
            bot.reply_to(m, "שימוש: /webmode webhook https://your-app.up.railway.app")
            return
        base = args[2].strip()
        _runtime["USE_WEBHOOK"] = True
        _runtime["WEBHOOK_BASE"] = base
        _save_and_apply()
        try:
            from urllib.parse import quote
            route = WEBHOOK_ROUTE_ENC if 'WEBHOOK_ROUTE_ENC' in globals() else WEBHOOK_SECRET
            wh = base.rstrip("/") + route
            bot.remove_webhook()
            ok = bot.set_webhook(url=wh, allowed_updates=["message","callback_query"])
            bot.reply_to(m, f"Webhook נקבע: {wh} | ok={ok}")
        except Exception as e:
            bot.reply_to(m, f"הגדרת וובהוק נכשלה: {e}")

@bot.message_handler(commands=["ship"])
def cmd_ship(m):
    args = m.text.split()
    if len(args) < 2 or args[1] not in ("free","any"):
        bot.reply_to(m, "שימוש: /ship free | /ship any")
        return
    v = (args[1] == "free")
    _runtime["FREE_SHIPPING_ONLY"] = v
    _save_and_apply()
    bot.reply_to(m, f"מסנן משלוח: {'חינם בלבד' if v else 'כל סוג'}")

@bot.message_handler(commands=["shipmax"])
def cmd_shipmax(m):
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "שימוש: /shipmax 15")
        return
    v = _to_float(args[1])
    _runtime["MAX_SHIP_ILS"] = v
    _save_and_apply()
    bot.reply_to(m, f"מסנן עלות משלוח מקסימלית עודכן ל: {v if v is not None else 'ללא הגבלה'} ₪")



def _delete_webhook_hard():
    if not bot or not BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
        r = requests.get(url, timeout=10)
        print("[BOOT] deleteWebhook drop_pending_updates:", r.status_code, r.text[:120])
    except Exception as e:
        print("[BOOT] deleteWebhook failed:", e)


def setup_webhook():
    if not bot:
        print("[BOOT] Bot token missing")
        return
    print(f"[BOOT] USE_WEBHOOK={USE_WEBHOOK} WEBHOOK_BASE={WEBHOOK_BASE!r} CHANNEL={CHANNEL!r}")
    if USE_WEBHOOK and WEBHOOK_BASE:
        wh = WEBHOOK_BASE.rstrip("/") + WEBHOOK_ROUTE_ENC
        try:
            bot.remove_webhook()
        except Exception:
            pass
        ok = bot.set_webhook(url=wh, allowed_updates=["message","callback_query"])
        print("setWebhook:", wh, ok)
    else:
        print("[BOOT] Webhook disabled/missing base — starting polling mode")
        def _poll():
            # Hard delete webhook (drop pending) and then start polling with retry
            try:
                try:
                    bot.remove_webhook()
                except Exception:
                    pass
                _delete_webhook_hard()
            except Exception:
                pass
            import time as _t
            backoff = 5
            while True:
                try:
                    bot.infinity_polling(timeout=30, long_polling_timeout=30, allowed_updates=["message","callback_query"])
                except Exception as e:
                    print("[POLL] exited:", e)
                    msg = str(e)
                    if "Error code: 409" in msg or "terminated by other getUpdates" in msg:
                        print("[POLL] 409 Conflict — יש כנראה אינסטנס נוסף של הבוט עם אותו טוקן שמריץ polling. ודא שיש רק שירות אחד פעיל, או עבור ל-Webhook.")
                    _t.sleep(backoff)
                    backoff = min(backoff*2, 120)
                else:
                    backoff = 5
            
        import threading
        t = threading.Thread(target=_poll, daemon=True)
        t.start()
        print("[POLL] Started background polling thread (robust)")

def serve():
    from waitress import serve as wserve
    print(f"[BOOT] Serving on {HOST}:{PORT}")
    wserve(app, host=HOST, port=PORT)

if __name__ == "__main__":
    setup_webhook()
    serve()


@bot.message_handler(commands=["config"])
def cmd_config(m):
    envs = f"""USE_WEBHOOK={USE_WEBHOOK}
WEBHOOK_BASE_URL={WEBHOOK_BASE or '(empty)'}
WEBHOOK_ROUTE={WEBHOOK_ROUTE}
CHANNEL={CHANNEL or '(empty)'}
TARGET_CURRENCY={TARGET_CCY}
TARGET_LANGUAGE={TARGET_LANG}
IL_MIN_ORDERS={IL_MIN_ORDERS}
AE_KEY={'set' if AE_KEY else '(empty)'}
AE_SECRET={'set' if AE_SECRET else '(empty)'}
AE_TRACKING_ID={'set' if AE_TRACKING_ID else '(empty)'}"""
    bot.reply_to(m, envs)
