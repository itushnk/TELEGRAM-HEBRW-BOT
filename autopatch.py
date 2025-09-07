# autopatch.py - Railway hotfix autopatcher (v7)
# -*- coding: utf-8 -*-
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

# 0) Ensure 'import os' exists for getenv/remove usage
if re.search(r'^\s*import\s+os\b', src, re.M) is None and "os." in src:
    src = "import os\n" + src
    changed = True
    print("[AUTOPATCH] Added 'import os' at top", flush=True)

def insert_after_imports(snippet, tag):
    global src, changed
    m = None
    for m in re.finditer(r"^(?:from\s+\S+\s+import\s+\S+|import\s+\S+)\s*$", src, re.M):
        pass
    idx = m.end() if m else 0
    if tag not in src:
        src = src[:idx] + "\n" + snippet + "\n" + src[idx:]
        changed = True
        print(f"[AUTOPATCH] Inserted {tag}", flush=True)

# 1) Add module default POST_DELAY_SECONDS if missing
if "POST_DELAY_SECONDS" not in src[:3000]:
    insert_after_imports("POST_DELAY_SECONDS = int(os.getenv('POST_DELAY_SECONDS', '900'))  # [AUTOPATCH:default]", "[AUTOPATCH:default]")

# 2) Auto-clear bot.lock on boot
boot_clear = (
    "try:\n"
    "    import os\n"
    "    p = 'data/bot.lock'\n"
    "    if os.path.exists(p):\n"
    "        os.remove(p)\n"
    "        print('[AUTOPATCH] Auto-cleared data/bot.lock', flush=True)\n"
    "except Exception as _e:\n"
    "    print(f'[AUTOPATCH][WARN] boot clear lock failed: {_e}', flush=True)\n"
)
if "Auto-cleared data/bot.lock" not in src:
    insert_after_imports(boot_clear, "boot_clear_lock")

# 3) Inject 'global' at the top of any function where POST_DELAY_SECONDS/CURRENT_TARGET appear
def inject_global_for(varname):
    global src, changed
    pat = re.compile(r"def\s+(\w+)\s*\([^)]*\)\s*:\s*\n", re.M)
    out = []
    last = 0
    for m in pat.finditer(src):
        fn_start = m.end()
        m2 = pat.search(src, fn_start)
        fn_end = m2.start() if m2 else len(src)
        head = src[last:m.start()]
        body = src[fn_start:fn_end]
        header = src[m.start():fn_start]

        if re.search(rf"\b{re.escape(varname)}\b", body) and not re.search(rf"(?m)^\s*global\s+[^\n]*\b{re.escape(varname)}\b", body):
            lines = body.splitlines(True)
            j = 0
            def _skip(s):
                t = s.lstrip()
                return (t == "" or t.startswith("#") or t.startswith('\"\"\"') or t.startswith(\"'''\"))  # noqa
            while j < len(lines) and _skip(lines[j]):
                j += 1
            lines.insert(j, f"    global {varname}  # [AUTOPATCH]\n")
            body = "".join(lines)
            changed = True
            print(f"[AUTOPATCH] Injected 'global {varname}' into {m.group(1)}", flush=True)

        out.append(head + header + body)
        last = fn_end
    out.append(src[last:])
    src = "".join(out)

for v in ("POST_DELAY_SECONDS", "CURRENT_TARGET"):
    inject_global_for(v)

# 4) Ensure on_inline_click early ACK + queue append + fallback
m = re.search(r"def\s+on_inline_click\s*\(c\)\s*:\s*\n", src)
if m:
    start = m.end()
    m2 = re.search(r"\n\ndef\s+\w+\s*\(", src[start:])
    end = start + (m2.start() if m2 else len(src)-start)
    block = src[start:end]

    if "ae_cat_" in block and "⏳ שואב פריטים" not in block:
        block = re.sub(
            r'(if\s+data\.startswith\("ae_cat_"\)\s*:\s*\n)',
            r'\1        try:\n            bot.answer_callback_query(c.id, "⏳ שואב פריטים…")\n        except Exception:\n            pass\n',
            block, count=1
        )
        changed = True
        print("[AUTOPATCH] Added early ACK for ae_cat_", flush=True)

    if "_ae_fallback_fetch" not in src:
        helper = (
            "\n# ===== AliExpress Fallback (Scrape) injected by autopatch =====\n"
            "import json as _json, re as _re\n"
            "from urllib.parse import urlencode as _urlencode\n"
            "import requests as _req\n\n"
            "def _ae_fallback_fetch(query: str, limit: int = 8, ship_to: str = \"IL\"):\n"
            "    try:\n"
            "        params = {\"SearchText\": query, \"ShipCountry\": ship_to or \"IL\", \"SortType\": \"total_tranpro_desc\", \"g\": \"y\"}\n"
            "        url = \"https://www.aliexpress.com/wholesale?\" + _urlencode(params, doseq=True)\n"
            "        sess = _req.Session()\n"
            "        sess.headers.update({\n"
            "            \"User-Agent\": \"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari\",\n"
            "            \"Accept-Language\": \"he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7\",\n"
            "            \"Referer\": \"https://www.aliexpress.com/\"\n"
            "        })\n"
            "        cookies = {\"xman_us_f\": \"x_lan=he_IL&x_locale=he_IL&region=IL&b_locale=he_IL\"}\n"
            "        r = sess.get(url, timeout=(10,20), cookies=cookies)\n"
            "        r.raise_for_status()\n"
            "        html = r.text\n"
            "        items = []\n\n"
            "        for pat in [r\"window\\.__AER_DATA__\\s*=\\s*(\\{.*?\\});\", r\"window\\.runParams\\s*=\\s*(\\{.*?\\});\"]:\n"
            "            m = _re.search(pat, html, _re.S)\n"
            "            if not m:\n"
            "                continue\n"
            "            raw = m.group(1).strip().rstrip(\";\")\n"
            "            try:\n"
            "                data = _json.loads(raw)\n"
            "            except Exception:\n"
            "                continue\n\n"
            "            def _walk(o):\n"
            "                if isinstance(o, dict):\n"
            "                    pid = o.get(\"productId\") or o.get(\"product_id\") or o.get(\"itemId\") or o.get(\"item_id\") or o.get(\"id\")\n"
            "                    title = o.get(\"title\") or o.get(\"productTitle\") or o.get(\"product_title\")\n"
            "                    url = o.get(\"productDetailUrl\") or o.get(\"product_detail_url\") or o.get(\"productUrl\") or o.get(\"url\")\n"
            "                    img = o.get(\"productMainImageUrl\") or o.get(\"product_main_image_url\") or o.get(\"image\") or o.get(\"imageUrl\") or o.get(\"image_url\")\n"
            "                    price = o.get(\"appSalePrice\") or o.get(\"salePrice\") or o.get(\"app_sale_price\") or o.get(\"sale_price\") or o.get(\"price\")\n"
            "                    if pid and title and url:\n"
            "                        items.append({\"id\": str(pid), \"title\": str(title), \"url\": str(url), \"image_url\": img or \"\", \"price\": price or \"\", \"rating\": \"\", \"orders\": \"\"})\n"
            "                    for v in o.values():\n"
            "                        _walk(v)\n"
            "                elif isinstance(o, list):\n"
            "                    for it in o: _walk(it)\n\n"
            "        if not items:\n"
            "            for m in _re.finditer(r'href=\"(https://www\\.aliexpress\\.com/item/[^\\\"]+)\"[^>]*>([^<]{10,120})</a>', html):\n"
            "                url, title = m.group(1), m.group(2).strip()\n"
            "                items.append({\"id\": str(abs(hash(url))), \"title\": title, \"url\": url, \"image_url\": \"\", \"price\": \"\", \"rating\": \"\", \"orders\": \"\"})\n\n"
            "        uniq, out = set(), []\n"
            "        for it in items:\n"
            "            pid = it.get(\"id\")\n"
            "            if not pid or pid in uniq: continue\n"
            "            uniq.add(pid); out.append(it)\n"
            "            if len(out) >= limit: break\n"
            "        return out\n"
            "    except Exception as e:\n"
            "        print(f\"[AE][FALLBACK][ERR] {e}\", flush=True)\n"
            "        return []\n"
        )
        pos = src.find("def normalize_ae_product")
        src = (src + helper) if pos == -1 else (src[:pos] + helper + src[pos:])
        changed = True
        print("[AUTOPATCH] Injected fallback helper", flush=True)

    def patch_block(mo):
        head, blk = mo.group(1), mo.group(2)
        if "append_to_pending(" in blk:
            return mo.group(0)
        patched = (
            "        try:\n"
            "            prods = affiliate_product_query_by_category(category_id=cat, page_no=1, page_size=6, country='IL')\n"
            "        except Exception as _e:\n"
            "            print(f\"[AE][API][ERR] {str(_e)}\", flush=True)\n"
            "            prods = []\n"
            "        if not prods:\n"
            "            fb = _ae_fallback_fetch(cat, limit=8, ship_to='IL')\n"
            "            prods = [\n"
            "                {\n"
            "                    'id': it.get('id'), 'title': it.get('title'), 'url': it.get('url'),\n"
            "                    'image_url': it.get('image_url'), 'price': it.get('price'),\n"
            "                    'rating': it.get('rating'), 'orders': it.get('orders')\n"
            "                } for it in fb\n"
            "            ]\n"
            "        if not prods:\n"
            "            bot.send_message(c.message.chat.id, f\"ℹ️ לא נמצאו פריטים מתאימים כרגע עבור '{cat}'. נסה קטגוריה אחרת.\")\n"
            "        else:\n"
            "            rows = [normalize_ae_product(p) for p in prods]\n"
            "            append_to_pending(rows)\n"
            "            with FILE_LOCK:\n"
            "                pending_count = len(read_products(PENDING_CSV))\n"
            "            bot.send_message(c.message.chat.id, f\"✅ נוספו {len(rows)} מוצרים. כעת בתור: {pending_count}\")\n"
            "            safe_edit_message(bot, chat_id=chat_id, message=c.message,\n"
            "                              new_text=\"✅ השאיבה הושלמה. חזור לתפריט הראשי:\", new_markup=inline_menu())\n"
        )
        return head + patched

    block2 = re.sub(r'(if\s+data\.startswith\("ae_cat_"\)\s*:\s*\n)([\s\S]{0,2500})', patch_block, block, count=1)
    if block2 != block:
        block = block2
        changed = True
        print("[AUTOPATCH] Patched ae_cat_ block", flush=True)

    src = src[:start] + block + src[end:]
else:
    print("[AUTOPATCH][WARN] on_inline_click not found; skipped ACK/fallback", flush=True)

# 5) Add /status command (non-invasive) if missing
if "@bot.message_handler(commands=['status'])" not in src:
    status_handler = r'''
@bot.message_handler(commands=['status'])
def _autopatch_status(m):
    try:
        q = 0
        try:
            with FILE_LOCK:
                q = len(read_products(PENDING_CSV))
        except Exception:
            pass
        msg = f"📡 סטטוס בוט: פעיל\n📥 פריטים ממתינים: {q}\n⏱️ השהייה בין פוסטים: {POST_DELAY_SECONDS}s"
        bot.reply_to(m, msg)
    except Exception as e:
        try:
            bot.reply_to(m, f"סטטוס לא זמין: {e}")
        except Exception:
            pass
'''
    src += "\n" + status_handler
    changed = True
    print("[AUTOPATCH] Added /status handler", flush=True)

# 6) Ensure /on removes lock file
if "@bot.message_handler(commands=['on'])" in src and "Removed lock file data/bot.lock" not in src:
    src = re.sub(
        r"(@bot\.message_handler\(commands=\['on'\]\)\s*def\s+\w+\(m\)\s*:\s*\n)",
        r"\1    try:\n        import os\n        os.remove('data/bot.lock')\n        print('[AUTOPATCH] Removed lock file data/bot.lock')\n    except FileNotFoundError:\n        pass\n",
        src, count=1
    )
    changed = True
    print("[AUTOPATCH] Ensured /on removes lock file", flush=True)

if changed:
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print("[AUTOPATCH] Patched successfully", flush=True)
else:
    print("[AUTOPATCH] Nothing to patch", flush=True)
