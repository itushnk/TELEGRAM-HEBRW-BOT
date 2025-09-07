# autopatch.py - Railway hotfix autopatcher (v7-fix2)
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

# ---- 1) Fix unterminated f-string inside /status handler, if exists ----
status_fn = re.search(r"(\n@bot\.message_handler\(commands=\['status'\]\)\s*\ndef\s+_autopatch_status\(m\)\s*:\s*\n)([\s\S]{1,1200}?)(?=\n@bot\.message_handler|\n#|\n\Z)", src)
if status_fn:
    head, body = status_fn.group(1), status_fn.group(2)
    if ('msg = f"' in body and "\n" in body) or ('📥 פריטים ממתינים:' in body and '⏱️ השהייה בין פוסטים:' in body):
        safe_msg = (
            "    msg = (f\"📡 סטטוס בוט: פעיל\\n\"\n"
            "           f\"📥 פריטים ממתינים: {q}\\n\"\n"
            "           f\"⏱️ השהייה בין פוסטים: {POST_DELAY_SECONDS}s\")\n"
        )
        body2 = re.sub(r"\s*msg\s*=\s*f\s*\"[\s\S]*?\n\s*bot\.reply_to\(m,\s*msg\)", safe_msg + "    bot.reply_to(m, msg)", body, flags=re.S)
        if body2 == body:
            body2 = re.sub(r"\s*msg\s*=\s*f\s*\"[\s\S]*?\n", safe_msg, body, flags=re.S)
        if body2 != body:
            src = src.replace(head + body, head + body2)
            changed = True
            print("[AUTOPATCH] Rewrote /status msg into safe multi-line f-string", flush=True)
else:
    print("[AUTOPATCH] /status handler not found (nothing to fix)", flush=True)

# ---- 2) Escape stray newlines inside other f-strings (best-effort) ----
def fix_multiline_fstrings(s):
    def repl(m):
        inner = m.group(1).replace("\n", "\\n")
        return 'f"' + inner + '"'
    return re.sub(r'f\"([^\"]*\n[\s\S]*?)\"', repl, s)

before = src
src = fix_multiline_fstrings(src)
if src != before:
    changed = True
    print("[AUTOPATCH] Escaped stray newlines inside f-strings", flush=True)

# ---- 3) Write back ----
if changed:
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print("[AUTOPATCH] Patched successfully", flush=True)
else:
    print("[AUTOPATCH] Nothing to patch", flush=True)
