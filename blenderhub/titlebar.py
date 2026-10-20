"""自定义标题栏：拖拽移动、双击最大化 / 还原、窗口控制按钮与主题切换。

配合主窗口的 FramelessWindowHint + WA_TranslucentBackground 使用，
由主窗口负责实际执行 move / showMaximized / showNormal / close。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme as T
from .icons import icon, pixmap
from .widgets import IconButton, label


class TitleBar(QWidget):
    minimize_clicked = pyqtSignal()
    maximize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    theme_clicked = pyqtSignal()

    def __init__(self, title: str = "BlenderHub", parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setObjectName("titlebar")
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(6)

        self._logo = QLabel(self)
        self._logo.setFixedSize(20, 20)
        lay.addWidget(self._logo)
        lay.addWidget(label(title, "muted2"))
        lay.addStretch(1)

        self._theme_btn = IconButton("moon", "ghost", self, tip="切换深浅主题")
        self._theme_btn.clicked.connect(self.theme_clicked.emit)
        lay.addWidget(self._theme_btn)

        self._min_btn = self._win_button("min", "最小化")
        self._max_btn = self._win_button("max", "最大化 / 还原")
        self._close_btn = self._win_button("x", "关闭", close=True)
        lay.addWidget(self._min_btn)
        lay.addWidget(self._max_btn)
        lay.addWidget(self._close_btn)

        self._min_btn.clicked.connect(self.minimize_clicked.emit)
        self._max_btn.clicked.connect(self.maximize_clicked.emit)
        self._close_btn.clicked.connect(self.close_clicked.emit)

        self.refresh()

    def _win_button(self, name: str, tip: str, close: bool = False) -> QPushButton:
        b = QPushButton(self)
        b.setFixedSize(36, 28)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setObjectName("closeBtn" if close else "winBtn")
        b._icon_name = name  # type: ignore[attr-defined]
        b.setIconSize(QSize(16, 16))
        return b

    def refresh(self) -> None:
        t = T.theme()
        dark = t.name == "dark"
        self._logo.setPixmap(pixmap("logo", "", 20))
        self._theme_btn.setIcon(icon("sun" if dark else "moon", t.text_2, 14))
        self._min_btn.setIcon(icon("min", t.text_2, 16))
        self._max_btn.setIcon(icon("max", t.text_2, 16))
        self._close_btn.setIcon(icon("x", t.text_2, 16))
        self.setStyleSheet(
            f"#titlebar {{ background: {t.bg_surface}; "
            f"border-top-left-radius: {T.RADIUS_XL}px; border-top-right-radius: {T.RADIUS_XL}px; }}"
            f"#winBtn {{ background: transparent; border: none; border-radius: 6px; }}"
            f"#winBtn:hover {{ background: {t.bg_hover}; }}"
            f"#winBtn:focus {{ border: 2px solid {t.brand_text}; }}"
            f"#closeBtn {{ background: transparent; border: none; border-radius: 6px; }}"
            f"#closeBtn:hover {{ background: #C42B1C; }}"
            f"#closeBtn:focus {{ border: 2px solid {t.brand_text}; }}"
        )

    # -- 拖拽与双击 ---------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(e)
            return
        win = self.window()
        if win.isMaximized():
            # 从最大化拖出：按光标在标题栏上的相对位置还原窗口，避免跳动
            ratio = e.position().x() / max(1, self.width())
            win.showNormal()
            # globalPosition() 返回 QPointF（float），PyQt6 的 move() 只接受 int，
            # 必须先 toPoint() 转成整数坐标，否则抛 TypeError
            gpos = e.globalPosition().toPoint()
            win.move(gpos.x() - int(win.width() * ratio), gpos.y() - 21)
        else:
            win.move(win.pos() + e.globalPosition().toPoint() - self._drag_pos)
        self._drag_pos = e.globalPosition().toPoint()
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.maximize_clicked.emit()
        super().mouseDoubleClickEvent(e)
