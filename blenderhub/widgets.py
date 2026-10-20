"""通用组件库 —— 与设计规范第 03 / 04 章一一对应。

约定：
- 所有容器类组件继承 QFrame 并置 NoFrame，保证 QSS 背景可靠渲染；
- 需要随主题换色的组件实现 refresh()，由 theme.repolish_tree() 统一调用；
- 标签一律用 label() 助手设置 textRole 动态属性，颜色走全局 QSS 自动换肤。
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QEnterEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .icons import icon, pixmap


def label(text: str = "", text_role: str = "body", parent=None) -> QLabel:
    """带文本角色的标签；颜色/字号由全局 QSS 按 textRole 提供。"""
    lb = QLabel(text, parent)
    lb.setProperty("textRole", text_role)
    return lb


class ElidedLabel(QLabel):
    """单行省略标签：宽度不足时按 mode 省略，完整文本进 tooltip。

    普通 QLabel 的 minimumSizeHint 是整段文本宽度，一个长路径就能把整页撑宽
    （QScrollArea 关闭了水平滚动 → 右侧内容被直接裁掉）。这里水平策略设为
    Ignored 并自绘省略号，布局可自由收缩，颜色仍取 QSS 写入的 palette。
    """

    def __init__(
        self,
        text: str = "",
        text_role: str = "body",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent=None,
    ):
        super().__init__(text, parent)
        self.setProperty("textRole", text_role)
        self._mode = mode
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt 命名)
        self._full = text
        self.setToolTip(text)
        super().setText(text)
        self.update()

    def minimumSizeHint(self):
        # 宽度可以压到 0（配合 Preferred 策略时仍能省略），高度保持字体行高
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        p.setFont(self.font())
        rect = self.contentsRect()
        txt = self.fontMetrics().elidedText(self._full, self._mode, rect.width())
        p.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), txt)
        p.end()


def hline(parent=None) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


# ---------------------------------------------------------------- 按钮
class Button(QPushButton):
    """设计规范按钮：primary / secondary / ghost / danger × md / sm。

    加载态用旋转弧 + 文字隐藏模拟 spinner（QSS 无原生过渡，色彩切换为瞬时）。
    """

    def __init__(
        self,
        text: str = "",
        variant: str = "primary",
        size: str = "md",
        icon_name: str | None = None,
        parent=None,
    ):
        super().__init__(text, parent)
        self._variant = variant
        self._size = size
        self._icon_name = icon_name
        self._busy = False
        self._text_backup = text
        self._angle = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.refresh()

    # -- 外观 --------------------------------------------------------
    def refresh(self) -> None:
        t = T.theme()
        if self._icon_name:
            n = 16 if self._size == "md" else 14
            self.setIcon(icon(self._icon_name, self._icon_color(), n))
            self.setIconSize(QSize(n, n))
        self.setStyleSheet(self._qss(t))

    def _icon_color(self) -> str:
        t = T.theme()
        if self._variant == "primary":
            return t.on_brand
        if self._variant == "danger":
            return t.error
        if self._variant == "ghost":
            return t.text_2
        return t.text_1

    def set_variant(self, variant: str) -> None:
        if variant == self._variant:
            return
        self._variant = variant
        self.refresh()

    def _qss(self, t: T.Theme) -> str:
        md = self._size == "md"
        r = T.RADIUS_MD if md else T.RADIUS_SM
        pad = 14 if md else 10
        base = (
            f"QPushButton {{ border: none; border-radius: {r}px; padding: 0 {pad}px; "
            f'min-height: {32 if md else 26}px; font-size: {13 if md else 12.5}px; '
            f'font-family: "Noto Sans SC", "Microsoft YaHei"; text-align: center; }}'
            "QPushButton:disabled { color: " + t.text_3 + "; }"
            f"QPushButton:focus {{ border: 2px solid {t.brand_text}; }}"
        )
        if self._variant == "primary":
            return base + (
                f"QPushButton {{ background: {t.brand}; color: {t.on_brand}; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {t.brand_hover}; }}"
                f"QPushButton:pressed {{ background: {t.brand_active}; }}"
                f"QPushButton:disabled {{ background: {t.bg_raised}; }}"
                f"QPushButton:focus {{ border: 2px solid {t.on_brand}; }}"
            )
        if self._variant == "danger":
            return base + (
                f"QPushButton {{ background: {t.error_bg}; color: {t.error}; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {T.mix(t.error_bg, t.error, 0.18)}; }}"
                f"QPushButton:pressed {{ background: {T.mix(t.error_bg, t.error, 0.32)}; }}"
                f"QPushButton:disabled {{ background: {t.bg_raised}; }}"
            )
        if self._variant == "ghost":
            return base + (
                f"QPushButton {{ background: transparent; color: {t.text_2}; }}"
                f"QPushButton:hover {{ background: {t.bg_hover}; color: {t.text_1}; }}"
                f"QPushButton:pressed {{ background: {t.bg_active}; }}"
            )
        return base + (
            f"QPushButton {{ background: {t.bg_raised}; color: {t.text_1}; "
            f"border: 1px solid {t.border_strong}; }}"
            f"QPushButton:hover {{ background: {t.bg_hover}; }}"
            f"QPushButton:pressed {{ background: {t.bg_active}; }}"
            f"QPushButton:disabled {{ border-color: {t.border}; }}"
        )

    # -- 加载态 ------------------------------------------------------
    def set_busy(self, on: bool) -> None:
        if on == self._busy:
            return
        self._busy = on
        if on:
            self._text_backup = self.text()
            self.setText("")
            self._timer.start(80)
        else:
            self.setText(self._text_backup)
            self._timer.stop()
        self.setEnabled(not on)
        self.update()

    def _advance(self) -> None:
        self._angle = (self._angle + 45) % 360
        self.update()

    def paintEvent(self, e):
        if not self._busy:
            return super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = T.theme()
        side = min(self.width(), self.height()) - 14
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        color = QColor(t.on_brand if self._variant == "primary" else t.text_2)
        qp = QPen(color)
        qp.setWidthF(2.2)
        qp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(qp)
        p.drawArc(rect, self._angle * 16, 270 * 16)
        p.end()


class IconButton(Button):
    """纯图标按钮（bell / more 等），方形。"""

    def __init__(self, icon_name: str, variant: str = "ghost", parent=None, tip: str = ""):
        super().__init__("", variant=variant, size="sm", icon_name=icon_name, parent=parent)
        self.setFixedSize(28, 28)
        if tip:
            self.setToolTip(tip)


# ---------------------------------------------------------------- 容器
class Card(QFrame):
    """卡片容器：bg_raised + 1px 描边 + 14px 圆角。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("card")
        Card.refresh(self)

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#card {{ background: {t.bg_raised}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_LG}px; }}"
            f"#card:focus {{ border: 2px solid {t.brand}; }}"
        )


class StatCard(Card):
    """统计卡：大数字 + 标签 + 辅助行。可点击（跳转相关页面）。"""

    clicked = pyqtSignal()

    def __init__(self, number: str, text: str, extra: str = "", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)
        self._num_lb = label(number, "stat")
        lay.addWidget(self._num_lb)
        self._text_lb = ElidedLabel(text, "stat_label")
        lay.addWidget(self._text_lb)
        self._extra_lb = ElidedLabel(extra, "stat_extra") if extra else None
        if self._extra_lb is not None:
            lay.addWidget(self._extra_lb)

    def set_data(self, number: str, text: str, extra: str = "") -> None:
        self._num_lb.setText(number)
        self._text_lb.setText(text)
        if self._extra_lb is None and extra:
            self._extra_lb = ElidedLabel(extra, "stat_extra")
            self.layout().addWidget(self._extra_lb)
        elif self._extra_lb is not None:
            self._extra_lb.setText(extra)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class Chip(QFrame):
    """徽标：brand / success / info / neutral，可选呼吸圆点。"""

    def __init__(self, text: str, variant: str = "neutral", live: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("chip")
        self._variant = variant
        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 2, 7, 2)
        lay.setSpacing(4)
        if live:
            lay.addWidget(LiveDot(T.theme().success))
        # Ignored 策略会让 QHBoxLayout 忽略 label 的 sizeHint，徽标会被压成
        # 只剩左右内边距的色块；改 Preferred + 最小宽度 0：优先显示完整文字，
        # 空间不足时再省略。
        self._lb = ElidedLabel(text, "body")
        self._lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._lb.setMinimumWidth(0)
        self._lb.setStyleSheet("font-size: 10.5px; font-weight: bold;")
        lay.addWidget(self._lb)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        bg, fg = {
            "brand": (t.brand_tint, t.brand_text),
            "success": (t.success_bg, t.success),
            "info": (t.info_bg, t.info),
            "neutral": (t.bg_hover, t.text_2),
        }[self._variant]
        self.setStyleSheet(
            f"#chip {{ background: {bg}; border-radius: 999px; }}"
        )
        self._lb.setStyleSheet(f"color: {fg}; font-size: 10.5px; font-weight: bold;")

    def setText(self, text: str) -> None:
        self._lb.setText(text)

    def text(self) -> str:
        return self._lb.text()


class LiveDot(QWidget):
    """呼吸圆点（运行中状态），透明度 1.6s 循环。"""

    def __init__(self, color: str, size: int = 6, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(1600)
        anim.setKeyValueAt(0, 1.0)
        anim.setKeyValueAt(0.5, 0.35)
        anim.setKeyValueAt(1, 1.0)
        anim.setLoopCount(-1)
        anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        p.end()


# ---------------------------------------------------------------- 输入
class SearchBox(QLineEdit):
    """搜索框：内嵌 16px 搜索图标（QLabel 绝对定位，尺寸精确可控）+ 清除按钮。

    不用 QAction 图标：其渲染尺寸由 QStyle 隐式决定，无法与设计规范的 16px 对齐。
    """

    def __init__(self, placeholder: str = "搜索", width: int = 250, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumWidth(width)
        self.setClearButtonEnabled(True)
        self._ic = QLabel(self)
        self._ic.setFixedSize(16, 16)
        self._ic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setTextMargins(24, 0, 0, 0)
        self.refresh()

    def refresh(self) -> None:
        self._ic.setPixmap(pixmap("search", T.theme().text_3, 16))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ic.move(10, (self.height() - 16) // 2)


class Toggle(QWidget):
    """开关：40×22 胶囊，滑块 240ms OutQuint 滑动。"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._pos = 20.0 if checked else 2.0

    def _get_knob_x(self) -> float:
        return self._pos

    def _set_knob_x(self, v: float) -> None:
        self._pos = v
        self.update()

    knobX = pyqtProperty(float, fget=_get_knob_x, fset=_set_knob_x)

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, on: bool, animate: bool = True) -> None:
        if on == self._checked:
            return
        self._checked = on
        if animate:
            anim = QPropertyAnimation(self, b"knobX", self)
            anim.setDuration(T.DUR_2)
            anim.setEasingCurve(T.ease_out())
            anim.setStartValue(self._pos)
            anim.setEndValue(20.0 if on else 2.0)
            anim.start()
        else:
            self._pos = 20.0 if on else 2.0
            self.update()
        self.toggled.emit(self._checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = T.theme()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.brand if self._checked else t.bg_active))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 11, 11)
        p.setBrush(QColor(t.on_brand if self._checked else t.text_2))
        p.drawEllipse(QRectF(self._pos, 2, 18, 18))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_checked(not self._checked)
        super().mousePressEvent(e)


# ---------------------------------------------------------------- 列表行
class VersionBadge(QFrame):
    """版本徽章：46×46，大数字 + LTS/BETA 小标。"""

    def __init__(self, number: str, tag: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("vbadge")
        self.setFixedSize(40, 40)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 3)
        lay.setSpacing(0)
        num = label(number, "h2")
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(num)
        self._tag = label(tag, "badge_tag")
        self._tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._tag)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#vbadge {{ background: {t.bg_hover}; border: 1px solid {t.border}; "
            f"border-radius: 11px; }}"
        )


class VersionRow(QFrame):
    """版本管理列表行：徽章 + 信息（含徽标行）+ 体积 + 操作。"""

    def __init__(
        self,
        number: str,
        tag: str,
        title: str,
        chips: list[tuple[str, str, bool]],
        path: str,
        size: str,
        actions: list[QWidget],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("vrow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(10)
        lay.addWidget(VersionBadge(number, tag))

        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(5)
        title_row.addWidget(ElidedLabel(title, "h2"))
        for text, variant, live in chips:
            title_row.addWidget(Chip(text, variant, live))
        title_row.addStretch(1)
        info.addLayout(title_row)
        info.addWidget(ElidedLabel(path, "mono", Qt.TextElideMode.ElideMiddle))
        lay.addLayout(info, 1)

        lay.addWidget(label(size, "muted2"))
        acts = QHBoxLayout()
        acts.setSpacing(5)
        for w in actions:
            acts.addWidget(w)
        lay.addLayout(acts)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#vrow {{ background: transparent; border-radius: {T.RADIUS_MD}px; }}"
            f"#vrow:hover {{ background: {t.bg_hover}; }}"
        )


# ---------------------------------------------------------------- 媒体卡
THUMB_GRADIENTS = {
    "a": ("#F0A03C", "#B45600"),
    "b": ("#6FA8DC", "#2F5F8F"),
    "c": ("#E58E6E", "#8F3B2E"),
    "d": ("#8FB98F", "#3F6B3F"),
    "e": ("#B78FD6", "#5E3B8F"),
    "f": ("#D6C06E", "#8F7B2E"),
}


def _rounded_cover(pm: QPixmap, w: int, h: int, radius: int,
                   top_only: bool = False) -> QPixmap:
    """把位图按 cover 方式缩放铺满 w×h（居中裁剪）并裁出圆角。

    top_only 为 True 时只裁上侧两角（配合卡片顶部缩略区）。
    尺寸均为设备像素；调用方负责 setDevicePixelRatio。
    """
    if pm.isNull() or w <= 0 or h <= 0:
        return QPixmap()
    scaled = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    cropped = scaled.copy(x, y, min(w, scaled.width()), min(h, scaled.height()))
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    r = min(radius, w / 2, h / 2)
    if top_only:
        path.moveTo(0, h)
        path.lineTo(0, r)
        path.quadTo(0, 0, r, 0)
        path.lineTo(w - r, 0)
        path.quadTo(w, 0, w, r)
        path.lineTo(w, h)
    else:
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
    path.closeSubpath()
    p.setClipPath(path)
    p.drawPixmap(0, 0, cropped)
    p.end()
    return out


class _ElideLabel(QLabel):
    """单行标签：宽度不足时右侧省略号截断，用于卡片标题 / 路径 / meta。

    sizeHint / minimumSizeHint 必须返回极小宽度，否则布局会按完整文本
    宽度计算卡片最小宽度，四列网格放不下只能看到两列。
    """

    def __init__(self, text: str, text_role: str = "body", parent=None):
        super().__init__(text, parent)
        self.setProperty("textRole", text_role)
        self._full = text

    def sizeHint(self):
        return QSize(24, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(24, super().minimumSizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_elide()

    def _sync_elide(self) -> None:
        if not self._full:
            return
        elided = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(12, self.width()))
        if elided != self.text():
            self.setText(elided)


class MediaCard(Card):
    """项目 / 插件卡：大图缩略区 + 标题 + 作者 + 标签 + 底部信息与操作。

    布局参照插件市场卡片：封面图通栏顶部，下方标题（超长省略）、
    作者行、标签行、底部左侧 meta 文字、右侧操作按钮。
    """

    clicked = pyqtSignal()

    def __init__(
        self,
        name: str,
        sub: str,
        meta_left: str,
        action: QWidget,
        thumb: str = "a",
        thumb_icon: str = "versions",
        star: bool = False,
        chips: list[tuple[str, str]] | None = None,
        selectable: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("mcard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        c1, c2 = THUMB_GRADIENTS[thumb]
        self._thumb = QLabel(self)
        self._thumb.setFixedHeight(150)
        # 缩略图是「按当前宽度重绘」的像素图：若沿用 QLabel 默认策略，
        # pixmap 尺寸会变成 minimumSizeHint，把卡片/整页最小宽度锁死，
        # 窗口缩小时右侧直接被裁掉。
        self._thumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._thumb.setMinimumWidth(0)
        self._thumb.installEventFilter(self)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_icon = thumb_icon
        self._thumb_c = (c1, c2)
        self._thumb_src: QPixmap | None = None
        self._selectable = selectable
        self._selected = False
        self._check = QLabel(self._thumb)
        self._check.setFixedSize(20, 20)
        self._check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 勾选框本身不吞鼠标事件：点击穿透到卡片，由页面按选择模式处理
        self._check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._check.move(10, 10)
        self._check.setVisible(selectable)
        if star:
            self._star = QLabel(self._thumb)
            self._star.setFixedSize(20, 20)
            self._star.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._place_star()
        else:
            self._star = None

        body = QWidget(self)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(4)
        bl.addWidget(_ElideLabel(name, "h2"))
        if sub:
            bl.addWidget(_ElideLabel(sub, "muted"))
        if chips:
            crow = QHBoxLayout()
            crow.setSpacing(5)
            for text, variant in chips:
                crow.addWidget(Chip(text, variant))
            crow.addStretch(1)
            bl.addLayout(crow)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        if meta_left:
            # 省略号标签 sizeHint 极小，必须给 stretch 权重才能吸收剩余宽度
            meta.addWidget(_ElideLabel(meta_left, "muted2"), 1)
        else:
            meta.addStretch(1)
        meta.addWidget(action)
        bl.addLayout(meta)
        lay.addWidget(self._thumb)
        lay.addWidget(body)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#mcard {{ background: {t.bg_raised}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_LG}px; }}"
            f'#mcard[selected="true"] {{ border: 1px solid {t.brand}; }}'
            f"#mcard:focus {{ border: 2px solid {t.brand}; }}"
        )
        if self._star is not None:
            self._star.setStyleSheet(
                f"background: {T.rgba('#000000', 0.45)}; border-radius: 10px;"
            )
            self._star.setPixmap(pixmap("star", "#FFD28A", 12))
        self._sync_check()
        if self._thumb_src is not None:
            self._render_thumb()
            return
        c1, c2 = self._thumb_c
        self._thumb.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {c1}, stop:1 {c2}); "
            f"border-top-left-radius: {T.RADIUS_LG - 1}px; "
            f"border-top-right-radius: {T.RADIUS_LG - 1}px;"
        )
        self._thumb.setPixmap(pixmap(self._thumb_icon, "#FFFFFF", 30))

    def set_thumbnail(self, pm: QPixmap) -> None:
        """用 .blend 内嵌缩略图 cover 填充缩略区；空图时保持渐变占位。"""
        if pm is None or pm.isNull():
            return
        self._thumb_src = pm
        self._render_thumb()

    def _render_thumb(self) -> None:
        src = self._thumb_src
        if src is None:
            return
        lw, lh = self._thumb.width(), self._thumb.height()
        if lw <= 1 or lh <= 1:
            return
        dpr = self.devicePixelRatio()
        dw, dh = max(1, round(lw * dpr)), max(1, round(lh * dpr))
        out = _rounded_cover(src, dw, dh, T.RADIUS_LG * dpr, top_only=True)
        if out.isNull():
            return
        out.setDevicePixelRatio(dpr)
        self._thumb.setStyleSheet("background: transparent;")
        self._thumb.setProperty("realThumb", True)
        self._thumb.setPixmap(out)

    def eventFilter(self, obj, e):
        # 卡片 resizeEvent 触发时子标签还没拿到新宽度，需在标签自身 resize 后重绘
        if obj is self._thumb and e.type() == QEvent.Type.Resize \
                and self._thumb_src is not None:
            self._render_thumb()
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._star is not None:
            self._place_star()
        if self._thumb_src is not None:
            self._render_thumb()

    # -- 批量选择态 -----------------------------------------------------
    def set_selectable(self, on: bool) -> None:
        """进入 / 退出多选模式（控制左上角勾选框可见性）。"""
        self._selectable = on
        self._check.setVisible(on)
        if not on:
            self.set_selected(False)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        if on == self._selected:
            return
        self._selected = on
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_check()

    def _sync_check(self) -> None:
        t = T.theme()
        if self._selected:
            self._check.setStyleSheet(
                f"background: {t.brand}; border-radius: 6px;")
            self._check.setPixmap(pixmap("check", t.on_brand, 13))
        else:
            self._check.setStyleSheet(
                f"background: {T.rgba('#000000', 0.45)}; "
                f"border: 1.5px solid #FFFFFF; border-radius: 6px;")
            self._check.clear()

    def _place_star(self) -> None:
        self._star.move(self._thumb.width() - 28, 8)

    def _lift_to(self, dy: int) -> None:
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(T.DUR_2)
        anim.setEasingCurve(T.ease_out())
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(self.pos().x(), self.pos().y() + dy))
        anim.start()

    def enterEvent(self, e: QEnterEvent):
        self._lift_to(-3)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._lift_to(3)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class NewCard(QFrame):
    """虚线新建卡：hover 变品牌色。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, icon_name: str = "plus", parent=None):
        super().__init__(parent)
        self.setObjectName("newcard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(250)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        plus = QLabel(self)
        plus.setFixedSize(32, 32)
        plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plus.setObjectName("plus")
        self._plus = plus
        lay.addWidget(plus, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label(text, "muted2"))
        self._icon_name = icon_name
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#newcard {{ background: transparent; border: 1.5px dashed {t.border_strong}; "
            f"border-radius: {T.RADIUS_LG}px; }}"
            f"#newcard:hover {{ border-color: {t.brand}; background: {t.brand_tint}; }}"
        )
        self._plus.setStyleSheet(
            f"#plus {{ border: 1.5px dashed {t.border_strong}; border-radius: 16px; }}"
            f"#newcard:hover #plus {{ border-color: {t.brand}; }}"
        )
        self._plus.setPixmap(pixmap(self._icon_name, T.theme().text_3, 16))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class DropZone(QFrame):
    """本地安装包拖放区（.zip / .tar.xz）。"""

    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(56)
        lay = QHBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        self._ic = QLabel(self)
        lay.addWidget(self._ic)
        lay.addWidget(label("拖入本地安装包（.zip / .tar.xz）到此处即可导入", "muted2"))
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        self.setStyleSheet(
            f"#dropzone {{ border: 1.5px dashed {t.border}; "
            f"border-radius: {T.RADIUS_MD}px; }}"
            f"#dropzone:hover {{ border-color: {t.brand}; }}"
        )
        self._ic.setPixmap(pixmap("file", t.text_3, 16))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            names = [u.toLocalFile().lower() for u in e.mimeData().urls()]
            if any(n.endswith((".zip", ".tar.xz", ".tar.gz")) for n in names):
                e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self.files_dropped.emit(paths)
        e.acceptProposedAction()


# ---------------------------------------------------------------- 选择控件
class RadioCard(QFrame):
    """单选卡：选中态品牌边框 + 淡橙底。"""

    clicked = pyqtSignal()

    def __init__(self, title: str, desc: str, parent=None):
        super().__init__(parent)
        self.setObjectName("rc")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        lay.addWidget(label(title, "body"))
        lay.addWidget(label(desc, "muted"))
        self._selected = False
        self.refresh()

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        bg = t.brand_tint if self._selected else t.bg_raised
        border = t.brand if self._selected else t.border
        self.setStyleSheet(
            f"#rc {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: {T.RADIUS_MD}px; }}"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class RadioCardGroup(QWidget):
    """横向单选卡组。"""

    currentChanged = pyqtSignal(int)

    def __init__(self, options: list[tuple[str, str]], current: int = 0, parent=None):
        super().__init__(parent)
        self._cards: list[RadioCard] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for i, (title, desc) in enumerate(options):
            c = RadioCard(title, desc, self)
            c.clicked.connect(lambda _=False, idx=i: self.set_current(idx))
            lay.addWidget(c)
            self._cards.append(c)
        self._current = -1
        self.set_current(current)

    def set_current(self, idx: int) -> None:
        if idx == self._current:
            return
        self._current = idx
        for i, c in enumerate(self._cards):
            c.set_selected(i == idx)
        self.currentChanged.emit(idx)

    def current(self) -> int:
        return self._current


class SettingRow(QWidget):
    """设置表单行：左标签（标题 + 说明）/ 右控件，底部 1px 分隔线。"""

    def __init__(self, title: str, desc: str, control: QWidget | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(label(title, "body"))
        left.addWidget(label(desc, "muted"))
        lay.addLayout(left)
        lay.addStretch(1)
        if control is not None:
            lay.addWidget(control)


# ---------------------------------------------------------------- 筛选控件
class _SegButton(QPushButton):
    """分段项按钮。

    QPushButton.sizeHint() 只按自身文本/图标计算，不量子布局，
    文本为空的按钮会被压成 32x15、子标签剩 0 宽；
    这里改写为内部布局的期望尺寸。
    """

    def sizeHint(self) -> QSize:
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()


class Segmented(QWidget):
    """分段控件（胶囊容器 + 互斥项）。

    计数必须用独立 QLabel 上色：QPushButton 不渲染富文本，
    直接把 <span> 拼进 setText 会把标签原样显示出来。
    """

    currentChanged = pyqtSignal(int)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._items = items
        box = QFrame(self)
        box.setObjectName("segbox")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        outer.addStretch(1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        for i, (text, count) in enumerate(items):
            b = _SegButton(self)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setObjectName("segbtn")
            inner = QHBoxLayout(b)
            inner.setContentsMargins(10, 4, 10, 4)
            inner.setSpacing(4)
            lb = QLabel(text, b)
            lb.setObjectName("seglabel")
            cnt = QLabel(count, b)
            cnt.setObjectName("segcount")
            inner.addWidget(lb)
            inner.addWidget(cnt)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self._buttons.append(b)
        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.currentChanged.emit)
        self.refresh()

    def set_items(self, items: list[tuple[str, str]]) -> None:
        """按真实数据重建分段项（数量 / 文案动态变化）。"""
        if items == self._items:
            return
        self._items = items
        box = self.findChild(QFrame, "segbox")
        lay = box.layout()
        for b in self._buttons:
            lay.removeWidget(b)
            self._group.removeButton(b)
            # 同 ChipFilter.set_items：必须立即脱离父级，防止幽灵控件残留
            b.setParent(None)
            b.deleteLater()
        self._buttons = []
        for i, (text, count) in enumerate(items):
            b = _SegButton(self)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setObjectName("segbtn")
            inner = QHBoxLayout(b)
            inner.setContentsMargins(10, 4, 10, 4)
            inner.setSpacing(4)
            lb = QLabel(text, b)
            lb.setObjectName("seglabel")
            cnt = QLabel(count, b)
            cnt.setObjectName("segcount")
            inner.addWidget(lb)
            inner.addWidget(cnt)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self._buttons.append(b)
        self._buttons[0].setChecked(True)
        self.refresh()

    def set_current(self, idx: int) -> None:
        """程序化选中指定项（set_items 会重置为 0，数据刷新后需调用恢复）。"""
        if 0 <= idx < len(self._buttons):
            self._buttons[idx].setChecked(True)

    def refresh(self) -> None:
        t = T.theme()
        self.findChild(QFrame, "segbox").setStyleSheet(  # type: ignore[union-attr]
            f"#segbox {{ background: {t.bg_raised}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_MD}px; }}"
        )
        font = 'font-size: 12.5px; font-family: "Noto Sans SC", "Microsoft YaHei";'
        for b in self._buttons:
            b.setStyleSheet(
                f"#segbtn {{ border: none; background: transparent; border-radius: 7px; }}"
                f"#segbtn:hover {{ background: {t.bg_hover}; }}"
                f"#segbtn:checked {{ background: {t.bg_hover}; }}"
                f"#segbtn:focus {{ border: 2px solid {t.brand_text}; }}"
                f"#seglabel {{ color: {t.text_2}; background: transparent; {font} }}"
                f"#segbtn:hover #seglabel {{ color: {t.text_1}; }}"
                f"#segbtn:checked #seglabel {{ color: {t.text_1}; font-weight: bold; }}"
                f"#segcount {{ color: {t.brand_text}; font-weight: bold; "
                f"background: transparent; {font} }}"
            )


class FlowLayout(QLayout):
    """流式布局：子项按可用宽度自动换行（分类胶囊一行放不下时折行）。

    高度随宽度变化，通过 heightForWidth 交给父布局；QScrollArea 在
    widgetResizable 下会据此重算内容高度，避免横向溢出被裁掉。
    """

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):  # noqa: N802 (Qt 命名)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self.spacing()
            if next_x - self.spacing() > area.right() and line_h > 0:
                x = area.x()
                y += line_h + self.spacing()
                next_x = x + hint.width() + self.spacing()
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ChipFilter(QWidget):
    """分类胶囊筛选（互斥）。"""

    currentChanged = pyqtSignal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._items = items
        lay = FlowLayout(self, 6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self._buttons.append(b)
        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.currentChanged.emit)
        self.refresh()

    def set_items(self, items: list[str]) -> None:
        """按真实数据重建胶囊项（分类 / 计数动态变化）。"""
        if items == self._items:
            return
        self._items = items
        lay = self.layout()
        for b in self._buttons:
            lay.removeWidget(b)
            self._group.removeButton(b)
            # removeWidget 只解除布局管理，控件仍是子控件且可见；
            # deleteLater 又要等事件循环才生效——期间旧胶囊会以默认尺寸
            # (640x480) 残留在原地，看起来就是「两块界面重叠」。必须立即脱离。
            b.setParent(None)
            b.deleteLater()
        self._buttons = []
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self._buttons.append(b)
        self._buttons[0].setChecked(True)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        for b in self._buttons:
            b.setStyleSheet(
                f"QPushButton {{ border: 1px solid {t.border}; background: {t.bg_raised}; "
                f"color: {t.text_2}; padding: 4px 12px; border-radius: 999px; "
                f'font-size: 12px; font-family: "Noto Sans SC", "Microsoft YaHei"; }}'
                f"QPushButton:hover {{ color: {t.text_1}; border-color: {t.border_strong}; }}"
                f"QPushButton:checked {{ background: {t.brand}; color: {t.on_brand}; "
                f"border-color: {t.brand}; }}"
                f"QPushButton:focus {{ border: 2px solid {t.brand_text}; padding: 3px 11px; }}"
                f"QPushButton:checked:focus {{ border: 2px solid {t.on_brand}; padding: 3px 11px; }}"
            )


class VerticalNav(QWidget):
    """纵向导航（设置页左栏，互斥）。"""

    currentChanged = pyqtSignal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self._buttons.append(b)
        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.currentChanged.emit)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        for b in self._buttons:
            b.setStyleSheet(
                f"QPushButton {{ border: none; background: transparent; color: {t.text_2}; "
                f"text-align: left; padding: 6px 10px; border-radius: 8px; "
                f'font-size: 13px; font-family: "Noto Sans SC", "Microsoft YaHei"; }}'
                f"QPushButton:hover {{ color: {t.text_1}; }}"
                f"QPushButton:checked {{ background: {t.brand_tint}; color: {t.brand_text}; font-weight: bold; }}"
                f"QPushButton:focus {{ border: 2px solid {t.brand_text}; }}"
            )


class PageHeader(QWidget):
    """页头：标题 + 副标题 + 右侧操作。"""

    def __init__(self, title: str, sub: str, actions: list[QWidget] | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(ElidedLabel(title, "h1"))
        # 副标题常含路径/统计长文本：可省略，否则会把页头连同右侧操作挤出窗口
        self._sub_lb = ElidedLabel(sub, "sub")
        left.addWidget(self._sub_lb)
        lay.addLayout(left, 1)
        if actions:
            row = QHBoxLayout()
            row.setSpacing(6)
            for w in actions:
                row.addWidget(w)
            lay.addLayout(row)

    def set_sub(self, text: str) -> None:
        self._sub_lb.setText(text)


# ---------------------------------------------------------------- Toast
class Toast(QFrame):
    """Toast：360ms 滑入，4s 后自动收起。

    外层容器不使用 QGraphicsOpacityEffect：在无边框半透明主窗口上该特效
    会让整个 Toast 不参与绘制（实测 opacity=1.0 仍不可见），因此只做位移
    动画，内层卡片保留投影。
    """

    closed = pyqtSignal(object)  # 传 Toast 自身，便于宿主精确摘除

    def __init__(self, text: str, kind: str = "ok", action: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("toastHost")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("toastCard")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        self._ic = QLabel(card)
        self._ic.setFixedSize(16, 16)
        lay.addWidget(self._ic)
        # 超长路径没有空格可断行，用省略号 + tooltip 而不是 wordWrap
        self._msg = ElidedLabel(text, "body")
        self._msg.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._msg.setMinimumWidth(0)
        lay.addWidget(self._msg, 1)
        if action is not None:
            lay.addWidget(action)
        outer.addWidget(card)
        self._card = card
        self._kind = kind
        self._closing = False
        if kind == "ok":
            self._icon_name, self._icon_color = "check", "success"
        elif kind == "info":
            self._icon_name, self._icon_color = "alert", "info"
        else:
            self._icon_name, self._icon_color = "alert", "error"
        self.refresh()

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        card.setGraphicsEffect(shadow)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(4000)

    def refresh(self) -> None:
        t = T.theme()
        self._card.setStyleSheet(
            f"#toastCard {{ background: {t.bg_raised}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_MD}px; }}"
        )
        fg = getattr(t, self._icon_color)
        self._ic.setPixmap(pixmap(self._icon_name, fg, 16))

    def fit_to(self, max_w: int) -> None:
        """限制宽度；超长文本省略（完整内容在 tooltip），避免横向溢出窗口。"""
        self.setMaximumWidth(max_w)
        # 可用文字宽度 = 总宽 - 外层留白(20) - 卡片内边距(28) - 图标(16) - 间距(10)
        avail = max(80, max_w - 74)
        self._msg.setMinimumWidth(0)
        self._msg.setMaximumWidth(16777215)
        if self._msg.sizeHint().width() > avail:
            self._msg.setFixedWidth(avail)
        self.adjustSize()

    def play_in(self) -> None:
        target = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(T.DUR_3)
        anim.setEasingCurve(T.ease_out())
        anim.setStartValue(QPoint(target.x(), target.y() + 16))
        anim.setEndValue(target)
        anim.start()
        self._pos_anim = anim

    def stop_slide(self) -> None:
        """堆叠位置变化时中断旧滑入动画，否则它会按旧目标把 Toast 拉回去。"""
        anim = getattr(self, "_pos_anim", None)
        if anim is not None and anim.state() == QPropertyAnimation.State.Running:
            anim.stop()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self.hide()
        # 先 emit（同步摘除，对象仍存活）再 deleteLater
        self.closed.emit(self)
        self.deleteLater()


class ToastHost(QWidget):
    """覆盖层容器：右下角堆叠 Toast，宽高由主窗口同步。

    覆盖层本身必须对鼠标事件透明，否则会吞掉整个窗口的点击与悬停；
    Toast 子控件自身仍可正常接收事件。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._toasts: list[Toast] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def show_toast(self, text: str, kind: str = "ok", action: QWidget | None = None) -> None:
        t = Toast(text, kind, action, self)
        t.closed.connect(self._remove)
        t.show()
        t.raise_()
        self._toasts.append(t)
        self._relayout(animate=t)

    def _remove(self, t: Toast) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
        self._relayout()

    def _relayout(self, animate: Toast | None = None) -> None:
        margin_r, margin_b, gap = 16, 16, 8
        max_w = max(240, min(560, self.width() - 2 * margin_r))
        y = self.height() - margin_b
        for t in reversed(list(self._toasts)):
            try:
                t.fit_to(max_w)
            except RuntimeError:
                self._toasts.remove(t)
                continue
            if t is not animate:
                t.stop_slide()
            y -= t.height()
            t.move(max(margin_r, self.width() - margin_r - t.width()), y)
            y -= gap
            if t is animate:
                t.play_in()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()
