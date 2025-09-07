# autopatch.py - hotfix for your existing main.py on Railway
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
    helper = r