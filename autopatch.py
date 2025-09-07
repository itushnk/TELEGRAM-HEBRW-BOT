# autopatch.py - Railway hotfix autopatcher (v7-fix3)
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

# --- Heuristic fix: convert literal backslash-n sequences into real newlines inside function bodies ---
pat_fn = re.compile(r"(^def\s+\w+\s*\([^)]*\)\s*:\s*\n)", re.M)

pieces = []
last = 0
for m in pat_fn.finditer(src):
    fn_header = m.group(1)
    start = m.end()
    m2 = pat_fn.search(src, start)
    end = m2.start() if m2 else len(src)

    head = src[last:m.start()]
    body = src[start:end]

    # Only patch if suspicious "\n" sequences exist alongside indentation keywords
    suspicious = re.search(r"\\n\s+(return|except|finally|elif|else|with|for|if|try|bot\.|print\(|raise|pass)", body)
    if suspicious:
        body_fixed = re.sub(r"\\n(\s+)", r"\n\1", body)
        if body_fixed != body:
            body = body_fixed
            changed = True
            print("[AUTOPATCH] Normalized literal \\n to newline inside a function block", flush=True)

    pieces.append(head + fn_header + body)
    last = end
pieces.append(src[last:])
src = "".join(pieces)

# Also fix top-level occurrences like `)\n            return` that might appear outside any def (rare)
before = src
src = re.sub(r"\)\s*\\n(\s+)(return|raise|pass)", r")\n\1\2", src)
if src != before:
    changed = True
    print("[AUTOPATCH] Normalized top-level literal \\n patterns", flush=True)

if changed:
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print("[AUTOPATCH] Patched successfully", flush=True)
else:
    print("[AUTOPATCH] Nothing to patch", flush=True)
