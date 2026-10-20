"""聚焦实验：确认 QIcon 路径在 dpr=1.25 下是否丢失 DPR。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import theme as T
from blenderhub.icons import icon, pixmap

app = QApplication(sys.argv)
app.setStyle("Fusion")
T.apply_theme(app, T.DARK)

dpr = app.primaryScreen().devicePixelRatio()
print(f"dpr = {dpr}")

pm = pixmap("max", "#FF0000", 16)
print(f"icons.pixmap(max,16): dev={pm.width()}x{pm.height()} "
      f"logical={pm.deviceIndependentSize().width()} dpr={pm.devicePixelRatio()}")
img = pm.toImage()
xs = [x for y in range(img.height()) for x in range(img.width())
      if img.pixelColor(x, y).alpha() > 24]
print(f"  ink device bbox = {max(xs)-min(xs)+1} px（期望 ≈ {16*14.75/24*1.25:.1f}）")

ic = icon("max", "#FF0000", 16)
print(f"QIcon.actualSize(16x16) = {ic.actualSize(QSize(16, 16)).width()}")
got = ic.pixmap(QSize(16, 16))
print(f"QIcon.pixmap(16x16): dev={got.width()}x{got.height()} "
      f"logical={got.deviceIndependentSize().width()} dpr={got.devicePixelRatio()}")

b = QPushButton()
b.setStyleSheet("background: transparent; border: none;")
b.setFixedSize(40, 32)
b.setIcon(ic)
b.setIconSize(QSize(16, 16))
g = b.grab()
gi = g.toImage()
xs = [x for y in range(gi.height()) for x in range(gi.width())
      if gi.pixelColor(x, y).alpha() > 24]
print(f"按钮 grab: dev={g.width()}x{g.height()} dpr={g.devicePixelRatio()} "
      f"logical={g.deviceIndependentSize().width()}")
print(f"  ink bbox = {max(xs)-min(xs)+1} 设备像素")
print(f"  换算逻辑 = {(max(xs)-min(xs)+1)/g.devicePixelRatio():.1f} 逻辑像素"
      f"（期望 16 逻辑 → ink {16*14.75/24:.1f} 逻辑）")
