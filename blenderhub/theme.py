"""设计令牌与主题管理。

色值、字体、圆角、动效时长全部来自《BlenderHub UI 设计规范 v1.0》第 02 章：
OKLCH 定义后换算 HEX，深浅双主题均通过 WCAG AA 对比度核验。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui import QColor, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class Theme:
    name: str
    bg_app: str
    bg_surface: str
    bg_raised: str
    bg_hover: str
    bg_active: str
    border: str
    border_strong: str
    text_1: str
    text_2: str
    text_3: str
    brand: str
    brand_hover: str
    brand_active: str
    brand_text: str
    on_brand: str
    brand_tint: str
    success: str
    success_bg: str
    error: str
    error_bg: str
    info: str
    info_bg: str


DARK = Theme(
    name="dark",
    bg_app="#110C08", bg_surface="#1A1511", bg_raised="#241E1A",
    bg_hover="#2E2723", bg_active="#39312C",
    border="#2E2723", border_strong="#6A615B",
    text_1="#EDE6E2", text_2="#ACA29C", text_3="#9A908A",
    brand="#E48233", brand_hover="#F28E42", brand_active="#D67523",
    brand_text="#FF9B50", on_brand="#1D140D", brand_tint="#4B2000",
    success="#67D283", success_bg="#033816",
    error="#F66D67", error_bg="#4F1A18",
    info="#60C2FF", info_bg="#003151",
)

LIGHT = Theme(
    name="light",
    bg_app="#FBF5F2", bg_surface="#FFFDFB", bg_raised="#F6F0ED",
    bg_hover="#EFE8E3", bg_active="#E9E1DD",
    border="#EAE3DE", border_strong="#928A85",
    text_1="#291F18", text_2="#5E534B", text_3="#72665E",
    brand="#B45600", brand_hover="#9A3E00", brand_active="#8D3200",
    brand_text="#A74A00", on_brand="#FFFBF9", brand_tint="#FFE7D2",
    success="#1D7D3E", success_bg="#D1F2D7",
    error="#BA2B2E", error_bg="#FFE2DE",
    info="#006AAA", info_bg="#D7EFFF",
)

# 圆角：输入框 8 / 按钮与卡片 10 / 面板 14 / 窗口与浮层 18
RADIUS_SM = 8
RADIUS_MD = 10
RADIUS_LG = 14
RADIUS_XL = 18

# 动效四档时长（ms）
DUR_1 = 120
DUR_2 = 240
DUR_3 = 360
DUR_4 = 600


def ease_out() -> QEasingCurve:
    return QEasingCurve(QEasingCurve.Type.OutQuint)


def mix(c1: str, c2: str, ratio: float) -> str:
    """按比例混合两个 HEX 颜色，用于推导 QSS 悬停态。"""
    a, b = QColor(c1), QColor(c2)
    r = round(a.red() + (b.red() - a.red()) * ratio)
    g = round(a.green() + (b.green() - a.green()) * ratio)
    bl = round(a.blue() + (b.blue() - a.blue()) * ratio)
    return QColor(r, g, bl).name()


def rgba(c: str, alpha: float) -> str:
    """HEX → QSS rgba() 字符串，用于水印等半透明文字。"""
    col = QColor(c)
    return f"rgba({col.red()}, {col.green()}, {col.blue()}, {alpha})"


# ---------------------------------------------------------------- 当前主题
_current = DARK


def theme() -> Theme:
    return _current


def set_theme(t: Theme) -> None:
    global _current
    _current = t


def toggle_theme() -> Theme:
    set_theme(LIGHT if _current.name == "dark" else DARK)
    return _current


# ---------------------------------------------------------------- 字体
def load_fonts(font_dir: str | None = None) -> None:
    """注册 assets/fonts 下的 ttf/otf；目录不存在时回退系统字体。"""
    if font_dir is None:
        if getattr(sys, "frozen", False):
            base = sys._MEIPASS  # noqa: SLF001 - PyInstaller 冻结环境资源根目录
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        font_dir = os.path.join(base, "assets", "fonts")
    font_dir = os.path.abspath(font_dir)
    if not os.path.isdir(font_dir):
        return
    for fn in os.listdir(font_dir):
        if fn.lower().endswith((".ttf", ".otf")):
            QFontDatabase.addApplicationFile(os.path.join(font_dir, fn))


# ---------------------------------------------------------------- 应用主题
def _palette(t: Theme) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(t.bg_app))
    p.setColor(QPalette.ColorRole.WindowText, QColor(t.text_1))
    p.setColor(QPalette.ColorRole.Base, QColor(t.bg_surface))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t.bg_raised))
    p.setColor(QPalette.ColorRole.Text, QColor(t.text_1))
    p.setColor(QPalette.ColorRole.Button, QColor(t.bg_raised))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(t.text_1))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(t.text_3))
    p.setColor(QPalette.ColorRole.Highlight, QColor(t.brand))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(t.on_brand))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t.bg_raised))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(t.text_1))
    p.setColor(QPalette.ColorRole.Link, QColor(t.brand_text))
    dis = QPalette.ColorGroup.Disabled
    p.setColor(dis, QPalette.ColorRole.Text, QColor(t.text_3))
    p.setColor(dis, QPalette.ColorRole.ButtonText, QColor(t.text_3))
    p.setColor(dis, QPalette.ColorRole.WindowText, QColor(t.text_3))
    return p


def _qss(t: Theme) -> str:
    return f"""
    QToolTip {{
        background: {t.bg_raised}; color: {t.text_1};
        border: 1px solid {t.border}; border-radius: 6px; padding: 5px 8px;
    }}
    /* 文本角色：动态属性选择器，主题切换后随全局 QSS 自动换色 */
    QLabel {{ background: transparent; }}
    QLabel[textRole="h1"] {{
        color: {t.text_1}; font-size: 20px; font-weight: bold;
        font-family: "Space Grotesk", "Microsoft YaHei UI", "Noto Sans SC";
    }}
    QLabel[textRole="h2"] {{
        color: {t.text_1}; font-size: 15px; font-weight: bold;
        font-family: "Space Grotesk", "Microsoft YaHei UI", "Noto Sans SC";
    }}
    QLabel[textRole="sub"] {{ color: {t.text_3}; font-size: 12px; }}
    QLabel[textRole="body"] {{ color: {t.text_1}; font-size: 13px; }}
    QLabel[textRole="muted2"] {{ color: {t.text_2}; font-size: 12px; }}
    QLabel[textRole="muted"] {{ color: {t.text_3}; font-size: 11.5px; }}
    QLabel[textRole="mono"] {{
        color: {t.text_3}; font-size: 11.5px;
        font-family: "JetBrains Mono", "Cascadia Mono", Consolas;
    }}
    QLabel[textRole="brand"] {{ color: {t.brand_text}; font-size: 12px; }}
    QLabel[textRole="badge_tag"] {{
        color: {t.brand_text}; font-size: 8px; font-weight: bold;
    }}
    QLabel[textRole="stat"] {{
        color: {t.text_1}; font-size: 26px; font-weight: bold;
        font-family: "Space Grotesk", "Microsoft YaHei UI", "Noto Sans SC";
    }}
    QLabel[textRole="stat_label"] {{ color: {t.text_3}; font-size: 12px; }}
    QLabel[textRole="stat_extra"] {{ color: {t.brand_text}; font-size: 11.5px; }}
    QLabel[textRole="success"] {{ color: {t.success}; font-size: 11.5px; }}
    QLabel[textRole="info"] {{ color: {t.info}; font-size: 11.5px; }}
    QLabel[textRole="error"] {{ color: {t.error}; font-size: 11.5px; }}
    QLabel[textRole="hero_num"] {{
        color: {rgba(t.brand, 0.16)}; font-size: 88px; font-weight: bold;
        font-family: "Space Grotesk", "Microsoft YaHei UI", "Noto Sans SC";
    }}
    QLabel[textRole="hero_ver"] {{
        color: {t.text_1}; font-size: 40px; font-weight: bold;
        font-family: "Space Grotesk", "Microsoft YaHei UI", "Noto Sans SC";
    }}
    QLineEdit {{
        background: {t.bg_raised};
        border: 1px solid {t.border_strong};
        border-radius: {RADIUS_SM}px;
        padding: 0 10px;
        min-height: 30px;
        color: {t.text_1};
        selection-background-color: {t.brand};
        selection-color: {t.on_brand};
        font-family: "Noto Sans SC", "Microsoft YaHei";
        font-size: 13px;
    }}
    QLineEdit:hover {{ border-color: {t.text_3}; }}
    QLineEdit:focus {{ border-color: {t.brand}; }}
    QLineEdit:disabled {{
        color: {t.text_3}; border-color: {t.border}; background: {t.bg_surface};
    }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {t.bg_active}; border-radius: 5px; min-height: 36px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.border_strong}; }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none;
    }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {t.bg_active}; border-radius: 5px; min-width: 36px;
    }}
    *:focus {{ outline: none; }}
    /* Qt QSS 没有 :focus-visible；用高优先级的 :focus 边框提供键盘焦点 */
    QPushButton:focus {{ border: 2px solid {t.brand_text}; }}
    QLineEdit:focus {{ border: 2px solid {t.brand}; }}
    QFrame#navitem:focus, QFrame#mcard:focus, QFrame#card:focus {{
        border: 2px solid {t.brand};
    }}
    """


def apply_theme(app: QApplication, t: Theme | None = None) -> None:
    if t is not None:
        set_theme(t)
    t = _current
    app.setPalette(_palette(t))
    app.setStyleSheet(_qss(t))


def repolish_tree(root: QWidget) -> None:
    """主题切换后刷新样式；对实现了 refresh() 的控件同步重建图标。

    refresh() 可能重建子控件（如搜索框 action），遍历中跳过已删除对象。
    """
    widgets = [root, *root.findChildren(QWidget)]
    for w in widgets:
        st = w.style()
        st.unpolish(w)
        st.polish(w)
        w.update()
    for w in widgets:
        try:
            refresh = getattr(w, "refresh", None)
            if callable(refresh):
                refresh()
        except RuntimeError:
            continue
