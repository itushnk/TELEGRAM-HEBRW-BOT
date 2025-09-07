# autopatch.py - hotfix for your existing main.py on Railway (v6)
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

def insert_module_default(name, default):
    global changed, src
    if name not in src[:3000]:
        m = re.search(r"^(?:from\s+\S+\s+import\s+\S+|import\s+\S+).*$", src, re.M)
        insert_at = m.end() if m else 0
        ins = f"\n{name} = {default}  # [AUTOPATCH]\n"
        src = src[:insert_at] + ins + src[insert_at:]
        changed = True
        print(f"[AUTOPATCH] Added {name} default", flush=True)

insert_module_default("POST_DELAY_SECONDS", "int(os.getenv('POST_DELAY_SECONDS', '900'))")

# ---- Helper: robust function patcher to add 'global VAR' if function assigns to it
def add_global_if_assigned(varname):
    global changed, src
    pat_def = re.compile(r"def\s+(\w+)\s*\([^)]*\)\s*:\s*\n", re.M)
    pos = 0
    out = []
    last = 0
    for m in pat_def.finditer(src):
        fn_start = m.end()
        m2 = pat_def.search(src, fn_start)
        fn_end = m2.start() if m2 else len(src)
        head = src[last:m.start()]
        out.append(head)
        fn_header = src[m.start():fn_start]
        body = src[fn_start:fn_end]
        assigns = re.search(rf"(?m)^\s*{re.escape(varname)}\s*[\+\-\*/]?=", body) is not None
        has_global = re.search(rf"(?m)^\s*global\s+[^\n]*\b{re.escape(varname)}\b", body) is not None
        if assigns and not has_global:
            lines = body.splitlines(True)
            j = 0
            def _skip(s):
                t = s.lstrip()
                return (t == "" or t.startswith("#") or t.startswith('"'*3) or t.startswith("'"*3))
            while j < len(lines) and _skip(lines[j]):
                j += 1
            lines.insert(j, f"    global {varname}  # [AUTOPATCH]\n")
            body = "".join(lines)
            changed = True
            print(f"[AUTOPATCH] Injected 'global {varname}' into function {m.group(1)}", flush=True)
        out.append(fn_header + body)
        last = fn_end
    out.append(src[last:])
    src = "".join(out)

for var in ("POST_DELAY_SECONDS", "CURRENT_TARGET"):
    add_global_if_assigned(var)

# ---- Add early ACK & fallback logic into on_inline_click (idempotent)
def ensure_on_inline_click_patches():
    global src, changed
    m = re.search(r"def\s+on_inline_click\s*\(c\)\s*:\s*\n", src)
    if not m:
        print("[AUTOPATCH][WARN] on_inline_click not found; skipped ACK/fallback", flush=True)
        return
    start = m.end()
    m2 = re.search(r"\n\ndef\s+\w+\s*\(", src[start:])
    end = start + (m2.start() if m2 else len(src) - start)
    body = src[start:end]

    if "ae_cat_" in body and "⏳ שואב פריטים" not in body:
        body = re.sub(
            r'(if\s+data\.startswith\("ae_cat_"\)\s*:\s*\n)',
            r'\1        try:\n            bot.answer_callback_query(c.id, "⏳ שואב פריטים…")\n        except Exception:\n            pass\n',
            body, count=1
        )
        changed = True
        print("[AUTOPATCH] Added early ACK for ae_cat_", flush=True)

    if "_ae_fallback_fetch" not in src:
        helper = r