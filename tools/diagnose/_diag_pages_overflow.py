"""找出各页面里把宽度撑大的控件（递归，按最小宽度排序）。

用法：python tools/diagnose/_diag_pages_overflow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

from blenderhub import detect, store  # noqa: E402
from blenderhub import theme as T  # noqa: E402
from blenderhub.main_window import MainWindow  # noqa: E402

T.load_fonts()
T.apply_theme(app, T.DARK)

cfg = store.load()
snap = detect.scan(install_dir=cfg["install_dir"], default_version=cfg["default_version"])

w = MainWindow()
w.show()
app.processEvents()
for page in w._pages:
    reload_ = getattr(page, "reload", None)
    if callable(reload_):
        reload_(snap)
app.processEvents()


def walk(widget, depth=0, path=""):
    out = []
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        name = child.objectName() or type(child).__name__
        p = f"{path}/{name}"
        out.append((child.minimumSizeHint().width(), depth, p, child))
        out += walk(child, depth + 1, p)
    return out


for idx, page in enumerate(w._pages):
    need = page.layout().minimumSize().width()
    print(f"\n=== page{idx} {type(page).__name__}  need={need}px ===")
    print("  直接子控件:")
    for child in page.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        print(f"    {child.minimumSizeHint().width():5d}px  "
              f"{child.objectName() or type(child).__name__}")
    items = walk(page)
    items.sort(key=lambda x: -x[0])
    shown = 0
    for mw, depth, p, _ in items:
        if mw < 600:
            break
        print(f"  {mw:5d}px  {'  ' * depth}{p[-70:]}")
        shown += 1
        if shown >= 12:
            break
