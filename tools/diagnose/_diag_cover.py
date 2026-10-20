"""诊断 3：本地插件目录内是否自带封面图片。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import detect, store  # noqa: E402

IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
LIMIT_FILES = 400

cfg = store.load()
snap = detect.scan(install_dir=cfg["install_dir"],
                   default_version=cfg["default_version"])

have = 0
for a in snap.addons:
    if not os.path.isdir(a.path):
        continue
    hits = []
    seen = 0
    for root, _dirs, files in os.walk(a.path):
        for f in files:
            seen += 1
            if seen > LIMIT_FILES:
                break
            if f.lower().endswith(IMG):
                try:
                    sz = os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
                if sz >= 12 * 1024:
                    hits.append((f, sz, os.path.join(root, f)))
        if seen > LIMIT_FILES:
            break
    if hits:
        have += 1
        hits.sort(key=lambda x: -x[1])
        print(f"{a.name[:38]:40s} {len(hits):3d} 张  e.g. {hits[0][0][:30]} "
              f"({hits[0][1] // 1024}KB)")
print(f"\n带封面图的本地插件：{have} / {len(snap.addons)}")
