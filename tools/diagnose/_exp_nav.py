"""诊断：导航图标 label / pixmap / grab 三者的尺寸关系。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import theme as T
from blenderhub import icons as I
from blenderhub.main_window import MainWindow

app = QApplication(sys.argv)
app.setStyle("Fusion")
T.load_fonts()
T.apply_theme(app, T.DARK)
print(f"screen dpr = {app.primaryScreen().devicePixelRatio()}")

win = MainWindow()
win.resize(1280, 860)
win.show()
app.processEvents()

nav_ic = win._sidebar._items[0]._ic


def ink(pm: QPixmap):
    img = pm.toImage()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys), max(xs) - min(xs) + 1)


pm = nav_ic.pixmap()
print(f"label.pixmap(): dev={pm.width()}x{pm.height()} dpr={pm.devicePixelRatio()} "
      f"logical={pm.deviceIndependentSize().width()} ink={ink(pm)}")
print(f"label.size(): {nav_ic.width()}x{nav_ic.height()} 逻辑")

g = nav_ic.grab()
print(f"label.grab(): dev={g.width()}x{g.height()} dpr={g.devicePixelRatio()} "
      f"logical={g.deviceIndependentSize().width()} ink={ink(g)}")

direct = I.pixmap("home", T.theme().text_2, 17)
print(f"direct pixmap(home,text_2,17): dev={direct.width()} dpr={direct.devicePixelRatio()} "
      f"logical={direct.deviceIndependentSize().width()} ink={ink(direct)}")

# 理论值：home 几何跨度 15.2/24
print(f"理论墨迹（17 逻辑）: {15.2/24*17:.2f} 逻辑 = {15.2/24*17*1.25:.2f} 设备px")

# 对照：dpr=1.0 时是否一致
os.environ["QT_SCALE_FACTOR"] = "1.0"
