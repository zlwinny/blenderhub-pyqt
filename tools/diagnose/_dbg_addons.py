import os
import re

base = os.path.join(os.environ["APPDATA"], "Blender Foundation", "Blender")
for ver in ("4.2", "4.5", "5.2", "3.3"):
    d = os.path.join(base, ver, "scripts", "addons")
    if not os.path.isdir(d):
        continue
    for name in sorted(os.listdir(d)):
        init = os.path.join(d, name, "__init__.py")
        if not os.path.isfile(init):
            continue
        text = open(init, encoding="utf-8", errors="ignore").read(65536)
        m = re.search(r"bl_info\s*=\s*\{", text)
        if not m:
            print(f"{ver}/{name}: NO bl_info")
            continue
        depth, i, start = 0, m.end() - 1, m.end()
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = text[start:i]
        cat = re.search(r'"category"\s*:\s*"([^"]*)"', block)
        nm = re.search(r'"name"\s*:\s*"([^"]*)"', block)
        print(f"{ver}/{name}: name={nm.group(1) if nm else None!r} cat={cat.group(1) if cat else None!r}")

# 抽查 4.5 的 extensions
ext = os.path.join(base, "4.5", "extensions", "user_default")
if os.path.isdir(ext):
    for name in sorted(os.listdir(ext)):
        toml = os.path.join(ext, name, "blender_manifest.toml")
        if os.path.isfile(toml):
            t = open(toml, encoding="utf-8", errors="ignore").read(8192)
            nm = re.search(r'^name\s*=\s*"([^"]*)"', t, re.M)
            tg = re.search(r'^tagline\s*=\s*"([^"]*)"', t, re.M)
            print(f"ext/4.5/{name}: name={nm.group(1) if nm else None!r} tagline={tg.group(1)[:40] if tg else None!r}")
