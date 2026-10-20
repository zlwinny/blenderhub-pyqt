"""精确实验：QSvgRenderer::render(painter) 对 width 属性与 pixmap DPR 的真实响应。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub.icons import _svg_bytes

app = QApplication(sys.argv)


def render_box(svg_px: int, pm_size: int, dpr: float):
    pm = QPixmap(pm_size, pm_size)
    pm.fill(Qt.GlobalColor.transparent)
    if dpr != 1.0:
        pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QSvgRenderer(_svg_bytes("max", "#FF0000", svg_px))
    print(f"    QSvgRenderer.defaultSize = {r.defaultSize().width()}x{r.defaultSize().height()}")
    r.render(p)
    p.end()
    img = pm.toImage()
    xs, ys = [], []
    for y in range(pm_size):
        for x in range(pm_size):
            if img.pixelColor(x, y).alpha() > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


print("max 图标 viewBox 24，几何墨迹跨度 4.625→19.375 单位")
print()
print("实验 1：pixmap 16×16 DPR=1.0，SVG width 分别取 16 / 24")
for svg_px in (16, 24):
    box = render_box(svg_px, 16, 1.0)
    print(f"  svg={svg_px} → box={box}（宽 {box[2]-box[0]+1}）")
print()
print("实验 2：pixmap 20×20 DPR=1.25，SVG width 分别取 16 / 20 / 24")
for svg_px in (16, 20, 24):
    box = render_box(svg_px, 20, 1.25)
    print(f"  svg={svg_px} → box={box}（宽 {box[2]-box[0]+1}）")
print()
print("实验 3：pixmap 20×20 DPR=1.0，SVG width 分别取 16 / 20")
for svg_px in (16, 20):
    box = render_box(svg_px, 20, 1.0)
    print(f"  svg={svg_px} → box={box}（宽 {box[2]-box[0]+1}）")
print()
print("实验 4：render(painter, bounds) 显式矩形，pixmap 20×20 DPR=1.25")
from PyQt6.QtCore import QRectF
for bounds_dev in (16, 20):
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(1.25)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QSvgRenderer(_svg_bytes("max", "#FF0000", 24))
    r.render(p, QRectF(0, 0, bounds_dev, bounds_dev))
    p.end()
    img = pm.toImage()
    xs = [x for y in range(20) for x in range(20) if img.pixelColor(x, y).alpha() > 24]
    print(f"  bounds={bounds_dev} → box=({min(xs)},{min(xs)},{max(xs)},{max(xs)})"
          f"（宽 {max(xs)-min(xs)+1}）")
