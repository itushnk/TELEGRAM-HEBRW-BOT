
import os
import time
import json
import telebot
from telebot import types
from flask import Flask, request, abort

# ================== Config from ENV ==================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    print("[INIT] Missing TELEGRAM_BOT_TOKEN/BOT_TOKEN in ENV", flush=True)

# Base URL for webhook (HTTPS). Options in priority:
# 1) TELEGRAM_WEBHOOK_URL (full URL, e.g. https://your.domain.tld/webhook/<id>)
# 2) TELEGRAM_WEBHOOK_BASE or WEBHOOK_BASE (base host), we'll append /webhook/<id>
# 3) RAILWAY_STATIC_URL or RAILWAY_PUBLIC_DOMAIN (Railway)
# 4) RENDER_EXTERNAL_URL (Render, in case of migration)
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL") or ""
WEBHOOK_BASE = os.getenv("TELEGRAM_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
RAILWAY_DOMAIN = os.getenv("RAILWAY_STATIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or ""
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") or ""

WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")  # optional, extra protection

# Data dir & lock for on/off
BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")
os.makedirs(BASE_DIR, exist_ok=True)
LOCK_PATH = os.environ.get("BOT_LOCK_PATH", os.path.join(BASE_DIR, "bot.lock"))

# Flask app
app = Flask(__name__)

# Bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)

# ================ Utilities ==================
def is_locked():
    return os.path.exists(LOCK_PATH)

def set_locked(val: bool):
    try:
        if val:
            with open(LOCK_PATH, "w", encoding="utf-8") as f:
                f.write("off")
        else:
            if os.path.exists(LOCK_PATH):
                os.remove(LOCK_PATH)
        return True
    except Exception as e:
        print(f"[LOCK] err: {e}", flush=True)
        return False

def inline_menu_fallback():
    km = types.InlineKeyboardMarkup(row_width=2)
    km.add(
        types.InlineKeyboardButton("🟢 הפעלה/כיבוי", callback_data="bot_toggle"),
        types.InlineKeyboardButton("🔥 מוצרים חמים", callback_data="ae_cat_hot"),
    )
    km.add(
        types.InlineKeyboardButton("📜 מצב תור", callback_data="queue_status"),
        types.InlineKeyboardButton("🧪 אבחון", callback_data="diag"),
    )
    return km

# Try to import existing helpers from your codebase if available
try:
    from ae_portal import do_ae_pull_async, do_ae_pull  # type: ignore
except Exception:
    do_ae_pull_async = None
    do_ae_pull = None

try:
    from main import inline_menu as existing_inline_menu  # if exists in original file, optional
except Exception:
    existing_inline_menu = None

def get_menu():
    if existing_inline_menu and callable(existing_inline_menu):
        try:
            return existing_inline_menu()
        except Exception:
            pass
    return inline_menu_fallback()

# ================ Commands ==================
@bot.message_handler(commands=["ping"])
def cmd_ping(m):
    bot.reply_to(m, "pong ✅")

@bot.message_handler(commands=["on"])
def cmd_on(m):
    set_locked(False)
    bot.reply_to(m, "🔌 הבוט הופעל")

@bot.message_handler(commands=["off"])
def cmd_off(m):
    set_locked(True)
    bot.reply_to(m, "🛑 הבוט כובה")

@bot.message_handler(commands=["health"])
def cmd_health(m):
    flags = [
        f"lock={'ON' if is_locked() else 'OFF'}",
        f"has_secret={'YES' if WEBHOOK_SECRET else 'NO'}",
    ]
    bot.reply_to(m, "Health OK\n" + " | ".join(flags))

@bot.message_handler(commands=["start","menu"])
def cmd_start(m):
    state = "כבוי" if is_locked() else "פעיל"
    try:
        bot.reply_to(m, f"שלום! מצב בוט: <b>{state}</b>", reply_markup=get_menu())
    except Exception:
        bot.reply_to(m, f"שלום! מצב בוט: <b>{state}</b>")

# ================ Callback Router ==================
@bot.callback_query_handler(func=lambda c: True)
def cb_router(c):
    try:
        data = getattr(c, "data", "") or ""
        uid = getattr(getattr(c, "from_user", None), "id", None)
        print(f"[CB] from={uid} data={data}", flush=True)

        if data == "bot_toggle":
            set_locked(not is_locked())
            bot.answer_callback_query(c.id, "בוצע.")
            try:
                bot.edit_message_reply_markup(chat_id=c.message.chat.id, message_id=c.message.message_id, reply_markup=get_menu())
            except Exception:
                pass
            return

        if data == "queue_status":
            bot.answer_callback_query(c.id, "תור: 0 ממתינים (דמו).")
            return

        if data == "diag":
            bot.answer_callback_query(c.id, "OK")
            bot.send_message(c.message.chat.id, "✅ שרות חי, webhook פעיל, כפתורים נקלטים.")
            return

        if data.startswith("ae_cat_"):
            if is_locked():
                bot.answer_callback_query(c.id, "הבוט כבוי.", show_alert=True)
                return
            cat = data.split("_", 2)[2]
            bot.answer_callback_query(c.id, "⏳ מתחיל שאיבה (דמו)...")
            if do_ae_pull_async and callable(do_ae_pull_async):
                try:
                    do_ae_pull_async(cat=cat, chat_id=c.message.chat.id, cb_id=c.id)
                    return
                except Exception as e:
                    bot.send_message(c.message.chat.id, f"שגיאה בהפעלה: {e}")
                    return
            elif do_ae_pull and callable(do_ae_pull):
                try:
                    do_ae_pull(cat=cat, chat_id=c.message.chat.id, cb_id=c.id)
                    return
                except Exception as e:
                    bot.send_message(c.message.chat.id, f"שגיאה בהפעלה: {e}")
                    return
            else:
                bot.send_message(c.message.chat.id, "⚠️ לא נמצא מטפל לשאיבה בקוד הנוכחי.")
                return

        # default ack
        try:
            bot.answer_callback_query(c.id, "👌")
        except Exception:
            pass
    except Exception as e:
        print(f"[CB][ERR] {e}", flush=True)

# ================ Flask Routes ==================
@app.route("/healthz", methods=["GET"])
def healthz():
    return {"ok": True, "status": "up"}, 200

@app.route("/webhook/<hook_id>", methods=["POST"])
def webhook(hook_id):
    # Optional: verify secret header if provided
    if WEBHOOK_SECRET:
        recv = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if recv != WEBHOOK_SECRET:
            print("[WH] bad secret", flush=True)
            return "forbidden", 403
    # Process update
    if request.headers.get("content-type") == "application/json":
        try:
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
        except Exception as e:
            print(f"[WH][ERR] {e}", flush=True)
        return "OK", 200
    return "unsupported", 415

# ================ Webhook Setup ==================
def compute_webhook_url():
    if WEBHOOK_URL:
        return WEBHOOK_URL
    base = WEBHOOK_BASE or RAILWAY_DOMAIN or RENDER_URL
    if not base:
        print("[WH] No base URL found (TELEGRAM_WEBHOOK_URL/BASE/RAILWAY_*).", flush=True)
        return ""
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    if not base.startswith("https://"):
        base = "https://" + base
    hook_id = BOT_TOKEN[:8]  # non-sensitive id
    return base.rstrip("/") + f"/webhook/{hook_id}"

def setup_webhook():
    url = compute_webhook_url()
    if not url or not BOT_TOKEN:
        print(f"[WH] cannot set webhook: url={url!r} token={'OK' if BOT_TOKEN else 'MISSING'}", flush=True)
        return False
    try:
        # Set webhook with optional secret token; drop pending updates to start clean
        ok = bot.set_webhook(url=url, secret_token=(WEBHOOK_SECRET or None), drop_pending_updates=True,
                             allowed_updates=['message','callback_query','inline_query','chat_member','my_chat_member'])
        print(f"[WH] set_webhook -> {ok} url={url}", flush=True)
        return ok
    except TypeError:
        # Older pyTelegramBotAPI without secret_token support
        ok = bot.set_webhook(url=url, drop_pending_updates=True)
        print(f"[WH] set_webhook(no-secret) -> {ok} url={url}", flush=True)
        return ok
    except Exception as e:
        print(f"[WH][ERR] set_webhook failed: {e}", flush=True)
        return False

def run_server():
    port = int(os.getenv("PORT", "8080"))
    host = "0.0.0.0"
    print(f"[BOOT] Starting Flask on {host}:{port}", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)

if __name__ == "__main__":
    # On boot, if not start-locked, ensure bot is ON
    if os.getenv("BOT_START_LOCKED", "0") != "1":
        set_locked(False)
        print("[BOOT] Cleared bot lock", flush=True)
    setup_webhook()
    run_server()
