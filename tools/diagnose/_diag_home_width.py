"""首页水平溢出定位：测量各区块的 minimumSize，找出撑宽来源。

用法：python tools/diagnose/_diag_home_width.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from blenderhub import detect, store  # noqa: E402
from blenderhub import theme as T  # noqa: E402
from blenderhub.pages import HomePage  # noqa: E402

T.load_fonts()
T.apply_theme(app, T.DARK)

cfg = store.load()
snap = detect.scan(install_dir=cfg["install_dir"], default_version=cfg["default_version"])
print(f"scan: inst={len(snap.installs)} proj={len(snap.projects)} addon={len(snap.addons)}")

w = HomePage()
w.reload(snap)
w.ensurePolished()
w.layout().activate()

print(f"\nHome  minimumSize={w.layout().minimumSize().width()}px  sizeHint={w.sizeHint().width()}px")

for i, (card, idx) in enumerate(w._stat_cards):
    print(f"  stat{i} (page {idx})  min={card.minimumSizeHint().width()}px  hint={card.sizeHint().width()}px"
          f"  num={card._num_lb.text()!r} extra={card._extra_lb.text() if card._extra_lb else ''!r}")

for name, lay in (("recent", w._recent_lay), ("feed", w._feed_lay)):
    cardw = lay.parentWidget()
    print(f"  {name} card  min={cardw.minimumSizeHint().width()}px  hint={cardw.sizeHint().width()}px")

for row, pname, ppath in w._recent_rows[:5]:
    print(f"  row {pname[:16]!r}  min={row.minimumSizeHint().width()}px  hint={row.sizeHint().width()}px"
          f"  path={ppath[-46:]!r}")

hero = w._hero
print(f"  hero  min={hero.minimumSizeHint().width()}px  hint={hero.sizeHint().width()}px")

# 模拟截图里的可用宽度（972 窗口 - 190 侧栏 - 40 边距）
for avail in (940, 800, 742, 640):
    w.resize(avail, 900)
    w.layout().activate()
    over = w.layout().minimumSize().width() - avail
    print(f"  avail={avail:4d}px -> 需要 {w.layout().minimumSize().width()}px  "
          f"{'溢出 +%d' % over if over > 0 else 'OK'}")
