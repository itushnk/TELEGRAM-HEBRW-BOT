
# autopatch.py - hotfix for your existing main.py on Railway
import os, re, sys

PATH = os.getenv("MAIN_PATH", "main.py")
print(f"[AUTOPATCH] Target: {PATH}", flush=True)

try:
    with open(PATH, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()
except FileNotFoundError:
    print("[AUTOPATCH][ERR] main.py not found", flush=True)
    sys.exit(1)

changed = False

# 1) Ensure POST_DELAY_SECONDS is defined at module-level (default 900s)
if "POST_DELAY_SECONDS" not in src[:3000]:
    m = re.search(r"^(?:from\s+\S+\s+import\s+\S+|import\s+\S+).*$", src, re.M)
    insert_at = m.end() if m else 0
    ins = "\nPOST_DELAY_SECONDS = int(os.getenv('POST_DELAY_SECONDS', '900'))  # [AUTOPATCH]\n"
    src = src[:insert_at] + ins + src[insert_at:]
    changed = True
    print("[AUTOPATCH] Added POST_DELAY_SECONDS default", flush=True)

# 2) Inject fallback fetch helper if missing (before normalize_ae_product if found)
if "_ae_fallback_fetch" not in src:
    helper = '''
# ===== AliExpress Fallback (Scrape) injected by autopatch =====
import json as _json, re as _re
from urllib.parse import urlencode as _urlencode
import requests as _req

def _ae_fallback_fetch(query: str, limit: int = 8, ship_to: str = "IL"):
    try:
        params = {"SearchText": query, "ShipCountry": ship_to or "IL", "SortType": "total_tranpro_desc", "g": "y"}
        url = "https://www.aliexpress.com/wholesale?" + _urlencode(params, doseq=True)
        sess = _req.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.aliexpress.com/"
        })
        cookies = {"xman_us_f": "x_lan=he_IL&x_locale=he_IL&region=IL&b_locale=he_IL"}
        r = sess.get(url, timeout=(10,20), cookies=cookies)
        r.raise_for_status()
        html = r.text
        items = []

        for pat in [r"window\.__AER_DATA__\s*=\s*(\{.*?\});", r"window\.runParams\s*=\s*(\{.*?\});"]:
            m = _re.search(pat, html, _re.S)
            if not m:
                continue
            raw = m.group(1).strip().rstrip(";")
            try:
                data = _json.loads(raw)
            except Exception:
                continue

            def _walk(o):
                if isinstance(o, dict):
                    pid = o.get("productId") or o.get("product_id") or o.get("itemId") or o.get("item_id") or o.get("id")
                    title = o.get("title") or o.get("productTitle") or o.get("product_title")
                    url = o.get("productDetailUrl") or o.get("product_detail_url") or o.get("productUrl") or o.get("url")
                    img = o.get("productMainImageUrl") or o.get("product_main_image_url") or o.get("image") or o.get("imageUrl") or o.get("image_url")
                    price = o.get("appSalePrice") or o.get("salePrice") or o.get("app_sale_price") or o.get("sale_price") or o.get("price")
                    if pid and title and url:
                        items.append({"id": str(pid), "title": str(title), "url": str(url), "image_url": img or "", "price": price or "", "rating": "", "orders": ""})
                    for v in o.values():
                        _walk(v)
                elif isinstance(o, list):
                    for it in o:
                        _walk(it)

        if not items:
            for m in _re.finditer(r'href="(https://www\.aliexpress\.com/item/[^"]+)"[^>]*>([^<]{10,120})</a>', html):
                url, title = m.group(1), m.group(2).strip()
                items.append({"id": str(abs(hash(url))), "title": title, "url": url, "image_url": "", "price": "", "rating": "", "orders": ""})

        uniq, out = set(), []
        for it in items:
            pid = it.get("id")
            if not pid or pid in uniq:
                continue
            uniq.add(pid)
            out.append(it)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        print(f"[AE][FALLBACK][ERR] {e}", flush=True)
        return []
'''
    pos = src.find("def normalize_ae_product")
    if pos == -1:
        src = src + helper
    else:
        src = src[:pos] + helper + src[pos:]
    changed = True
    print("[AUTOPATCH] Injected fallback helper", flush=True)

# 3) Patch on_inline_click: globals + early ACK + AE API->fallback + queue append
m = re.search(r"def\s+on_inline_click\s*\(c\)\s*:\s*\n", src)
if m:
    start = m.end()
    m2 = re.search(r"\n\ndef\s+\w+\s*\(", src[start:])
    end = start + (m2.start() if m2 else len(src) - start)
    body = src[start:end]

    # insert global at top
    lines = body.splitlines(True)
    j = 0
    while j < len(lines) and (lines[j].strip()=="" or lines[j].lstrip().startswith('#') or lines[j].lstrip().startswith('"""') or lines[j].lstrip().startswith("'''")):
        j += 1
    if "global POST_DELAY_SECONDS" not in "".join(lines[:5]):
        lines.insert(j, "    global POST_DELAY_SECONDS, CURRENT_TARGET  # [AUTOPATCH]\n")
        changed = True
        print("[AUTOPATCH] Injected globals in on_inline_click", flush=True)

    # remove later duplicate global lines
    cleaned = []
    first = False
    for ln in lines:
        if "global POST_DELAY_SECONDS" in ln or "global CURRENT_TARGET" in ln:
            if not first:
                cleaned.append(ln)
                first = True
            else:
                continue
        else:
            cleaned.append(ln)
    body = "".join(cleaned)

    # add early ACK on ae_cat_
    if "ae_cat_" in body and "⏳ שואב פריטים…" not in body:
        body = re.sub(
            r'(if\s+data\.startswith\("ae_cat_"\)\s*:\s*\n)',
            "\g<1>        try:\n            bot.answer_callback_query(c.id, "⏳ שואב פריטים…")\n        except Exception:\n            pass\n",
            body, count=1
        )
        changed = True
        print("[AUTOPATCH] Added early ACK for ae_cat_", flush=True)

    # Replace the core body of the ae_cat_ block with API+fallback + append_to_pending + summary message
    def patch_block(mo):
        head, blk = mo.group(1), mo.group(2)
        if "_ae_fallback_fetch(" in blk and "append_to_pending(" in blk:
            return mo.group(0)
        patched = (
            "        try:\n"
            "            prods = affiliate_product_query_by_category(category_id=cat, page_no=1, page_size=6, country='IL')\n"
            "        except Exception as _e:\n"
            "            print(f"[AE][API][ERR] {str(_e)}", flush=True)\n"
            "            prods = []\n"
            "        if not prods:\n"
            "            fb = _ae_fallback_fetch(cat, limit=8, ship_to='IL')\n"
            "            prods = [\n"
            "                {\n"
            "                    'id': it.get('id'),\n"
            "                    'title': it.get('title'),\n"
            "                    'url': it.get('url'),\n"
            "                    'image_url': it.get('image_url'),\n"
            "                    'price': it.get('price'),\n"
            "                    'rating': it.get('rating'),\n"
            "                    'orders': it.get('orders')\n"
            "                } for it in fb\n"
            "            ]\n"
            "        if not prods:\n"
            "            bot.send_message(c.message.chat.id, f"ℹ️ לא נמצאו פריטים מתאימים כרגע עבור '{cat}'. נסה קטגוריה אחרת.")\n"
            "        else:\n"
            "            rows = [normalize_ae_product(p) for p in prods]\n"
            "            append_to_pending(rows)\n"
            "            with FILE_LOCK:\n"
            "                pending_count = len(read_products(PENDING_CSV))\n"
            "            bot.send_message(c.message.chat.id, f"✅ נוספו {len(rows)} מוצרים. כעת בתור: {pending_count}")\n"
            "            safe_edit_message(bot, chat_id=chat_id, message=c.message,\n"
            "                              new_text="✅ השאיבה הושלמה. חזור לתפריט הראשי:",\n"
            "                              new_markup=inline_menu())\n"
        )
        return head + patched

    body = re.sub(r'(if\s+data\.startswith\("ae_cat_"\)\s*:\s*\n)([\s\S]{0,2500})', patch_block, body, count=1)

    # make answer_callback_query(alert=...) safer → convert to send_message
    body = re.sub(r'bot\.answer_callback_query\(\s*c\.id\s*,\s*f?["\'][^"\']+["\']\s*,\s*show_alert=True\s*\)',
                  'bot.send_message(c.message.chat.id, "ℹ️ עדכון בוצע.")', body)

    src = src[:start] + body + src[end:]
else:
    print("[AUTOPATCH][WARN] on_inline_click not found; skipped", flush=True)

# 4) Ensure /on removes the lock file if exists
if "@bot.message_handler(commands=['on'])" in src or '@bot.message_handler(commands=["on"])' in src:
    if "os.remove('data/bot.lock')" not in src:
        src = re.sub(
            r"(@bot\.message_handler\(commands=\['on'\]\)\s*def\s+\w+\(m\)\s*:\s*\n)",
            "\g<1>    try:\n        import os\n        os.remove('data/bot.lock')\n        print('[AUTOPATCH] Removed lock file data/bot.lock')\n    except FileNotFoundError:\n        pass\n",
            src,
            count=1
        )
        changed = True
        print("[AUTOPATCH] Ensured /on removes lock file", flush=True)

if changed:
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print("[AUTOPATCH] Patched successfully", flush=True)
else:
    print("[AUTOPATCH] Nothing to patch", flush=True)
