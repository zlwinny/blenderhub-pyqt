"""t2 验证：目录缓存落盘 + 缩略图下载持久化 + 本地插件名匹配。"""
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LOCALAPPDATA"] = os.path.join(PROJ, "_t2_cache")
os.makedirs(os.environ["LOCALAPPDATA"], exist_ok=True)
sys.path.insert(0, PROJ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from blenderhub import detect, store  # noqa: E402
from blenderhub.addon_store import (  # noqa: E402
    AddonStore, cache_root, norm_name,
)

root = cache_root()
print("cache root:", root)

assert norm_name("Zen UV 4.2.1") == norm_name("ZenUV"), norm_name("Zen UV 4.2.1")
assert norm_name("Batch Operations™") == "batchoperations"
print("norm_name OK")

st = AddonStore()
if len(st.addons) < 100:
    ok, msg = st._crawl(0)
    print("crawl:", ok, msg)
print("catalog addons:", len(st.addons),
      "with thumb_url:", sum(1 for a in st.addons if a.thumbnail_url))

# -- 全量下载（前 MAX_THUMBS 条）
st._download_thumbs()
thumbs_dir = os.path.join(root, "thumbnails")
files = [f for f in os.listdir(thumbs_dir)
         if os.path.isfile(os.path.join(thumbs_dir, f))]
nz = [f for f in files
      if os.path.getsize(os.path.join(thumbs_dir, f)) > 0]
print("thumbs files: %d (non-zero %d) | with thumb_path: %d"
      % (len(files), len(nz), sum(1 for a in st.addons if a.thumb_path)))

# -- 重新实例化验证持久化
st2 = AddonStore()
valid = sum(1 for a in st2.addons
            if a.thumb_path and os.path.isfile(a.thumb_path))
print("reload from disk: addons=%d valid thumb_path=%d" % (len(st2.addons), valid))

# -- 本地插件匹配（复刻页面 _apply_local_thumbs 逻辑）
data = store.load()
snap = detect.scan(install_dir=data["install_dir"],
                   default_version=data["default_version"])
cached, pending = [], []
for a in snap.addons:
    hit = st2.find_local(a.name)
    if hit is None:
        continue
    tp = st2.thumb_pixmap_path(hit)
    if tp:
        cached.append((a.name, os.path.basename(tp)))
    elif hit.thumbnail_url:
        pending.append(hit)
for name, tp in cached:
    print("MATCH(cached):", name, "->", tp)
print("local addons: %d | matched-cached: %d | pending-download: %d"
      % (len(snap.addons), len(cached), len(pending)))

# -- 按需下载 pending 并复验
if pending:
    st2._download_thumbs(pending)
    ok_now = sum(1 for h in pending if st2.thumb_pixmap_path(h))
    print("after on-demand download: %d/%d got thumbs" % (ok_now, len(pending)))
    for h in pending:
        if st2.thumb_pixmap_path(h):
            print("MATCH(ondemand):", h.name)
