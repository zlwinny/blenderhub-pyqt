"""诊断 2：对 MISS 的本地插件，在在线目录里做宽松候选搜索。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import detect, store  # noqa: E402
from blenderhub.addon_store import AddonStore, norm_name  # noqa: E402

s = AddonStore()
cfg = store.load()
snap = detect.scan(install_dir=cfg["install_dir"],
                   default_version=cfg["default_version"])

online_keys = [(a, norm_name(a.name)) for a in s.addons]
missed = []
for a in snap.addons:
    if s.find_local(a.name) is None:
        missed.append(a)
print(f"本地 {len(snap.addons)} 个，MISS {len(missed)} 个\n")

for a in missed:
    k = norm_name(a.name)
    cands = []
    for oa, ok in online_keys:
        if not ok:
            continue
        if len(k) < 3:
            continue
        if ok in k or k in ok:
            cands.append(("包含", oa.name))
        elif len(set(k) & set(ok)) >= max(3, int(len(k) * 0.6)) and len(ok) >= 3:
            cands.append(("字集", oa.name))
        if len(cands) >= 3:
            break
    head = f"{a.name}"[:44]
    if cands:
        print(f"{head:46s} -> {cands[0][0]} {cands[0][1][:40]}")
    else:
        print(f"{head:46s} -> 无候选")
