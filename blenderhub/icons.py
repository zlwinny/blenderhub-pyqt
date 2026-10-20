"""图标库。

图标与设计规范中的 SVG symbol 完全一致，按主题色栅格化为 QPixmap / QIcon。

尺寸约定（修复「图标尺寸不对」的关键）：
- pixmap() 返回逻辑尺寸精确为 size 的位图：按当前屏幕 devicePixelRatio 超采样
  栅格化（如 17px @1.25x → 21 设备像素，DPR=1.25），高分屏下保持锐利；
  直接 setPixmap() 到 QLabel 即可，不经过任何二次缩放；
- 栅格化必须给 QSvgRenderer.render() 显式传逻辑边界 QRectF(0,0,size,size)：
  不传 bounds 时它按「设备尺寸×DPR」渲染，DPR>1 的屏幕上图标会整体放大
  （125% 缩放下大 1.25 倍）并被画布边缘裁切，且 SVG width 属性会被忽略；
- icon() 仅供 QPushButton / QAction 使用，调用方必须显式 setIconSize()，
  与 pixmap 的逻辑尺寸保持一致，避免 QStyle 按默认值缩放。
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

try:
    from PyQt6.QtSvg import QSvgRenderer
    _HAS_SVG = True
except ImportError:  # pragma: no cover - 极端精简环境
    _HAS_SVG = False

# 描边类图标的默认笔触属性（对应设计规范 svg.i）
_STROKE = 'fill="none" stroke="{c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"'

_PATHS = {
    "home": '<path d="M4 11.4 12 4l8 7.4"/><path d="M6.5 10v9.5h11V10"/><path d="M10 19.5v-5h4v5"/>',
    "versions": '<path d="M12 4l8 4-8 4-8-4 8-4Z"/><path d="m4 12 8 4 8-4"/><path d="m4 16.4 8 4 8-4"/>',
    "folder": '<path d="M4 7a2 2 0 0 1 2-2h3.4l2 2H18a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z"/>',
    "puzzle": '<path d="M15.6 5.2a1.9 1.9 0 1 1 3.8 0v1.5h1.7a1.4 1.4 0 0 1 1.4 1.4v3.1h-1.6a1.9 1.9 0 1 0 0 3.8h1.6v3.1a1.4 1.4 0 0 1-1.4 1.4h-3.1v-1.6a1.9 1.9 0 1 0-3.8 0v1.6H7.1a1.4 1.4 0 0 1-1.4-1.4v-3.1h1.6a1.9 1.9 0 1 0 0-3.8H5.7V8.1A1.4 1.4 0 0 1 7.1 6.7h3.1V5.2"/>',
    "gear": '<circle cx="12" cy="12" r="3.1"/><path d="M12 2.8v2.6M12 18.6v2.6M4.6 7.4l2.2 1.4M17.2 15.2l2.2 1.4M19.4 16.6l-2.2-1.4M6.8 8.8l-2.2-1.4"/>',
    "search": '<circle cx="11" cy="11" r="6.4"/><path d="m15.8 15.8 4.2 4.2"/>',
    "bell": '<path d="M18.4 16.2H5.6l1.5-2V10a4.9 4.9 0 0 1 9.8 0v4.2l1.5 2Z"/><path d="M10.1 19.4a2.2 2.2 0 0 0 3.8 0"/>',
    "download": '<path d="M12 4v10m0 0 4-4m-4 4-4-4"/><path d="M5 17.6V19a1.4 1.4 0 0 0 1.4 1.4h11.2A1.4 1.4 0 0 0 19 19v-1.4"/>',
    "play": '<path d="M8.5 5.6 18.4 12 8.5 18.4V5.6Z" fill="{c}"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="m5 12.6 4.6 4.6L19 7.6"/>',
    "alert": '<path d="M12 4.2 21 19.4H3L12 4.2Z"/><path d="M12 10v4.2"/><path d="M12 17.1v.1"/>',
    "trash": '<path d="M4.8 7.4h14.4"/><path d="M9.4 7.4V5.6a1.2 1.2 0 0 1 1.2-1.2h2.8a1.2 1.2 0 0 1 1.2 1.2v1.8"/><path d="M6.6 7.4l.8 11.2a1.4 1.4 0 0 0 1.4 1.3h6.4a1.4 1.4 0 0 0 1.4-1.3l.8-11.2"/><path d="M10.4 10.6v6M13.6 10.6v6"/>',
    "star": '<path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.6-4.8 2.6.9-5.4-3.9-3.8 5.4-.8L12 4Z" fill="{c}"/>',
    "more": '<circle cx="5.5" cy="12" r="1.3" fill="{c}"/><circle cx="12" cy="12" r="1.3" fill="{c}"/><circle cx="18.5" cy="12" r="1.3" fill="{c}"/>',
    "update": '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 3.6v4.2h-4.2"/>',
    "x": '<path d="M6.4 6.4l11.2 11.2M17.6 6.4 6.4 17.6"/>',
    "min": '<path d="M5.5 12h13"/>',
    "max": '<rect x="5.5" y="5.5" width="13" height="13" rx="2"/>',
    "file": '<path d="M7 4.5h6.6L18 8.9V19a1.4 1.4 0 0 1-1.4 1.4H7A1.4 1.4 0 0 1 5.6 19V5.9A1.4 1.4 0 0 1 7 4.5Z"/><path d="M13.4 4.6v4.4h4.4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.4M12 18.8v2.4M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M2.8 12h2.4M18.8 12h2.4M4.9 19.1l1.7-1.7M17.4 6.6l1.7-1.7"/>',
    "moon": '<path d="M20.4 14.5A8.5 8.5 0 0 1 9.5 3.6a8.5 8.5 0 1 0 10.9 10.9Z"/>',
}

_LOGO = (
    '<rect x="1.5" y="1.5" width="21" height="21" rx="6.2" fill="url(#lg)"/>'
    '<g stroke="#1D140D" stroke-width="1.7" stroke-linecap="round" fill="none">'
    '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 3.4v4.4M12 16.2v4.4M4.3 7.7l3.8 2.2M15.9 14.1l3.8 2.2'
    'M19.7 7.7l-3.8 2.2M8.1 14.1l-3.8 2.2"/></g>'
    '<defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#F28E42"/><stop offset="1" stop-color="#B45600"/>'
    "</linearGradient></defs>"
)

_cache: dict[tuple[str, str, int, float], QPixmap] = {}


def _svg_bytes(name: str, color: str, px: int) -> bytes:
    if name == "logo":
        body = _LOGO
        stroke = ""
    else:
        body = _PATHS[name].replace("{c}", color)
        stroke = _STROKE.format(c=color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{px}" height="{px}" {stroke}>{body}</svg>'
    ).encode("utf-8")


def _screen_dpr() -> float:
    """主屏幕 devicePixelRatio；无 QApplication 时按 1.0 处理。"""
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            dpr = float(screen.devicePixelRatio())
            if dpr > 0:
                return dpr
    except Exception:
        pass
    return 1.0


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    """逻辑尺寸精确为 size 的图标位图（按屏幕 DPR 超采样，高分屏锐利）。"""
    dpr = _screen_dpr()
    key = (name, color, size, dpr)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    dev = max(1, int(round(size * dpr)))
    pm = QPixmap(dev, dev)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    if _HAS_SVG and (name in _PATHS or name == "logo"):
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 必须显式传逻辑边界：render(painter) 不传 bounds 时按「设备尺寸×DPR」渲染，
        # 在 125% 缩放屏幕上每个图标都会被放大 1.25 倍并遭画布边缘裁切；
        # 传 (0,0,size,size) 后由 painter 的 DPR 变换映射到设备像素，尺寸精确
        QSvgRenderer(_svg_bytes(name, color, size)).render(
            painter, QRectF(0, 0, size, size)
        )
        painter.end()
    _cache[key] = pm
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    """QIcon 版（QPushButton / QAction 用）；调用方须以 size 设置 iconSize。"""
    result = QIcon()
    result.addPixmap(pixmap(name, color, size))
    return result
