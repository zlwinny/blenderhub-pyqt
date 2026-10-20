"""渲染首页不同宽度截图，检查是否有内容被裁切。

用法：python tools/diagnose/_diag_home_render.py
输出：tools/diagnose/_out/home_<宽>.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from blenderhub import detect, store  # noqa: E402
from blenderhub import theme as T  # noqa: E402
from blenderhub.main_window import MainWindow  # noqa: E402

T.load_fonts()
T.apply_theme(app, T.DARK)

cfg = store.load()
snap = detect.scan(install_dir=cfg["install_dir"], default_version=cfg["default_version"])

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

w = MainWindow()
w.show()
app.processEvents()
for page in w._pages:
    reload_ = getattr(page, "reload", None)
    if callable(reload_):
        reload_(snap)

for width, height in ((1400, 900), (1100, 800), (972, 654), (960, 640), (820, 700)):
    w.resize(width, height)
    app.processEvents()
    home = w._pages[0]
    print(f"{width}px: page={home.width()}px  need={home.layout().minimumSize().width()}px  "
          f"narrow={home._narrow}  hscroll={w.stack.currentWidget().horizontalScrollBar().isVisible()}")
    w.grab().save(os.path.join(OUT, f"home_{width}.png"))

# 其余页面的最小宽度（>= 视口宽度就会右侧裁切）
print("\n各页面最小宽度（960 窗口可用约 770px）：")
for i, page in enumerate(w._pages):
    print(f"  page{i} {type(page).__name__:12s} need={page.layout().minimumSize().width()}px")

# ElidedLabel 自绘颜色是否与 QSS 一致
from PyQt6.QtGui import QPalette  # noqa: E402
from blenderhub.widgets import ElidedLabel, label  # noqa: E402

probe_e = ElidedLabel("x", "mono")
probe_l = label("x", "mono")
for pb in (probe_e, probe_l):
    pb.ensurePolished()
print("\ncolor: ElidedLabel", probe_e.palette().color(QPalette.ColorRole.WindowText).name(),
      "| QLabel", probe_l.palette().color(QPalette.ColorRole.WindowText).name(),
      "| theme.text_3", T.theme().text_3)

w.resize(972, 654)
app.processEvents()
for i, page in enumerate(w._pages):
    w.stack.setCurrentIndex(i)
    w.stack.widget(i).setGraphicsEffect(None)
    app.processEvents()
    w.grab().save(os.path.join(OUT, f"page{i}.png"))
    print(f"  saved page{i} {type(page).__name__}")

print("saved to", OUT)
