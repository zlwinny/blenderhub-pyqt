"""五个核心页面 —— 与设计规范第 03 章 Mockup 一一对应。

所有列表数据均来自 detect.scan() 的真实检测快照（Snapshot），
交互全部接真实功能：
启动 Blender（本机真实安装）、打开目录 / 项目（真实路径校验）、
搜索与筛选（真实过滤）、安装包导入（真实解压）、
设置保存（QSettings 持久化，重启生效）。

页面数据刷新统一走 reload(snap)：由 MainWindow 启动扫描与 rescan() 驱动；
方法名避开 refresh()，防止被 theme.repolish_tree() 无参误触发。
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

from PyQt6.QtCore import QPoint, Qt, pyqtSignal, QThreadPool, QTimer
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import detect
from . import native_dialog as ND
from . import system_ops as ops
from . import store
from . import theme as T
from .addon_dialogs import (
    PanDownloadDialog,
    StoreDetailDialog,
    StoreLoginDialog,
    confirm,
)
from .addon_store import (
    CATEGORY_CHIPS,
    AddonStore,
    AddonVerifier,
    DirectDownloadTask,
    DownloadTarget,
    InstallZipTask,
    NetBridge,
    ResolveTask,
    StoreAddon,
    VerifyLoginTask,
    VerifyResult,
    VerifyZipTask,
    cache_root,
    has_net_deps,
    install_zip,
)
from .icons import pixmap
from .widgets import (
    Button,
    Card,
    Chip,
    ChipFilter,
    DropZone,
    ElidedLabel,
    IconButton,
    MediaCard,
    NewCard,
    PageHeader,
    RadioCardGroup,
    SearchBox,
    Segmented,
    SettingRow,
    StatCard,
    THUMB_GRADIENTS,
    Toggle,
    VersionRow,
    VerticalNav,
    _rounded_cover,
    hline,
    label,
)

_PAGE_MARGIN = 20
_CARD_W = 300             # 卡片目标宽度：1240 窗口 → 3 列，960 → 2 列
_GRID_GAP = 14
_THUMB_KEYS = ["a", "b", "c", "d", "e", "f"]
_ONLINE_CAP = 240          # 在线目录单屏渲染上限（目录可达上千条）


def _page(title: str, sub: str, actions=None) -> tuple[QWidget, QVBoxLayout, PageHeader]:
    """页面根：页头 + 内容纵向布局（由主窗口包进 QScrollArea）。"""
    root = QWidget()
    lay = QVBoxLayout(root)
    lay.setContentsMargins(_PAGE_MARGIN, 14, _PAGE_MARGIN, 14)
    lay.setSpacing(14)
    header = PageHeader(title, sub, actions or [])
    lay.addWidget(header)
    return root, lay, header


def _open_blend(window, name: str, path: str) -> None:
    """首页最近项目与项目页共用的打开逻辑。"""
    ok, msg = ops.launch_blender(blend=path)
    if ok:
        window.show_toast(f"已打开项目：{name}", "ok")
    else:
        window.show_toast(f"{name}：{msg}", "info")


def _menu(parent, items: list[str], on_pick) -> QMenu:
    """主题化弹出菜单；选中项回调 on_pick(文本)。"""
    t = T.theme()
    m = QMenu(parent)
    m.setStyleSheet(
        f"QMenu {{ background: {t.bg_raised}; border: 1px solid {t.border};"
        f" border-radius: 10px; padding: 4px; }}"
        f"QMenu::item {{ padding: 6px 16px; border-radius: 7px; color: {t.text_1};"
        f' font-family: "Noto Sans SC", "Microsoft YaHei"; font-size: 12.5px; }}'
        f"QMenu::item:selected {{ background: {t.bg_hover}; }}"
        f"QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 6px; }}"
    )
    for text in items:
        m.addAction(text)
    m.triggered.connect(lambda a: on_pick(a.text()))
    return m


def _clear_layout(lay) -> None:
    """清空布局内所有子控件（删除而非隐藏，保证下次重建干净）。"""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


def _set_filtered(w: QWidget, visible: bool) -> None:
    """筛选态单独记属性：新卡片在未显示时 isHidden() 也是 True，不可依赖。"""
    w.setProperty("filteredOut", not visible)
    w.setVisible(visible)


def _regrid(grid: QGridLayout, widgets: list[QWidget],
            empty: QWidget | None, cols: int, compact: bool = True) -> None:
    """只挪动已有控件，不重建：缩略图 / 选择态 / 信号全部保留。

    compact=True 时被搜索/筛选隐藏的卡片不占网格位，否则可见卡片之间会留下空洞。
    """
    if compact:
        shown = [w for w in widgets if w.property("filteredOut") is not True]
        for w in widgets:
            if w.property("filteredOut") is True:
                grid.removeWidget(w)
    else:
        shown = list(widgets)
    if empty is not None:
        grid.removeWidget(empty)
    for i, w in enumerate(shown):
        # 先 addWidget 再 show：新建卡片没有父级，先 show 会变顶层窗口，
        # 重新挂到布局里时又会被隐藏
        grid.addWidget(w, i // cols, i % cols)
        if w.property("filteredOut") is not True:
            w.show()
    for w in widgets:
        if w.property("filteredOut") is True:
            w.hide()
    if empty is not None:
        grid.addWidget(empty, 0, 0, 1, cols)
        empty.show()
    for c in range(6):
        grid.setColumnStretch(c, 1 if c < cols else 0)


def _ver_sort_key(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", ver)) or (0,)


def _grid_cols(width: int, card_w: int = _CARD_W, gap: int = _GRID_GAP) -> int:
    """按页面可用宽度算卡片列数，避免固定列数在窄窗口下把右侧挤出可视区。"""
    avail = max(1, width - 2 * _PAGE_MARGIN)
    return max(1, min(6, (avail + gap) // (card_w + gap)))


class _MoreLink(QLabel):
    """可点击的「更多 →」文字链接。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("textRole", "stat_extra")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def panel_head(title: str, more: str = "", on_more=None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(label(title, "h2"))
    lay.addStretch(1)
    if more:
        if on_more is not None:
            link = _MoreLink(more)
            link.clicked.connect(on_more)
            lay.addWidget(link)
        else:
            lay.addWidget(label(more, "stat_extra"))
    return w


class EmptyState(QFrame):
    """列表空态：图标 + 标题 + 说明（真实检测无数据时展示）。"""

    def __init__(self, text: str, desc: str, parent=None):
        super().__init__(parent)
        self.setObjectName("empty")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(100)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(4)
        self._ic = QLabel(self)
        self._ic.setFixedSize(22, 22)
        self._ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._ic, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label(text, "h2"), 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label(desc, "muted"), 0, Qt.AlignmentFlag.AlignCenter)
        self.refresh()

    def refresh(self) -> None:
        self._ic.setPixmap(pixmap("alert", T.theme().text_3, 22))


class BubbleIcon(QLabel):
    """24px 圆角图标气泡（动态流用）。"""

    def __init__(self, name: str, variant: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name = name
        self._variant = variant
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        bg, fg = {
            "info": (t.info_bg, t.info),
            "warn": (t.brand_tint, t.brand_text),
            "ok": (t.success_bg, t.success),
        }[self._variant]
        self.setStyleSheet(f"background: {bg}; border-radius: 7px;")
        self.setPixmap(pixmap(self._name, fg, 13))


class ProjectRow(QFrame):
    """首页「最近项目」列表行。整行可点击打开。"""

    open_clicked = pyqtSignal()
    clicked = pyqtSignal()

    def __init__(self, name: str, path: str, chip_text: str, chip_variant: str, thumb: str, parent=None):
        super().__init__(parent)
        self.setObjectName("prjrow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        self._thumb = QLabel(self)
        self._thumb.setFixedSize(34, 34)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grad = THUMB_GRADIENTS[thumb]
        self._thumb_src: QPixmap | None = None
        lay.addWidget(self._thumb)
        info = QVBoxLayout()
        info.setSpacing(1)
        info.addWidget(ElidedLabel(name, "body"))
        info.addWidget(ElidedLabel(path, "mono", Qt.TextElideMode.ElideMiddle))
        lay.addLayout(info, 1)
        lay.addWidget(Chip(chip_text, chip_variant))
        self._open_btn = Button("打开", "secondary", "sm")
        self._open_btn.clicked.connect(self.open_clicked.emit)
        lay.addWidget(self._open_btn)
        self.refresh()

    def refresh(self) -> None:
        t = T.theme()
        c1, c2 = self._grad
        self.setStyleSheet(
            f"#prjrow {{ background: transparent; border-radius: {T.RADIUS_MD}px; }}"
            f"#prjrow:hover {{ background: {t.bg_hover}; }}"
        )
        self._thumb.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c1}, stop:1 {c2}); "
            f"border-radius: 9px;"
        )
        self._thumb.setPixmap(pixmap("versions", "#FFFFFF", 17))

    def set_thumbnail(self, pm: QPixmap) -> None:
        """用 .blend 内嵌缩略图填充 34px 缩略块；空图时保持渐变占位。"""
        if pm is None or pm.isNull():
            return
        self._thumb_src = pm
        dpr = self.devicePixelRatio()
        s = 34
        out = _rounded_cover(pm, round(s * dpr), round(s * dpr), 9 * dpr)
        if out.isNull():
            return
        out.setDevicePixelRatio(dpr)
        self._thumb.setStyleSheet("background: transparent;")
        self._thumb.setProperty("realThumb", True)
        self._thumb.setPixmap(out)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class FeedItem(QFrame):
    """动态流条目。"""

    def __init__(self, title: str, desc: str, icon_name: str, variant: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(10)
        lay.addWidget(BubbleIcon(icon_name, variant, self))
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addWidget(ElidedLabel(title, "body"))
        txt.addWidget(ElidedLabel(desc, "muted"))
        lay.addLayout(txt, 1)


class HeroCard(Card):
    """首页默认版本横幅：内容由真实检测快照驱动（set_install）。"""

    launch_clicked = pyqtSignal()
    dir_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(label("DEFAULT VERSION", "mono"))
        ver_row = QHBoxLayout()
        ver_row.setSpacing(8)
        self._ver_lb = label("--", "hero_ver")
        ver_row.addWidget(self._ver_lb)
        self._tag_chip = Chip("未安装", "neutral")
        ver_row.addWidget(self._tag_chip)
        self._state_chip = Chip("默认", "brand")
        ver_row.addWidget(self._state_chip)
        ver_row.addStretch(1)
        left.addLayout(ver_row)
        self._path_lb = ElidedLabel("正在检测本机 Blender 安装…", "mono", Qt.TextElideMode.ElideMiddle)
        left.addWidget(self._path_lb)
        self._meta_lb = ElidedLabel("", "muted")
        left.addWidget(self._meta_lb)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self._launch_btn = Button("启动 Blender", "primary", icon_name="play")
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        acts.addWidget(self._launch_btn)
        self._dir_btn = Button("安装目录", "secondary", icon_name="folder")
        self._dir_btn.clicked.connect(self.dir_clicked.emit)
        acts.addWidget(self._dir_btn)
        acts.addStretch(1)
        left.addLayout(acts)
        lay.addLayout(left, 1)

        self._mark = label("--", "hero_num")
        self._mark.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

    def set_install(self, inst: detect.Install | None) -> None:
        if inst is None:
            self._ver_lb.setText("--")
            self._tag_chip.setText("未安装")
            self._state_chip.setVisible(False)
            self._path_lb.setText("未检测到本机 Blender 安装")
            self._meta_lb.setText("拖入安装包或前往「版本管理」导入后即可启动")
            self._mark.setText("--")
            self._launch_btn.setEnabled(False)
            return
        self._ver_lb.setText(inst.version)
        self._tag_chip.setText(inst.tag)
        self._state_chip.setVisible(True)
        self._path_lb.setText(inst.exe)
        self._meta_lb.setText(
            f"{detect.install_age(inst.mtime)} · 占用 {detect.fmt_size(inst.size_bytes)} · {inst.name}"
        )
        self._mark.setText(inst.version)
        self._launch_btn.setEnabled(True)


# ================================================================ 首页
class HomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search = SearchBox("搜索最近项目", 260)
        self._search.textChanged.connect(self._filter_recent)
        self._search.returnPressed.connect(self._on_search)
        bell = IconButton("bell", "secondary", tip="通知")
        bell.clicked.connect(lambda: self.window().show_toast("暂无新通知", "info"))
        root, lay, self._header = _page(
            "工作台",
            "正在检测本机环境…",
            [self._search, bell],
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        stats = QGridLayout()
        stats.setSpacing(10)
        self._stats_lay = stats
        self._stat_cards: list[tuple[StatCard, int]] = []
        for i, page_idx in enumerate((1, 2, 3, 2)):
            card = StatCard("--", "检测中", "")
            card.clicked.connect(lambda _c=False, i=page_idx: self._goto(i))
            stats.addWidget(card, 0, i)
            stats.setColumnStretch(i, 1)
            self._stat_cards.append((card, page_idx))
        lay.addLayout(stats)

        hero = HeroCard()
        self._hero = hero
        hero.launch_clicked.connect(self._launch)
        hero.dir_clicked.connect(self._open_install_dir)
        lay.addWidget(hero)

        cols = QGridLayout()
        cols.setSpacing(10)
        self._cols_lay = cols
        for c in range(5):
            cols.setColumnStretch(c, 1)

        recent = Card()
        rl = QVBoxLayout(recent)
        rl.setContentsMargins(14, 14, 14, 14)
        rl.setSpacing(3)
        rl.addWidget(panel_head("最近项目", "全部 →", lambda: self._goto(2)))
        self._recent_rows: list[tuple[ProjectRow, str, str]] = []
        self._thumb_targets: dict[str, ProjectRow] = {}
        self._thumb_hooked = False
        self._recent_lay = rl
        self._recent_card = recent
        cols.addWidget(recent, 0, 0, 1, 3)

        feed = Card()
        fl = QVBoxLayout(feed)
        fl.setContentsMargins(14, 14, 14, 14)
        fl.setSpacing(3)
        fl.addWidget(panel_head("动态", "版本管理 →", lambda: self._goto(1)))
        self._feed_lay = fl
        self._feed_card = feed
        cols.addWidget(feed, 0, 3, 1, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        self._narrow = False

    # -- 响应式：窄宽度下统计卡 2×2、动态栏下移 ------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        """按可用宽度切换排布，避免内容被 QScrollArea 裁掉右侧。

        页面宽度 = 窗口宽 - 侧栏 190。窗口最小值 960 → 约 730；
        阈值 720 兼容滚动条占用，窄于此就把 4 列统计压成 2×2、两栏改上下。
        """
        narrow = self.width() < 720
        if narrow == self._narrow:
            return
        self._narrow = narrow
        for i, (card, _idx) in enumerate(self._stat_cards):
            self._stats_lay.removeWidget(card)
            if narrow:
                self._stats_lay.addWidget(card, i // 2, i % 2)
            else:
                self._stats_lay.addWidget(card, 0, i)
        for card in (self._recent_card, self._feed_card):
            self._cols_lay.removeWidget(card)
        if narrow:
            self._cols_lay.addWidget(self._recent_card, 0, 0, 1, 2)
            self._cols_lay.addWidget(self._feed_card, 1, 0, 1, 2)
        else:
            self._cols_lay.addWidget(self._recent_card, 0, 0, 1, 3)
            self._cols_lay.addWidget(self._feed_card, 0, 3, 1, 2)

    # -- 真实数据 ----------------------------------------------------
    def reload(self, snap: detect.Snapshot) -> None:
        default = snap.default_install
        self._hero.set_install(default)

        hour = datetime.now().hour
        greet = "早上好" if hour < 6 else "上午好" if hour < 12 else "下午好" if hour < 18 else "晚上好"
        if default is not None:
            self._header.set_sub(f"{greet}，Creator —— 默认版本 {default.version} {default.tag}")
        else:
            self._header.set_sub(f"{greet}，Creator —— 未检测到 Blender 安装")

        total_bytes = sum(p.size_bytes for p in snap.projects)
        last_edit = detect.rel_time(snap.projects[0].mtime) if snap.projects else "暂无项目"
        vers = sorted({a.blender_ver for a in snap.addons}, key=_ver_sort_key)
        data = [
            (str(len(snap.installs)), "已安装版本",
             f"{default.version} {default.tag} 为默认" if default else "未检测到安装"),
            (str(len(snap.projects)), "个项目",
             f"{snap.active_projects_7d} 个本周活跃"),
            (str(len(snap.addons)), "个插件",
             f"覆盖 {len(vers)} 个 Blender 版本" if vers else "未检测到插件"),
            (detect.fmt_size(total_bytes), "项目占用", f"最近编辑 {last_edit}"),
        ]
        for (card, _idx), (num, text, extra) in zip(self._stat_cards, data, strict=False):
            card.set_data(num, text, extra)

        _clear_layout(self._recent_lay)
        self._recent_lay.addWidget(
            panel_head("最近项目", "全部 →", lambda: self._goto(2))
        )
        self._recent_rows = []
        self._thumb_targets = {}
        if not snap.projects:
            self._recent_lay.addWidget(
                EmptyState("暂无项目", "扫描桌面 / 文档 / 下载目录未发现 .blend 文件")
            )
        for i, p in enumerate(snap.projects[:5]):
            row = ProjectRow(p.name, p.path, p.version, "brand", _THUMB_KEYS[i % 6])
            row.open_clicked.connect(
                lambda _c=False, n=p.name, pa=p.path: self._open_project(n, pa)
            )
            row.clicked.connect(
                lambda _c=False, n=p.name, pa=p.path: self._open_project(n, pa)
            )
            self._recent_lay.addWidget(row)
            self._recent_rows.append((row, p.name, p.path))
            self._thumb_targets[p.path] = row
        self._request_thumbs()

        _clear_layout(self._feed_lay)
        self._feed_lay.addWidget(
            panel_head("动态", "版本管理 →", lambda: self._goto(1))
        )
        items: list[tuple[str, str, str, str]] = []
        if snap.installs:
            latest = snap.latest_install
            items.append((
                f"检测到 {len(snap.installs)} 个 Blender 安装",
                f"最新 {latest.version} {latest.tag} · 扫描耗时 {snap.elapsed:.1f}s",
                "versions", "info",
            ))
            if default is not None:
                items.append((
                    f"默认版本 {default.version} {default.tag}",
                    f"{detect.install_age(default.mtime)} · 双击 .blend 时启动",
                    "check", "ok",
                ))
        else:
            items.append((
                "未检测到 Blender 安装",
                f"扫描耗时 {snap.elapsed:.1f}s · 拖入安装包即可导入",
                "alert", "warn",
            ))
        if snap.projects:
            p = snap.projects[0]
            items.append((
                f"最近编辑：{p.name}",
                f"{detect.rel_time(p.mtime)} · Blender {p.version} · {detect.fmt_size(p.size_bytes)}",
                "versions", "info",
            ))
        items.append((
            f"共检测到 {len(snap.addons)} 个插件",
            f"分布于 {len(snap.addon_roots)} 个插件目录",
            "puzzle", "ok",
        ))
        for title, desc, icon_name, variant in items[:4]:
            self._feed_lay.addWidget(FeedItem(title, desc, icon_name, variant))

    # -- 真实功能 ----------------------------------------------------
    def _goto(self, index: int) -> None:
        self.window().switch_page(index)

    # -- 缩略图 ------------------------------------------------------
    def _request_thumbs(self) -> None:
        """把当前列表中的 .blend 路径交给主窗口的缩略图加载器（后台线程提取）。"""
        loader = getattr(self.window(), "_thumbs", None)
        if loader is None:
            return
        if not self._thumb_hooked:
            loader.loaded.connect(self._on_thumb_loaded)
            self._thumb_hooked = True
        for path in self._thumb_targets:
            loader.request(path)

    def _on_thumb_loaded(self, path: str, img) -> None:
        target = self._thumb_targets.get(path)
        if target is None or img is None or img.isNull():
            return
        target.set_thumbnail(QPixmap.fromImage(img))

    def _launch(self) -> None:
        snap = self.window()._snapshot
        exe = snap.default_install.exe if snap and snap.default_install else None
        ok, msg = ops.launch_blender(exe=exe)
        if ok:
            self.window().show_toast(f"已启动 Blender：{msg}", "ok")
        else:
            self.window().show_toast(msg, "error")

    def _open_install_dir(self) -> None:
        snap = self.window()._snapshot
        inst = snap.default_install if snap else None
        path = os.path.dirname(inst.exe) if inst else ""
        ok, msg = ops.open_dir_or_fallback(path)
        if ok:
            self.window().show_toast(f"已打开目录：{msg}", "ok")
        else:
            self.window().show_toast("未找到安装目录（本机未检测到 Blender）", "info")

    def _open_project(self, name: str, path: str) -> None:
        _open_blend(self.window(), name, path)

    def _filter_recent(self, text: str) -> None:
        key = text.strip().lower()
        for row, name, path in self._recent_rows:
            row.setVisible(not key or key in name.lower() or key in path.lower())

    def _on_search(self) -> None:
        text = self._search.text().strip()
        if not text:
            return
        n = sum(1 for row, _, _ in self._recent_rows if not row.isHidden())
        self.window().show_toast(f"「{text}」匹配 {n} 个最近项目", "info")


# ================================================================ 版本管理
class VersionsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._check_btn = Button("重新扫描", "secondary", icon_name="update")
        self._check_btn.clicked.connect(self._rescan)
        add_btn = Button("添加版本", "primary", icon_name="plus")
        add_btn.clicked.connect(self._add_version)
        root, lay, self._header = _page(
            "版本管理",
            "正在检测本机安装…",
            [self._check_btn, add_btn],
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        self._seg = Segmented([("全部", "0"), ("LTS", "0"), ("测试版", "0")])
        self._seg.currentChanged.connect(self._filter_versions)
        self._seg_keys = {1: "lts", 2: "beta"}
        lay.addWidget(self._seg)

        list_card = Card()
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(0)
        self._list_lay = ll
        lay.addWidget(list_card)

        drop = DropZone()
        drop.files_dropped.connect(self._on_drop)
        lay.addWidget(drop)
        lay.addStretch(1)

    # -- 真实数据 ----------------------------------------------------
    def reload(self, snap: detect.Snapshot) -> None:
        self._header.set_sub(
            f"本机检测到 {len(snap.installs)} 个安装 · 扫描耗时 {snap.elapsed:.1f}s"
        )
        n_lts = sum(1 for i in snap.installs if i.tag == "LTS")
        n_beta = sum(1 for i in snap.installs if i.tag == "测试版")
        self._seg.set_items([
            ("全部", str(len(snap.installs))),
            ("LTS", str(n_lts)),
            ("测试版", str(n_beta)),
        ])
        self._seg_keys = {1: "lts", 2: "beta"}

        _clear_layout(self._list_lay)
        self._version_rows: list[tuple[QWidget, dict[str, bool]]] = []
        if not snap.installs:
            self._list_lay.addWidget(EmptyState(
                "未检测到 Blender 安装",
                "扫描了常见安装目录与 PATH · 拖入安装包或点击「添加版本」导入",
            ))
            return
        for inst in snap.installs:
            is_default = snap.default_exe == inst.exe
            chips: list[tuple[str, str, bool]] = []
            if is_default:
                chips.append(("默认", "brand", False))
            chips.append((inst.tag, "neutral", False))
            act_btn = Button("启动", "primary", "sm", icon_name="play")
            act_btn.clicked.connect(
                lambda _c=False, e=inst.exe, n=inst.name: self._launch(e, n)
            )
            actions = [act_btn]
            folder_btn = IconButton("folder", "secondary", tip="打开目录")
            folder_btn.clicked.connect(
                lambda _c=False, e=inst.exe, n=inst.name: self._open_dir(e, n)
            )
            actions.append(folder_btn)
            more_btn = IconButton("more", "ghost", tip="更多")
            more_btn.clicked.connect(
                lambda _c=False, n=inst.name, b=more_btn: self._version_menu(n, b)
            )
            actions.append(more_btn)
            row = VersionRow(
                inst.version, inst.tag, inst.name, chips,
                f"{os.path.dirname(inst.exe)} · {detect.install_age(inst.mtime)}",
                detect.fmt_size(inst.size_bytes), actions,
            )
            self._list_lay.addWidget(row)
            self._version_rows.append((row, {"lts": inst.tag == "LTS", "beta": inst.tag == "测试版"}))

    # -- 真实功能 ----------------------------------------------------
    def _filter_versions(self, idx: int) -> None:
        key = self._seg_keys.get(idx)
        for row, keys in self._version_rows:
            row.setVisible(key is None or keys.get(key, False))

    def _launch(self, exe: str, name: str) -> None:
        ok, msg = ops.launch_blender(exe=exe)
        if ok:
            self.window().show_toast(f"已启动 {name}：{msg}", "ok")
        else:
            self.window().show_toast(msg, "error")

    def _open_dir(self, exe: str, name: str) -> None:
        ok, msg = ops.open_dir_or_fallback(os.path.dirname(exe))
        if ok:
            self.window().show_toast(f"已打开目录：{msg}", "ok")
        else:
            self.window().show_toast(f"{name}：未找到安装目录", "info")

    def _version_menu(self, name: str, btn) -> None:
        m = _menu(self, ["设为默认"],
                  lambda pick: self._version_pick(name, pick))
        m.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    def _version_pick(self, name: str, pick: str) -> None:
        snap = self.window()._snapshot
        inst = next((i for i in snap.installs if i.name == name), None) if snap else None
        if inst is None:
            self.window().show_toast(f"{name} 已不在本机检测结果中", "info")
            return
        if pick == "设为默认":
            label_text = f"{inst.version} {inst.tag}"
            self.window().set_default_version(label_text)
            self.window().show_toast(
                f"已将 {name}（{label_text}）设为默认版本", "ok")

    def _rescan(self) -> None:
        self._check_btn.set_busy(True)
        QTimer.singleShot(60, self._rescan_done)

    def _rescan_done(self) -> None:
        self._check_btn.set_busy(False)
        self.window().rescan()
        snap = self.window()._snapshot
        self.window().show_toast(
            f"已重新扫描：{len(snap.installs)} 个安装 · {len(snap.projects)} 个项目"
            f" · {len(snap.addons)} 个插件（{snap.elapsed:.1f}s）", "ok",
        )

    def _add_version(self) -> None:
        with ND.guard_native_dialog(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 Blender 安装包", "", "压缩包 (*.zip *.tar.xz *.tar.gz)"
            )
        if path:
            self._import_archive(path)

    def _on_drop(self, paths: list[str]) -> None:
        if paths:
            self._import_archive(paths[0])

    def _import_archive(self, path: str) -> None:
        dest = store.load()["install_dir"]
        ok, msg = detect.extract_archive(path, dest)
        if not ok:
            self.window().show_toast(msg, "error")
            return
        self.window().rescan()
        snap = self.window()._snapshot
        self.window().show_toast(
            f"已导入 {os.path.basename(path)} 到 {dest}，检测到 {len(snap.installs)} 个安装", "ok",
        )


# ================================================================ 项目
class ProjectsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        new_btn = Button("新建项目", "primary", icon_name="plus")
        new_btn.clicked.connect(self._new_project)
        self._del_btn = Button("批量删除", "secondary", icon_name="trash")
        self._del_btn.setToolTip("进入多选后勾选项目，删除会移入回收站（可恢复）")
        self._del_btn.clicked.connect(self._toggle_select_mode)
        self._all_btn = Button("全选", "ghost", "sm")
        self._all_btn.setToolTip("全选 / 取消全选 当前可见项目（Ctrl+A）")
        self._all_btn.clicked.connect(self._toggle_select_all_visible)
        self._all_btn.setVisible(False)
        self._exit_btn = Button("退出多选", "ghost", "sm")
        self._exit_btn.setToolTip("退出多选模式（Esc）")
        self._exit_btn.clicked.connect(self._exit_select_mode)
        self._exit_btn.setVisible(False)
        self._search = SearchBox("搜索项目", 220)
        self._search.textChanged.connect(self._apply_filters)
        root, lay, self._header = _page(
            "项目",
            "正在扫描本机项目…",
            [self._search, new_btn, self._del_btn, self._all_btn, self._exit_btn],
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        self._chip_idx = 0
        self._chip_versions: list[str] = []
        self._chips = ChipFilter(["全部"])
        self._chips.currentChanged.connect(self._on_chip)
        lay.addWidget(self._chips)

        self._grid = QGridLayout()
        self._grid.setSpacing(_GRID_GAP)
        grid_host = QWidget()
        grid_host.setLayout(self._grid)
        lay.addWidget(grid_host)
        lay.addStretch(1)
        self._grid_widgets: list[QWidget] = []
        self._empty: QWidget | None = None
        self._cols = 0
        self._grid_timer = QTimer(self)
        self._grid_timer.setSingleShot(True)
        self._grid_timer.setInterval(60)
        self._grid_timer.timeout.connect(self._arrange_grid)
        self._thumb_targets: dict[str, MediaCard] = {}
        self._thumb_hooked = False
        self._sel: set[str] = set()
        self._select_mode = False
        # 多选快捷键：Ctrl+A 全选/取消全选，Esc 退出多选
        self._sc_all = QShortcut(QKeySequence.StandardKey.SelectAll, self)
        self._sc_all.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._sc_all.activated.connect(self._toggle_select_all_visible)
        self._sc_esc = QShortcut(QKeySequence(QKeySequence.StandardKey.Cancel), self)
        self._sc_esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._sc_esc.activated.connect(self._exit_select_mode)

    # -- 布局 --------------------------------------------------------
    def _arrange_grid(self, force: bool = False) -> None:
        cols = _grid_cols(self.width())
        if not force and cols == self._cols:
            return
        self._cols = cols
        _regrid(self._grid, self._grid_widgets, self._empty, cols)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (self._grid_widgets or self._empty is not None) \
                and _grid_cols(self.width()) != self._cols:
            self._grid_timer.start()

    # -- 真实数据 ----------------------------------------------------
    def reload(self, snap: detect.Snapshot) -> None:
        self._header.set_sub(
            f"{len(snap.projects)} 个项目 · {snap.active_projects_7d} 个本周活跃"
        )
        counts: dict[str, int] = {}
        for p in snap.projects:
            counts[p.version] = counts.get(p.version, 0) + 1
        self._chip_versions = sorted(
            (v for v in counts if v != "未知"), key=_ver_sort_key, reverse=True
        )
        items = [f"全部 {len(snap.projects)}"]
        items += [f"{v} · {counts[v]}" for v in self._chip_versions]
        if "未知" in counts:
            items.append(f"未知 · {counts['未知']}")
        items.append("最近打开")
        self._chips.set_items(items)
        self._chip_idx = 0

        _clear_layout(self._grid)
        self._cards: list[tuple[QWidget, str, str, str, float]] = []
        self._thumb_targets = {}
        self._grid_widgets = []
        self._empty = None
        # 上一次多选里已被删除 / 移动的项目不再参与选择态
        live = {p.path for p in snap.projects}
        self._sel = {p for p in self._sel if p in live}
        if not snap.projects:
            self._empty = EmptyState(
                "未检测到 .blend 项目",
                "扫描桌面 / 文档 / 下载 / 安装目录与 Blender 最近打开记录",
            )
            self._arrange_grid(force=True)
            self._sync_sel_ui()
            return
        for i, p in enumerate(snap.projects):
            act = Button("打开", "ghost", "sm", icon_name="folder")
            meta = f"{detect.fmt_size(p.size_bytes)} · {detect.rel_time(p.mtime)}"
            card = MediaCard(
                p.name, p.path, meta, act,
                thumb=_THUMB_KEYS[i % 6], thumb_icon="versions",
                chips=[(f"v{p.version}", "info")],
            )
            act.clicked.connect(
                lambda _c=False, n=p.name, pa=p.path: self._open_project(n, pa)
            )
            card.clicked.connect(
                lambda _c=False, n=p.name, pa=p.path: self._card_clicked(n, pa)
            )
            card.set_selectable(self._select_mode)
            card.set_selected(p.path in self._sel)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._grid_widgets.append(card)
            self._cards.append((card, p.name, p.path, p.version, p.mtime))
            self._thumb_targets[p.path] = card
        new = NewCard("新建项目")
        new.clicked.connect(self._new_project)
        self._grid_widgets.append(new)
        self._arrange_grid(force=True)
        self._request_thumbs()
        self._sync_sel_ui()

    # -- 缩略图 ------------------------------------------------------
    def _request_thumbs(self) -> None:
        """把当前列表中的 .blend 路径交给主窗口的缩略图加载器（后台线程提取）。"""
        loader = getattr(self.window(), "_thumbs", None)
        if loader is None:
            return
        if not self._thumb_hooked:
            loader.loaded.connect(self._on_thumb_loaded)
            self._thumb_hooked = True
        for path in self._thumb_targets:
            loader.request(path)

    def _on_thumb_loaded(self, path: str, img) -> None:
        target = self._thumb_targets.get(path)
        if target is None or img is None or img.isNull():
            return
        target.set_thumbnail(QPixmap.fromImage(img))

    # -- 真实功能 ----------------------------------------------------
    def _on_chip(self, idx: int) -> None:
        self._chip_idx = idx
        self._apply_filters()

    def _apply_filters(self, _text: str = "") -> None:
        key = self._search.text().strip().lower()
        for card, name, path, _ver, _mtime in self._cards:
            ok_search = not key or key in name.lower() or key in path.lower()
            _set_filtered(card, ok_search and self._chip_match(card))
        # 重新紧凑排列：隐藏卡片不占网格位
        self._arrange_grid(force=True)
        # 可见集合变了，「全选 / 取消全选」的判定要跟着更新
        if self._select_mode:
            self._sync_sel_ui()

    def _chip_match(self, card: QWidget) -> bool:
        if self._chip_idx <= 0:
            return True
        entry = next((c for c in self._cards if c[0] is card), None)
        if entry is None:
            return True
        _card, _name, _path, ver, mtime = entry
        if self._chip_idx == len(self._chip_versions) + 1 + (1 if any(
            c[3] == "未知" for c in self._cards
        ) else 0):
            return mtime >= time.time() - 7 * 86400
        if ver == "未知":
            return self._chip_idx == len(self._chip_versions) + 1
        if self._chip_idx > len(self._chip_versions):
            # 「未知」胶囊（idx = len+1）不在版本列表索引范围内，
            # 已知版本的项目一律不匹配；缺少这道守卫会 IndexError
            return False
        return self._chip_versions[self._chip_idx - 1] == ver

    def _open_project(self, name: str, path: str) -> None:
        _open_blend(self.window(), name, path)

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip():
            return
        base = store.load()["install_dir"]
        target = os.path.join(base, "projects", name.strip())
        try:
            os.makedirs(target, exist_ok=True)
            self.window().show_toast(f"已创建项目目录：{target}", "ok")
        except OSError as e:
            self.window().show_toast(f"创建失败：{e}", "error")

    # -- 批量删除（移入回收站，可恢复）-------------------------------
    def _card_clicked(self, name: str, path: str) -> None:
        if not self._select_mode:
            self._open_project(name, path)
            return
        card = self._thumb_targets.get(path)
        if path in self._sel:
            self._sel.discard(path)
            if card is not None:
                card.set_selected(False)
        else:
            self._sel.add(path)
            if card is not None:
                card.set_selected(True)
        self._sync_sel_ui()

    def _toggle_select_mode(self) -> None:
        if self._select_mode:
            self._delete_selected()
            return
        self._select_mode = True
        for card, *_ in self._cards:
            card.set_selectable(True)
        self._all_btn.setVisible(True)
        self._exit_btn.setVisible(True)
        self._sync_sel_ui()

    def _exit_select_mode(self) -> None:
        if not self._select_mode:
            return
        self._select_mode = False
        self._sel.clear()
        for card, *_ in self._cards:
            card.set_selectable(False)
        self._all_btn.setVisible(False)
        self._exit_btn.setVisible(False)
        self._sync_sel_ui()

    def _visible_cards(self) -> list[tuple[QWidget, str, str, str, float]]:
        """当前通过搜索/筛选、实际可见的卡片条目。"""
        return [c for c in self._cards if c[0].isVisible()]

    def _toggle_select_all_visible(self) -> None:
        """全选 / 取消全选 当前可见项目（已在 search 框内时不拦截 Ctrl+A）。"""
        if self._search.hasFocus():
            return
        if not self._select_mode:
            self._toggle_select_mode()
            if not self._select_mode:
                return
        vis = self._visible_cards()
        all_on = bool(vis) and all(p in self._sel for _c, _n, p, _v, _m in vis)
        for card, _name, path, _ver, _mtime in vis:
            self._sel.add(path) if not all_on else self._sel.discard(path)
            card.set_selected(not all_on)
        self._sync_sel_ui()

    def _sync_sel_ui(self) -> None:
        """按选择态刷新顶部三个按钮的文案 / 变体 / 可见性。"""
        n = len(self._sel)
        if self._select_mode:
            self._del_btn.setText(f"删除所选（{n}）" if n else "删除所选")
            self._del_btn.set_variant("danger" if n else "secondary")
            self._del_btn.setEnabled(n > 0)
            vis = self._visible_cards()
            all_on = bool(vis) and all(p in self._sel for _c, _n, p, _v, _m in vis)
            self._all_btn.setText("取消全选" if all_on else "全选")
            self._all_btn.setEnabled(bool(vis))
        else:
            self._del_btn.setText("批量删除")
            self._del_btn.set_variant("secondary")
            self._del_btn.setEnabled(True)
            self._all_btn.setText("全选")
            self._all_btn.setEnabled(True)

    def _sel_total_size(self) -> int:
        """所选项目文件在磁盘上的实际占用总字节数（不存在的文件按 0 计）。"""
        total = 0
        for path in self._sel:
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
        return total

    def _delete_selected(self) -> None:
        paths = sorted(self._sel)
        if not paths:
            return
        names = [n for _c, n, p, _v, _m in self._cards if p in self._sel]
        shown = "、".join(names[:4]) + (" 等" if len(names) > 4 else "")
        size = detect.fmt_size(self._sel_total_size())
        if not confirm(
            self, "批量删除项目",
            f"确定将 {len(paths)} 个项目（{size}）移入回收站吗？\n{shown}\n\n"
            "文件不会立即永久删除，之后可从回收站恢复。",
            ok_text="移入回收站",
        ):
            return
        ok, msg = ops.recycle_paths(paths)
        if not ok:
            self.window().show_toast(f"删除失败：{msg}", "error")
            return
        self._exit_select_mode()
        self.window().rescan()
        self.window().show_toast(
            f"已将 {len(paths)} 个项目（{size}）移入回收站（可恢复）", "ok")


# ================================================================ 插件
class AddonsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "local"
        self._chip_idx = 0
        self._chip_categories: list[str] = []
        self._store_chip_idx = 0
        self._cards: list[tuple[QWidget, detect.Addon]] = []
        self._store_cards: list[tuple[MediaCard, StoreAddon]] = []
        self._store_hooked = False
        self._auth_hooked = False
        self._busy_ids: set[str] = set()
        self._installed_names: set[str] = set()
        self._store_categories: list[str] = []
        self._store_kicked = False
        self._thumb_requested: set[str] = set()
        self._thumb_queue: list[StoreAddon] = []

        self._mode_seg = Segmented([("本地", "0"), ("在线", "0")])
        self._mode_seg.currentChanged.connect(self._on_mode)
        self._login_btn = Button("登录", "ghost", icon_name="folder")
        self._login_btn.clicked.connect(self._open_login)
        self._refresh_btn = Button("刷新", "secondary", icon_name="update")
        self._refresh_btn.clicked.connect(self._refresh)
        self._search = SearchBox("搜索插件", 220)
        self._search.textChanged.connect(self._apply_filters)
        root, lay, self._header = _page(
            "插件",
            "正在扫描插件目录…",
            [self._search, self._mode_seg, self._login_btn, self._refresh_btn],
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        self._chips = ChipFilter(["全部"])
        self._chips.currentChanged.connect(self._on_chip)
        lay.addWidget(self._chips)

        self._grid = QGridLayout()
        self._grid.setSpacing(_GRID_GAP)
        grid_host = QWidget()
        grid_host.setLayout(self._grid)
        lay.addWidget(grid_host)
        lay.addStretch(1)
        self._grid_widgets: list[QWidget] = []
        self._empty: QWidget | None = None
        self._cols = 0
        self._grid_timer = QTimer(self)
        self._grid_timer.setSingleShot(True)
        self._grid_timer.setInterval(60)
        self._grid_timer.timeout.connect(self._arrange_grid)

    # -- 布局 --------------------------------------------------------
    def _arrange_grid(self, force: bool = False) -> None:
        cols = _grid_cols(self.width())
        if not force and cols == self._cols:
            return
        self._cols = cols
        # 在线目录可达 240 张卡，隐藏/恢复全量重排要数秒，保留原占位行为
        _regrid(self._grid, self._grid_widgets, self._empty, cols,
                compact=self._mode == "local")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (self._grid_widgets or self._empty is not None) \
                and _grid_cols(self.width()) != self._cols:
            self._grid_timer.start()

    # -- 真实数据 ----------------------------------------------------
    def reload(self, snap: detect.Snapshot) -> None:
        self._installed_names = {
            a.name.strip().lower() for a in snap.addons if a.name
        }
        store_ = self._store
        self._ensure_hooks()
        self._kick_store_once()
        self._mode_seg.set_items([
            ("本地", str(len(snap.addons))),
            ("在线", str(len(store_.addons)) if store_ else "0"),
        ])
        self._mode_seg.set_current(0 if self._mode == "local" else 1)
        if self._mode == "online":
            self._reload_online()
            return
        self._reload_local(snap)

    def _reload_local(self, snap: detect.Snapshot) -> None:
        self._header.set_sub(
            f"{len(snap.addons)} 个插件 · 分布于 {len(snap.addon_roots)} 个插件目录"
        )
        cats: dict[str, int] = {}
        for a in snap.addons:
            cat = snap.addon_chip_category(a)
            cats[cat] = cats.get(cat, 0) + 1
        self._chip_categories = [
            c for c, _ in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:4]
        items = [f"全部 {len(snap.addons)}"]
        items += [f"{c} · {cats[c]}" for c in self._chip_categories]
        self._chips.set_items(items)
        self._chip_idx = 0

        _clear_layout(self._grid)
        self._cards: list[tuple[QWidget, detect.Addon]] = []
        self._grid_widgets = []
        self._empty = None
        if not snap.addons:
            self._empty = EmptyState(
                "未检测到已安装插件",
                "扫描各 Blender 安装与用户配置目录的 scripts/addons 与 extensions",
            )
            self._arrange_grid(force=True)
            return
        for i, a in enumerate(snap.addons):
            dir_btn = Button("目录", "ghost", "sm", icon_name="folder")
            chips = [(snap.addon_chip_category(a), "info"),
                     (f"Blender {a.blender_ver}", "info")]
            # 随发行包自带的内置插件在线市场没有对应条目、拿不到封面；
            # 标注来源，避免看起来像「封面没加载出来」
            if a.builtin:
                chips.append(("内置 · 无在线封面", "neutral"))
            card = MediaCard(
                a.name,
                f"by {a.author or '未知作者'} · v{a.version or '?'}",
                detect.fmt_size(a.size_bytes),
                dir_btn,
                thumb=_THUMB_KEYS[i % 6], thumb_icon="puzzle",
                chips=chips,
            )
            dir_btn.clicked.connect(
                lambda _c=False, p=a.path, n=a.name: self._open_addon_dir(n, p)
            )
            card.clicked.connect(
                lambda _c=False, p=a.path, n=a.name: self._open_addon_dir(n, p)
            )
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._grid_widgets.append(card)
            self._cards.append((card, a))
        new = NewCard("从 ZIP 安装", icon_name="plus")
        new.clicked.connect(self._install_from_zip)
        self._grid_widgets.append(new)
        self._arrange_grid(force=True)
        self._apply_local_thumbs()

    def _apply_local_thumbs(self) -> None:
        """本地插件卡片按名称匹配在线目录缩略图。

        已缓存的直接贴；命中但未下载的排进按需队列，
        完成后经 thumbs_done 再次进入本方法补齐。
        """
        st = self._store
        if st is None:
            return
        pending: list[StoreAddon] = []
        for card, a in self._cards:
            hit = st.find_local(a.name)
            if hit is None:
                continue
            tp = st.thumb_pixmap_path(hit)
            if tp:
                card.set_thumbnail(QPixmap(tp))
            elif hit.thumbnail_url and hit.id not in self._thumb_requested:
                self._thumb_requested.add(hit.id)
                pending.append(hit)
        self._queue_thumbs(st, pending)

    # -- 真实功能 ----------------------------------------------------
    def _on_chip(self, idx: int) -> None:
        if self._mode == "online":
            self._store_chip_idx = idx
        else:
            self._chip_idx = idx
        self._apply_filters()

    def _apply_filters(self, _text: str = "") -> None:
        if self._mode == "online":
            key = self._search.text().strip().lower()
            cat = (self._store_categories[self._store_chip_idx]
                   if 0 <= self._store_chip_idx < len(self._store_categories)
                   else "全部")
            for card, addon in self._store_cards:
                _set_filtered(card, self._store_match(addon, key, cat))
            return
        key = self._search.text().strip().lower()
        for card, addon in self._cards:
            ok = not key or key in addon.name.lower() or key in (addon.author or "").lower()
            _set_filtered(card, ok and self._chip_match(addon))
        self._arrange_grid(force=True)

    def _chip_match(self, addon: detect.Addon) -> bool:
        if self._chip_idx <= 0:
            return True
        cat = self._snapshot.addon_chip_category(addon)
        return self._chip_categories[self._chip_idx - 1] == cat

    @property
    def _store(self):
        win = self.window()
        return getattr(win, "_store", None) if win is not None else None

    @property
    def _auth(self):
        win = self.window()
        return getattr(win, "_auth", None) if win is not None else None

    @property
    def _snapshot(self) -> detect.Snapshot:
        return self.window()._snapshot

    def _open_addon_dir(self, name: str, path: str) -> None:
        ok, msg = ops.open_dir_or_fallback(path)
        if ok:
            self.window().show_toast(f"已打开 {name} 目录：{msg}", "ok")
        else:
            self.window().show_toast(f"{name}：目录不存在", "info")

    def _kick_store_once(self) -> None:
        """本地目录为空时后台静默拉取一次，供本地卡片匹配在线缩略图。"""
        if self._store_kicked:
            return
        self._store_kicked = True
        st = self._store
        if st is None or st.refreshing or st.addons or not has_net_deps():
            return
        st.refresh_async(incremental=0)

    def _refresh(self) -> None:
        if self._mode == "online":
            st = self._store
            if st is None:
                return
            if st.refreshing:
                self.window().show_toast("正在拉取中，请稍候…", "info")
                return
            if st.refresh_async(force=True):
                self._refresh_btn.set_busy(True)
                self._header.set_sub("正在从 BudecoTop 拉取插件目录…")
            return
        self._refresh_btn.set_busy(True)
        QTimer.singleShot(60, self._refresh_done)

    def _refresh_done(self) -> None:
        self._refresh_btn.set_busy(False)
        self.window().rescan()
        snap = self._snapshot
        self.window().show_toast(
            f"已刷新插件列表：{len(snap.addons)} 个", "ok",
        )

    def _install_from_zip(self) -> None:
        with ND.guard_native_dialog(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择插件安装包", "", "ZIP 压缩包 (*.zip)"
            )
        if not path:
            return
        verifier = AddonVerifier()
        result = verifier.verify_file(path)
        if not result.ok:
            self.window().show_toast(result.summary(), "error")
            return
        if result.requires_user_confirmation and not confirm(
                self, "安全提示",
                "该安装包包含可执行文件，正常插件不需要：\n"
                + "、".join(result.risky_payloads[:5])
                + "\n\n仍要安装吗？"):
            self.window().show_toast("已取消安装", "info")
            return
        ok, msg, name = install_zip(path)
        if ok:
            verifier.record_audit(f"local:{name}", name, path, result)
            self.window().rescan()
            self.window().show_toast(msg, "ok")
        else:
            self.window().show_toast(msg, "error")

    # -- 在线模式（BudecoTop） ---------------------------------------
    def _on_mode(self, idx: int) -> None:
        self._mode = "local" if idx == 0 else "online"
        if self._mode == "online":
            self._enter_online()
        else:
            self._enter_local()

    def _enter_local(self) -> None:
        self._reload_local(self._snapshot)

    def _enter_online(self) -> None:
        st = self._store
        if st is None:
            self.window().show_toast("插件市场服务未初始化", "error")
            return
        self._ensure_hooks()
        if not has_net_deps():
            self.window().show_toast(
                "缺少 requests / beautifulsoup4，无法拉取在线目录"
                "（请先 pip install -r requirements.txt）", "error")
        self._reload_online()
        if not st.refreshing and st.refresh_async(incremental=0):
            self.window().show_toast("正在从 BudecoTop 拉取插件目录…", "info")
        auth = self._auth
        if auth is not None and auth.available:
            bridge = NetBridge(self)
            bridge.payload.connect(self._on_login_verified)
            QThreadPool.globalInstance().start(VerifyLoginTask(auth, bridge))

    def _ensure_hooks(self) -> None:
        st = self._store
        if st is not None and not self._store_hooked:
            st.fetch_done.connect(self._on_fetch_done)
            st.thumbs_done.connect(self._on_thumbs_done)
            self._store_hooked = True
        auth = self._auth
        if auth is not None and not self._auth_hooked:
            auth.login_changed.connect(self._on_login_changed)
            self._auth_hooked = True

    def _on_login_changed(self, logged_in: bool) -> None:
        self._login_btn.setText("已登录" if logged_in else "登录")

    def _on_login_verified(self, ok) -> None:
        self._login_btn.setText("已登录" if ok else "登录")

    def _on_fetch_done(self, ok: bool, msg: str) -> None:
        self._refresh_btn.set_busy(False)
        if self._mode != "online":
            return
        self.window().show_toast(msg, "ok" if ok else "error")
        self._reload_online()

    # -- 缩略图按需下载队列 ------------------------------------------
    def _queue_thumbs(self, st, addons: list[StoreAddon]) -> None:
        """把「已展示但还没封面」的条目排进队列并尝试下发。

        AddonStore 同一时刻只跑一个缩略图任务，正在忙时直接调用会把请求丢掉，
        这里用页面侧队列兜住，任务结束后再补发。
        下载失败的 id 已经在调用方记进 _thumb_requested，不会空转重试。
        """
        if st is None or not addons:
            return
        self._thumb_queue.extend(addons)
        self._flush_thumb_queue(st)

    def _flush_thumb_queue(self, st) -> None:
        if st is None or not self._thumb_queue or st.thumb_busy:
            return
        batch = self._thumb_queue[:AddonStore.ONDEMAND_THUMBS]
        rest = self._thumb_queue[AddonStore.ONDEMAND_THUMBS:]
        self._thumb_queue = rest
        if not st.download_thumbs_async(batch):
            # 被并发抢占：整批还回队列，等下次 thumbs_done / 本Round 结束再试
            self._thumb_queue = batch + self._thumb_queue

    def _on_thumbs_done(self) -> None:
        self._flush_thumb_queue(self._store)
        if self._mode == "online":
            self._reload_online()
        else:
            self._apply_local_thumbs()

    def _store_match(self, addon: StoreAddon, key: str, cat: str) -> bool:
        if key and key not in addon.name.lower() \
                and key not in addon.description.lower():
            return False
        if cat != "全部" and addon.category != cat:
            return False
        return True

    def _is_installed(self, addon: StoreAddon) -> bool:
        name = addon.name.strip().lower()
        if not name:
            return False
        for local in self._installed_names:
            if local == name:
                return True
            if len(name) >= 4 and (name in local or local in name):
                return True
        return False

    def _reload_online(self) -> None:
        st = self._store
        addons = st.addons if st else []
        self._store_categories = list(CATEGORY_CHIPS)
        # 在线分类用纯名称（不带计数），set_items 相等时不会重建、不重置用户选择
        self._chips.set_items(self._store_categories)
        key = self._search.text().strip().lower()
        cat = (self._store_categories[self._store_chip_idx]
               if 0 <= self._store_chip_idx < len(self._store_categories)
               else "全部")
        shown = [a for a in addons if self._store_match(a, key, cat)]

        if st is not None and st.refreshing:
            sub = "正在从 BudecoTop 拉取插件目录…"
        elif not addons:
            sub = "BudecoTop 在线市场 · 尚未拉取目录（点「刷新」开始）"
        elif st is not None and st.last_refresh_ts:
            ts = time.strftime("%m-%d %H:%M", time.localtime(st.last_refresh_ts))
            sub = f"BudecoTop 在线市场 · 共 {len(addons)} 个 · 更新于 {ts}"
        else:
            sub = f"BudecoTop 在线市场 · 共 {len(addons)} 个"
        if len(shown) > _ONLINE_CAP:
            # 目录可达上千条，全量建卡会长时间卡界面；截断并提示可用搜索/分类缩小
            sub += (f" · 显示前 {_ONLINE_CAP} / {len(shown)}"
                    "（可搜索或选分类缩小范围）")
            shown = shown[:_ONLINE_CAP]
        self._header.set_sub(sub)

        _clear_layout(self._grid)
        self._store_cards = []
        self._grid_widgets = []
        self._empty = None
        need_thumbs: list[StoreAddon] = []
        if not shown:
            if st is not None and st.refreshing:
                empty = EmptyState("正在拉取插件目录…", "首次拉取需要一些时间，请稍候")
            elif not addons:
                empty = EmptyState(
                    "暂无在线插件", "点右上角「刷新」从 BudecoTop 拉取插件目录")
            else:
                empty = EmptyState("没有匹配的插件", "换个关键词或分类试试")
            self._empty = empty
            self._arrange_grid(force=True)
            return
        for i, a in enumerate(shown):
            installed = self._is_installed(a)
            dl_btn = Button("安装", "primary", "sm", icon_name="download")
            dl_btn.clicked.connect(
                lambda _c=False, x=a: self._download_addon(x))
            chips = [(a.category, "info")]
            if installed:
                chips.append(("已安装", "success"))
            elif a.tags:
                chips.append((a.tags[0], "info"))
            card = MediaCard(
                a.name, f"by {a.author or '未知作者'}", a.price, dl_btn,
                thumb=_THUMB_KEYS[i % 6], thumb_icon="download", chips=chips,
            )
            tp = st.thumb_pixmap_path(a) if st else ""
            if tp:
                card.set_thumbnail(QPixmap(tp))
            elif st is not None and a.thumbnail_url \
                    and a.id not in self._thumb_requested:
                # 全量下载有上限（MAX_THUMBS），可见项缺图时按需补；
                # 失败不重试，避免断网时空转
                self._thumb_requested.add(a.id)
                need_thumbs.append(a)
            card.clicked.connect(
                lambda _c=False, x=a, p=tp: self._open_detail(x, p))
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._grid_widgets.append(card)
            self._store_cards.append((card, a))
        self._arrange_grid(force=True)
        self._queue_thumbs(st, need_thumbs)

    def _open_detail(self, addon: StoreAddon, thumb_path: str) -> None:
        st = self._store
        tp = thumb_path or (st.thumb_pixmap_path(addon) if st else "")
        dlg = StoreDetailDialog(addon, tp, self._is_installed(addon), self)
        dlg.install_requested.connect(lambda: self._download_addon(addon))
        dlg.exec()

    def _open_login(self) -> None:
        auth = self._auth
        if auth is None:
            return
        StoreLoginDialog(auth, self).exec()

    # -- 下载 / 安装链路 ---------------------------------------------
    def _download_addon(self, addon: StoreAddon) -> None:
        if addon.id in self._busy_ids:
            return
        auth = self._auth
        if auth is None:
            return
        if not auth.logged_in:
            dlg = StoreLoginDialog(auth, self)
            if dlg.exec() != QDialog.DialogCode.Accepted or not auth.logged_in:
                self.window().show_toast(
                    "需要登录 BudecoTop 后才能下载插件", "info")
                return
        self._busy_ids.add(addon.id)
        self._header.set_sub(f"正在解析「{addon.name}」的下载链…")
        bridge = NetBridge(self)
        bridge.payload.connect(lambda t: self._on_resolved(addon, t))
        QThreadPool.globalInstance().start(
            ResolveTask(auth, addon.source_url, bridge))

    def _on_resolved(self, addon: StoreAddon, target) -> None:
        if not isinstance(target, DownloadTarget) or not target.ok:
            self._busy_ids.discard(addon.id)
            self._reload_online()
            note = target.note if isinstance(target, DownloadTarget) else ""
            self.window().show_toast(note or "解析下载链失败", "error")
            return
        if target.kind == "pan":
            dlg = PanDownloadDialog(addon, target.url, target.code, self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.zip_path:
                self._verify_and_install(addon, dlg.zip_path)
            else:
                self._busy_ids.discard(addon.id)
                self._reload_online()
            return
        auth = self._auth
        if auth is None:
            self._busy_ids.discard(addon.id)
            return
        dl_dir = os.path.join(cache_root(), "downloads")
        try:
            os.makedirs(dl_dir, exist_ok=True)
        except OSError as e:
            self._busy_ids.discard(addon.id)
            self.window().show_toast(f"创建下载目录失败：{e}", "error")
            return
        safe = re.sub(r'[\\/:*?"<>|]', "_", addon.name).strip() or "addon"
        dest = os.path.join(dl_dir, f"{safe}.zip")
        self._header.set_sub(f"正在下载「{addon.name}」… 0%")
        bridge = NetBridge(self)
        bridge.progress.connect(
            lambda pct: self._header.set_sub(f"正在下载「{addon.name}」… {pct}%"))
        bridge.finished.connect(
            lambda ok, msg: self._on_downloaded(addon, dest, ok, msg))
        QThreadPool.globalInstance().start(
            DirectDownloadTask(auth, target.url, dest, bridge))

    def _on_downloaded(self, addon: StoreAddon, dest: str,
                       ok: bool, msg: str) -> None:
        if not ok:
            self._busy_ids.discard(addon.id)
            self._reload_online()
            self.window().show_toast(msg, "error")
            return
        self._verify_and_install(addon, dest)

    def _verify_and_install(self, addon: StoreAddon, zip_path: str) -> None:
        self._header.set_sub(f"正在校验「{addon.name}」安装包…")
        bridge = NetBridge(self)
        bridge.payload.connect(
            lambda r: self._on_verified(addon, zip_path, r))
        QThreadPool.globalInstance().start(
            VerifyZipTask(AddonVerifier(), zip_path, bridge))

    def _on_verified(self, addon: StoreAddon, zip_path: str,
                     result: VerifyResult) -> None:
        if not isinstance(result, VerifyResult):
            self._busy_ids.discard(addon.id)
            self.window().show_toast("校验结果异常", "error")
            return
        if not result.ok:
            self._busy_ids.discard(addon.id)
            self._reload_online()
            self.window().show_toast(result.summary(), "error")
            return
        if result.requires_user_confirmation and not confirm(
                self, "安全提示",
                f"「{addon.name}」的安装包包含可执行文件，正常插件不需要：\n"
                + "、".join(result.risky_payloads[:5])
                + "\n\n仍要安装吗？"):
            self._busy_ids.discard(addon.id)
            self._reload_online()
            self.window().show_toast("已取消安装", "info")
            return
        self._header.set_sub(f"正在安装「{addon.name}」…")
        bridge = NetBridge(self)
        bridge.finished.connect(
            lambda ok, msg: self._on_installed(addon, ok, msg))
        QThreadPool.globalInstance().start(
            InstallZipTask(AddonVerifier(), zip_path, addon, result, bridge))

    def _on_installed(self, addon: StoreAddon, ok: bool, msg: str) -> None:
        self._busy_ids.discard(addon.id)
        self.window().rescan()
        self.window().show_toast(msg, "ok" if ok else "error")


# ================================================================ 设置
class SettingsPage(QWidget):
    theme_requested = pyqtSignal(str)  # "dark" / "light" / "system"

    def __init__(self, parent=None):
        super().__init__(parent)
        data = store.load()
        self._save_btn = Button("保存", "primary")
        self._save_btn.clicked.connect(self._save)
        reset_btn = Button("恢复默认", "ghost")
        reset_btn.clicked.connect(self._reset)
        root, lay, _header = _page(
            "设置",
            "BlenderHub v1.0.0",
            [reset_btn, self._save_btn],
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        body = QHBoxLayout()
        body.setSpacing(16)
        nav = VerticalNav(["常规", "版本与安装", "插件", "网络", "关于"])
        nav.setFixedWidth(140)
        body.addWidget(nav)

        form = Card()
        fl = QVBoxLayout(form)
        fl.setContentsMargins(16, 6, 16, 6)
        fl.setSpacing(0)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self._dir_input = QLineEdit(data["install_dir"])
        self._dir_input.setMinimumWidth(280)
        dir_row.addWidget(self._dir_input)
        browse_btn = Button("浏览…", "secondary", "sm")
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(browse_btn)
        fl.addWidget(SettingRow("安装目录", "新版本默认安装位置", self._wrap(dir_row)))

        fl.addWidget(hline())
        default_row = QHBoxLayout()
        default_row.setSpacing(8)
        self._default_chip = Chip(data["default_version"], "brand")
        default_row.addWidget(self._default_chip)
        self._change_btn = Button("更改", "ghost", "sm")
        self._change_btn.clicked.connect(self._change_default)
        default_row.addWidget(self._change_btn)
        fl.addWidget(SettingRow("默认版本", "双击 .blend 文件时启动", self._wrap(default_row)))

        fl.addWidget(hline())
        self._theme_cards = RadioCardGroup(
            [("深色", "默认推荐"), ("浅色", "明亮环境"), ("跟随系统", "自动切换")],
            current=0 if T.theme().name == "dark" else 1,
        )
        self._theme_cards.currentChanged.connect(self._on_theme)
        fl.addWidget(SettingRow("主题", "深浅双主题即时生效", self._theme_cards))

        fl.addWidget(hline())
        self._update_toggle = Toggle(data["check_updates"])
        fl.addWidget(SettingRow("启动时检查更新", "每日一次，静默检查", self._update_toggle))

        fl.addWidget(hline())
        self._conc = QLineEdit(str(data["concurrency"]))
        self._conc.setFixedWidth(120)
        fl.addWidget(SettingRow("下载并发数", "同时下载的任务上限", self._conc))

        body.addWidget(form, 1)
        lay.addLayout(body)

        about = Card()
        al = QHBoxLayout(about)
        al.setContentsMargins(14, 12, 14, 12)
        logo = QLabel(self)
        logo.setFixedSize(40, 40)
        logo.setPixmap(pixmap("logo", "", 40))
        al.addWidget(logo)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.addWidget(label("BlenderHub", "h2"))
        txt.addWidget(label("Blender 版本 / 项目 / 插件一体化管理", "muted"))
        al.addLayout(txt, 1)
        ver = QVBoxLayout()
        ver.setSpacing(2)
        ver.addWidget(label("v1.0.0", "muted2"))
        ver.addWidget(label("© 2026 ZL Studio", "muted"))
        al.addLayout(ver)
        lay.addWidget(about)
        lay.addStretch(1)

    @staticmethod
    def _wrap(inner: QHBoxLayout) -> QWidget:
        w = QWidget()
        inner.setContentsMargins(0, 0, 0, 0)
        w.setLayout(inner)
        return w

    def set_default_version(self, text: str) -> None:
        self._default_chip.setText(text)

    # -- 真实功能 ----------------------------------------------------
    def _browse(self) -> None:
        with ND.guard_native_dialog(self):
            path = QFileDialog.getExistingDirectory(
                self, "选择安装目录", self._dir_input.text())
        if path:
            self._dir_input.setText(path)

    def _change_default(self) -> None:
        installs = ops.find_blender_installs()
        if not installs:
            self.window().show_toast("未检测到 Blender 安装，无可选默认版本", "info")
            return
        m = _menu(
            self,
            [os.path.basename(os.path.dirname(exe)) for exe in installs],
            self._set_default,
        )
        m.exec(self._change_btn.mapToGlobal(QPoint(0, self._change_btn.height())))

    def _set_default(self, name: str) -> None:
        for exe in ops.find_blender_installs():
            if os.path.basename(os.path.dirname(exe)) == name:
                info = detect._install_info(exe)
                label_text = f"{info.version} {info.tag}"
                self.window().set_default_version(label_text)
                self.window().show_toast(f"默认版本已切换：{label_text}", "ok")
                return
        self.window().show_toast("所选安装已不存在，请刷新后重试", "info")

    def _on_theme(self, idx: int) -> None:
        self.theme_requested.emit(["dark", "light", "system"][idx])

    def _save(self) -> None:
        conc = self._conc.text().strip()
        if not conc.isdigit() or not (1 <= int(conc) <= 8):
            self.window().show_toast("下载并发数需为 1–8 的整数", "error")
            return
        store.save(
            install_dir=self._dir_input.text().strip() or store.DEFAULTS["install_dir"],
            default_version=self._default_chip.text(),
            check_updates=self._update_toggle.is_checked(),
            concurrency=conc,
        )
        self._save_btn.set_busy(True)
        QTimer.singleShot(700, self._save_done)

    def _save_done(self) -> None:
        self._save_btn.set_busy(False)
        self.window().rescan()
        self.window().show_toast("设置已保存，重启后自动恢复")

    def _reset(self) -> None:
        self._dir_input.setText(store.DEFAULTS["install_dir"])
        self._default_chip.setText(store.DEFAULTS["default_version"])
        self._update_toggle.set_checked(store.DEFAULTS["check_updates"], animate=False)
        self._conc.setText(str(store.DEFAULTS["concurrency"]))
        self.window().show_toast("已恢复默认设置（保存后生效）", "info")
