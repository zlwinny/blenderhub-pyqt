"""主窗口：自定义标题栏 + 侧边导航 + QStackedWidget + 状态栏 + Toast 覆盖层。

窗口为 FramelessWindowHint + 半透明背景，由 shell 控件绘制 18px 圆角外壳；
右下角 QSizeGrip 提供缩放，边缘留白 1px 容纳圆角描边。
"""
from __future__ import annotations

import struct

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import detect
from . import native_dialog as ND
from . import theme as T
from . import store
from .addon_store import AddonStore, BudecoAuth
from .icons import pixmap
from .pages import AddonsPage, HomePage, ProjectsPage, SettingsPage, VersionsPage
from .thumbs import ThumbLoader
from .titlebar import TitleBar
from .widgets import label

_NAV = [
    ("首页", "home", None),
    ("版本管理", "versions", None),
    ("项目", "folder", None),
    ("插件", "puzzle", "0"),
    ("设置", "gear", None),
]


class NavItem(QFrame):
    """侧边导航项：active 用动态属性驱动 QSS。"""

    clicked = None

    def __init__(self, text: str, icon_name: str, badge: str | None, parent=None):
        super().__init__(parent)
        self.setObjectName("navitem")
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._text = text
        self._icon_name = icon_name
        self._badge = badge
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        self._ic = QLabel(self)
        self._ic.setFixedSize(16, 16)
        lay.addWidget(self._ic)
        self._text_lb = label(text, "body")
        lay.addWidget(self._text_lb)
        lay.addStretch(1)
        self._badge_lb = None
        if badge is not None:
            self._badge_lb = QLabel(badge, self)
            self._badge_lb.setFixedSize(18, 18)
            self._badge_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self._badge_lb)
            self._badge_lb.setVisible(badge != "0")
        self.refresh()

    def set_badge(self, text: str) -> None:
        if self._badge_lb is None:
            return
        self._badge_lb.setText(text)
        self._badge_lb.setVisible(text != "0")

    def set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_colors()

    def _sync_colors(self) -> None:
        t = T.theme()
        color = t.brand_text if self.property("active") else t.text_2
        self._ic.setPixmap(pixmap(self._icon_name, color, 16))
        # 直接给标签上色：属性选择器的后代规则在运行时改属性后不会自动重刷子控件
        self._text_lb.setStyleSheet(f"color: {color};")

    def refresh(self) -> None:
        t = T.theme()
        self._sync_colors()
        if self._badge_lb is not None:
            self._badge_lb.setStyleSheet(
                f"background: {t.brand}; color: {t.on_brand}; border-radius: 9px; "
                f"font-size: 10px; font-weight: bold;"
            )
        self.setStyleSheet(
            f"#navitem {{ background: transparent; border-radius: 8px; }}"
            f"#navitem:hover {{ background: {t.bg_hover}; }}"
            f'#navitem[active="true"] {{ background: {t.brand_tint}; }}'
            f"#navitem:focus {{ border: 2px solid {t.brand}; }}"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._activate()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            e.accept()
            return
        super().keyPressEvent(e)

    def _activate(self) -> None:
        parent = self.parent()
        while parent is not None and not isinstance(parent, SideBar):
            parent = parent.parent()
        if isinstance(parent, SideBar):
            parent.select(self)


class SideBar(QWidget):
    """侧边导航：品牌 + 导航项 + 用户卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(190)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        self._logo = QLabel(self)
        self._logo.setFixedSize(24, 24)
        brand.addWidget(self._logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        bcol.addWidget(label("BlenderHub", "h2"))
        bcol.addWidget(label("v1.0.0", "mono"))
        brand.addLayout(bcol)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(12)

        self._items: list[NavItem] = []
        for text, icon_name, badge in _NAV:
            item = NavItem(text, icon_name, badge, self)
            self._items.append(item)
            lay.addWidget(item)
        lay.addStretch(1)

        user = QFrame(self)
        user.setObjectName("userbox")
        ul = QHBoxLayout(user)
        ul.setContentsMargins(8, 6, 8, 6)
        ul.setSpacing(8)
        self._avatar = QLabel("Z", self)
        self._avatar.setFixedSize(26, 26)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ul.addWidget(self._avatar)
        ucol = QVBoxLayout()
        ucol.setSpacing(0)
        ucol.addWidget(label("ZL Studio", "body"))
        ucol.addWidget(label("本地账户", "muted"))
        ul.addLayout(ucol, 1)
        lay.addWidget(user)
        self._user = user
        self.refresh()

        self._current = 0
        self._items[0].set_active(True)
        for a, b in zip(self._items, self._items[1:]):
            QWidget.setTabOrder(a, b)

    def refresh(self) -> None:
        t = T.theme()
        self._logo.setPixmap(pixmap("logo", "", 24))
        self._avatar.setStyleSheet(
            f"background: {t.brand}; color: {t.on_brand}; border-radius: 15px; "
            f"font-size: 13px; font-weight: bold;"
        )
        self._user.setStyleSheet(
            f"#userbox {{ background: {t.bg_raised}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_MD}px; }}"
        )
        for item in self._items:
            item.refresh()

    def select(self, item: NavItem) -> None:
        idx = self._items.index(item)
        if idx == self._current:
            return
        self._current = idx
        for i, it in enumerate(self._items):
            it.set_active(i == idx)
        self.window().switch_page(idx)

    def set_current(self, index: int) -> None:
        """只同步高亮态，不回调 switch_page。

        供首页统计卡 /「全部 →」等入口直接调 switch_page 的场景调用，
        否则页面切了而侧栏仍停在上一个条目上（点击后目录不跟着走）。
        """
        if not 0 <= index < len(self._items) or index == self._current:
            return
        self._current = index
        for i, it in enumerate(self._items):
            it.set_active(i == index)

    def set_badge_plugins(self, count: int) -> None:
        for item in self._items:
            if item._text == "插件":
                item.set_badge(str(count))


class _StatusBar(QWidget):
    """状态栏：左侧状态项 + 右侧版本信息 + 内嵌缩放角标。

    角标作为状态栏子控件内缩放置：若放在布局里会把状态栏右侧顶出 14px 缺口、
    右边距与左边距不对称；内缩 10px 后既保持通栏对齐，又完整落在 shell 的
    18px 圆角内，不会被圆角切出画布。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusbar")
        self.setFixedHeight(28)
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(14, 14)
        self._grip.setStyleSheet("background: transparent; border: none;")
        self._grip.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 24, self.height() - 24)


_GEOM_MAGIC = b"\x01\xd9\xd0\xcb"
_GEOM_MAGIC_OLD = b"\x01\xd9\xd0\xc1"


def saved_frame_rect(blob) -> QRect | None:
    """从 Qt `saveGeometry()` 的 blob 里取出保存时的 frameGeometry。

    布局（大端）：magic(4) + major(2) + minor(2) + frameRect。

    注意 frameRect **不是** x,y,w,h —— `QDataStream << QRect` 写的是
    left/top/right/bottom（包含端点的坐标），宽度要自己算 right-left+1。

    为什么需要它：`restoreGeometry()` 内部会把「超出单块屏幕可用区」的窗口
    收缩回一块屏，双屏并排时跨度较大的窗口会被误砍；拿原始矩形自己判断
    才能保住合法的跨屏尺寸。
    """
    if blob is None:
        return None
    try:
        raw = blob.data() if hasattr(blob, "data") else bytes(blob)
        raw = bytes(raw)
    except Exception:
        return None
    if len(raw) < 24 or raw[:4] not in (_GEOM_MAGIC, _GEOM_MAGIC_OLD):
        return None
    try:
        left, top, right, bottom = struct.unpack(">4i", raw[8:24])
    except struct.error:
        return None
    w = right - left + 1
    h = bottom - top + 1
    if w <= 0 or h <= 0:
        return None
    return QRect(left, top, w, h)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BlenderHub")
        self.setMinimumSize(960, 640)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._app_guard = None
        ND.install_repaint_guard(self)
        self._current_index = 0
        self._restore_geometry()
        self._build()

    # -- 窗口几何：自适应首启 + 记住上次 -----------------------------
    def _restore_geometry(self) -> None:
        """有记录就还原，否则按当前屏幕工作区算一个合适尺寸。"""
        blob = store.load_geometry()
        if blob is not None:
            want = saved_frame_rect(blob)
            try:
                if self.restoreGeometry(blob):
                    # restoreGeometry 会把跨屏大窗口收缩到单块屏幕，
                    # 原始矩形在当前布局下依旧完整可见时，按原值还原
                    if want is not None and not self.isMaximized() \
                            and self._visible_ratio(want) >= 0.98:
                        self.setGeometry(want)
                    self._clamp_to_screens()
                    return
            except Exception:
                pass
        self._apply_default_geometry()

    def _visible_ratio(self, rect: QRect) -> float:
        """窗口矩形落在所有真实屏幕可用区内的面积占比。"""
        screens = QApplication.screens()
        if not screens:
            return 1.0
        area = max(1, rect.width() * rect.height())
        covered = 0
        for s in screens:
            inter = s.availableGeometry().intersected(rect)
            if inter.isValid() and not inter.isEmpty():
                covered += inter.width() * inter.height()   # 各屏可用区互不重叠
        return covered / area

    def _apply_default_geometry(self) -> None:
        """首次启动：取主屏工作区的约 65% × 76%，居中，并限制在合理区间。

        比例对齐正式版 Blender Hub 的实际窗口（1240×780 / 1920×1032 工作区），
        既不被两侧留白挤得太窄，也不至于一上来就铺满整块屏。
        """
        scr = QApplication.primaryScreen()
        ava = scr.availableGeometry() if scr is not None else None
        if ava is None or ava.width() < 200:
            self.resize(1240, 780)
            return
        w = max(self.minimumWidth(), min(int(ava.width() * 0.65), 1600))
        h = max(self.minimumHeight(), min(int(ava.height() * 0.76), 1000))
        w = min(w, ava.width())
        h = min(h, ava.height())
        x = ava.x() + (ava.width() - w) // 2
        y = ava.y() + (ava.height() - h) // 2
        self.setGeometry(x, y, w, h)

    def _clamp_to_screens(self) -> None:
        """窗口只要有绝大部分落在真实屏幕上就保留，否则收回主屏。

        判据用「可见面积比例」而不是窗口中心：双屏并排时 3499 宽的窗口中心
        可能落在两块屏的交界之外，用中心判断会把它误砍成单屏宽度。
        """
        screens = QApplication.screens()
        if not screens:
            return
        g = self.frameGeometry()
        if self._visible_ratio(g) >= 0.98:
            return                     # 全部可见（含横跨双屏），原样保留

        host = QApplication.screenAt(g.center()) or QApplication.primaryScreen() \
            or screens[0]
        ava = host.availableGeometry()

        w = min(g.width(), ava.width())
        h = min(g.height(), ava.height())
        w = max(w, min(self.minimumWidth(), ava.width()))
        h = max(h, min(self.minimumHeight(), ava.height()))

        x = min(max(g.x(), ava.x()), ava.right() - w + 1)
        y = min(max(g.y(), ava.y()), ava.bottom() - h + 1)
        if (x, y, w, h) != (g.x(), g.y(), g.width(), g.height()):
            self.setGeometry(x, y, w, h)

    def closeEvent(self, e):
        """关闭时记住几何（含最大化 / 最小化状态），下次启动还原。"""
        try:
            store.save_geometry(self.saveGeometry())
        except Exception:
            pass
        super().closeEvent(e)

    # -- 构建 --------------------------------------------------------
    def _build(self) -> None:
        store.ckpt("build-begin")
        shell = QWidget()
        shell.setObjectName("shell")
        v = QVBoxLayout(shell)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)

        self._titlebar = TitleBar("BlenderHub", shell)
        self._titlebar.minimize_clicked.connect(self.showMinimized)
        self._titlebar.maximize_clicked.connect(self._toggle_maximize)
        self._titlebar.close_clicked.connect(self.close)
        self._titlebar.theme_clicked.connect(self._toggle_theme)
        v.addWidget(self._titlebar)
        store.ckpt("titlebar")

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._sidebar = SideBar(shell)
        body.addWidget(self._sidebar)
        store.ckpt("sidebar")

        self.stack = QStackedWidget()
        self._pages = []
        for i, page in enumerate((HomePage(), VersionsPage(), ProjectsPage(), AddonsPage(), SettingsPage())):
            store.ckpt(f"page-{i}-begin")
            self._pages.append(page)
            if isinstance(page, SettingsPage):
                page.theme_requested.connect(self._on_settings_theme)
            sa = QScrollArea()
            sa.setWidget(page)
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.Shape.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.stack.addWidget(sa)
            store.ckpt(f"page-{i}-done")
        body.addWidget(self.stack, 1)
        v.addLayout(body, 1)
        store.ckpt("pages")

        self._status_labels: list[QLabel] = []
        self._status_box = _StatusBar(shell)
        inner = QHBoxLayout(self._status_box)
        inner.setContentsMargins(16, 0, 26, 0)
        inner.setSpacing(8)
        self._status_layout = inner
        inner.addStretch(1)
        self._right_status = label("BlenderHub v1.0.0 · Windows 11", "mono")
        inner.addWidget(self._right_status)
        store.ckpt("statusbar")

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(0)
        bottom.addWidget(self._status_box, 1)
        v.addLayout(bottom)

        self.setCentralWidget(shell)
        self._shell = shell
        self._snapshot: detect.Snapshot | None = None
        self._thumbs = ThumbLoader(self)
        store.ckpt("thumbs")
        self._store = AddonStore(self)
        store.ckpt("store-obj")
        self._auth = BudecoAuth(self)
        store.ckpt("auth")
        self.rescan()
        store.ckpt("rescan")
        self._refresh_status(0)
        store.ckpt("refresh-status")

        from .widgets import ToastHost

        self._toast_host = ToastHost(shell)
        self._toast_host.setGeometry(shell.rect())
        self._toast_host.raise_()
        store.ckpt("build-done")

    # -- 真实检测 ----------------------------------------------------
    def rescan(self) -> None:
        """重新执行本机真实检测，分发快照到所有页面并刷新状态栏。"""
        data = store.load()
        store.ckpt("rescan-begin")
        snap = detect.scan(
            install_dir=data["install_dir"],
            default_version=data["default_version"],
        )
        store.ckpt(f"scan-done inst={len(snap.installs)} proj={len(snap.projects)} addon={len(snap.addons)}")
        self._snapshot = snap
        for page in self._pages:
            reload_ = getattr(page, "reload", None)
            if callable(reload_):
                reload_(snap)
        store.ckpt("pages-reloaded")
        self._sidebar.set_badge_plugins(len(snap.addons))
        self._refresh_status(self._current_index)
        store.ckpt("rescan-done")

    def set_default_version(self, label_text: str) -> None:
        """首页 / 版本页 / 设置页共用的默认版本切换（立即保存并刷新）。"""
        store.save(default_version=label_text)
        self.rescan()
        settings = self._pages[4]
        if settings is not None and hasattr(settings, "set_default_version"):
            settings.set_default_version(label_text)

    # -- 主题 --------------------------------------------------------
    def _apply_theme_and_save(self) -> None:
        T.apply_theme(QApplication.instance())
        T.repolish_tree(self)
        self._apply_shell_style()
        store.save(theme=T.theme().name)

    def _toggle_theme(self) -> None:
        T.toggle_theme()
        self._apply_theme_and_save()

    def _on_settings_theme(self, name: str) -> None:
        if name == "system":
            # 简化实现：跟随系统暂按深色渲染，正式版可读注册表 AppAppsUseLightTheme
            name = "dark"
            self.show_toast("跟随系统：当前按深色渲染", "info")
        target = T.DARK if name == "dark" else T.LIGHT
        if T.theme().name != target.name:
            T.set_theme(target)
        self._apply_theme_and_save()

    def _apply_shell_style(self) -> None:
        t = T.theme()
        self._shell.setStyleSheet(
            f"#shell {{ background: {t.bg_app}; border: 1px solid {t.border}; "
            f"border-radius: {T.RADIUS_XL}px; }}"
            f"#statusbar {{ background: {t.bg_surface}; "
            f"border-bottom-left-radius: {T.RADIUS_XL - 1}px; "
            f"border-bottom-right-radius: {T.RADIUS_XL - 1}px; }}"
        )

    # -- 页面切换 ----------------------------------------------------
    def switch_page(self, index: int) -> None:
        if index == self._current_index:
            return
        self._current_index = index
        # 不做透明度淡入：QGraphicsOpacityEffect 半透明合成时上一页的帧会
        # 从底下透出来（观感＝两页内容重叠），从 0 淡入又会先空白一瞬。
        # 直接切换最稳，也是桌面应用的常规做法。
        self.stack.setCurrentIndex(index)
        # 非侧栏入口（首页统计卡、面板「全部 →」）也要让侧栏高亮跟着走
        sidebar = getattr(self, "_sidebar", None)
        if sidebar is not None:
            sidebar.set_current(index)
        self._refresh_status(index)

    def _refresh_status(self, index: int) -> None:
        for lb in self._status_labels:
            self._status_layout.removeWidget(lb)
            lb.deleteLater()
        self._status_labels = []
        parts: list[str] = []
        for i, text in enumerate(self._status_texts(index)):
            if i:
                parts.append("·")
            parts.append(text)
        for i, text in enumerate(parts):
            lb = label(text, "mono")
            self._status_layout.insertWidget(i, lb)
            self._status_labels.append(lb)

    def _status_texts(self, index: int) -> list[str]:
        snap = self._snapshot
        if snap is None:
            return ["正在检测本机环境…"]
        if index == 0:
            default = snap.default_install
            head = f"默认版本 {default.version} {default.tag}" if default else "未检测到 Blender 安装"
            return [head, f"项目 {len(snap.projects)} · 插件 {len(snap.addons)}"]
        if index == 1:
            return [
                f"{len(snap.installs)} 个已安装版本",
                f"磁盘 C: {detect.fmt_disk_free('C:')} 可用",
            ]
        if index == 2:
            return [
                f"{len(snap.projects)} 个项目",
                f"上次扫描：今天 {snap.scanned_at.strftime('%H:%M')}",
            ]
        if index == 3:
            return [f"{len(snap.addons)} 个插件", f"{len(snap.addon_roots)} 个插件目录"]
        return ["设置已同步"]

    # -- Toast -------------------------------------------------------
    def show_toast(self, text: str, kind: str = "ok") -> None:
        self._toast_host.show_toast(text, kind)

    # -- 窗口控制 ----------------------------------------------------
    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_toast_host"):
            self._toast_host.setGeometry(self._shell.rect())
            self._toast_host.raise_()

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_shell_style()

    def changeEvent(self, e):
        super().changeEvent(e)
        # 原生模态对话框（如 QFileDialog）关闭后窗口重新激活时，
        # DWM 可能丢失无框半透明窗口的外壳绘制，这里强制重刷背景
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._apply_shell_style()

    def _repaint_shell(self) -> None:
        """原生对话框关闭后强制重绘外壳，修复背景变透明。

        单次重绘会与对话框销毁 / DWM 重新合成竞争，这里走多点延迟补刷。
        """
        ND.repaint_shell(self)
