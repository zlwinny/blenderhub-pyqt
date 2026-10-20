"""插件市场对话框：登录 / 网盘下载 / 插件详情。

沿用全局设计系统（widgets.Button / label + theme 令牌），不引入新 UI 架构：

- StoreLoginDialog   BudecoTop 登录态管理（Firefox 同步 / 浏览器登录 / 手动 Cookie）
- PanDownloadDialog  网盘分享下载引导（复制提取码 → 系统浏览器 → 守望自动拾取 zip）
- StoreDetailDialog  在线插件详情（缩略图 + 元信息 + 下载安装）
- confirm()          主题化风险确认框（可执行载荷等场景）
"""
from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import detect
from . import native_dialog as ND
from . import theme as T
from .addon_store import OAUTH_URL, DownloadWatcher, StoreAddon
from .blender_releases import (
    MIRRORS,
    BlenderInstaller,
    Release,
    ReleaseDownloader,
    ReleaseIndex,
)
from .widgets import Button, Chip, ElidedLabel, _rounded_cover, label


def _dlg_qss() -> str:
    t = T.theme()
    font = 'font-family: "Noto Sans SC", "Microsoft YaHei";'
    return (
        f"QDialog {{ background: {t.bg_raised}; }}"
        f"QLabel {{ color: {t.text_1}; {font} }}"
        f"QLineEdit {{ background: {t.bg_surface}; border: 1px solid {t.border}; "
        f"border-radius: 8px; padding: 6px 10px; color: {t.text_1}; {font} }}"
        f"QLineEdit:focus {{ border: 1px solid {t.brand}; }}"
    )


def _frame_qss(name: str = "panel") -> str:
    """面板底色。必须用 QFrame#name 限定：裸规则会连子控件一起描边、上色。"""
    t = T.theme()
    return (f"QFrame#{name} {{ background: {t.bg_surface}; "
            f"border: 1px solid {t.border}; border-radius: 12px; }}")


def _picker_qss() -> str:
    """在线版本对话框特有控件（下拉、进度条）的配色，全局 QSS 里没有这两类。"""
    t = T.theme()
    font = 'font-family: "Noto Sans SC", "Microsoft YaHei";'
    return (
        f"QComboBox {{ background: {t.bg_surface}; border: 1px solid {t.border}; "
        f"border-radius: 8px; padding: 5px 10px; color: {t.text_1}; {font} }}"
        f"QComboBox:hover {{ border-color: {t.border_strong}; }}"
        f"QComboBox:focus {{ border: 1px solid {t.brand}; }}"
        f"QComboBox QAbstractItemView {{ background: {t.bg_raised}; "
        f"border: 1px solid {t.border}; color: {t.text_1}; "
        f"selection-background-color: {t.brand}; selection-color: {t.on_brand}; }}"
        f"QProgressBar {{ background: {t.bg_surface}; border: 1px solid {t.border}; "
        f"border-radius: 6px; text-align: center; color: {t.text_2}; }}"
        f"QProgressBar::chunk {{ background: {t.brand}; border-radius: 5px; }}"
    )


class StoreLoginDialog(QDialog):
    """BudecoTop 登录：Firefox 一键同步 / 系统浏览器登录 / 手动粘贴 Cookie。"""

    def __init__(self, auth, parent=None):
        super().__init__(parent)
        self._auth = auth
        self.setWindowTitle("BudecoTop 登录")
        self.setMinimumWidth(520)
        self.setStyleSheet(_dlg_qss())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        lay.addWidget(label("BudecoTop 账号登录", "h1"))
        lay.addWidget(label(
            "BudecoTop 的插件下载需要登录。推荐两种方式：", "muted"))
        steps = QFrame(self)
        steps.setObjectName("panel")
        steps.setStyleSheet(_frame_qss())
        sl = QVBoxLayout(steps)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(6)
        sl.addWidget(label(
            "① 已在本机 Firefox 登录过该站点 → 直接点「从 Firefox 同步登录态」", "body"))
        sl.addWidget(label(
            "② 否则点「在浏览器中打开登录页」，用 QQ 扫码登录后，"
            "回到本窗口点「从 Firefox 同步登录态」，或粘贴 Cookie", "body"))
        lay.addWidget(steps)

        self._state = label("当前状态：检测中…", "body")
        lay.addWidget(self._state)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._ff_btn = Button("从 Firefox 同步登录态", "primary", icon_name="update")
        self._ff_btn.clicked.connect(self._sync_firefox)
        row.addWidget(self._ff_btn)
        web_btn = Button("在浏览器中打开登录页", "secondary", icon_name="download")
        web_btn.clicked.connect(self._open_browser)
        row.addWidget(web_btn)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(label("或手动粘贴 Cookie（name=value; name2=value2）", "muted"))
        crow = QHBoxLayout()
        crow.setSpacing(8)
        self._cookie_edit = QLineEdit()
        self._cookie_edit.setPlaceholderText("wordpress_logged_in_…=…; wordpress_sec_…=…")
        crow.addWidget(self._cookie_edit)
        imp = Button("导入", "secondary")
        imp.clicked.connect(self._import_cookie)
        crow.addWidget(imp)
        lay.addLayout(crow)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        out = Button("退出登录", "danger")
        out.clicked.connect(self._logout)
        foot.addWidget(out)
        foot.addStretch(1)
        close = Button("关闭", "ghost")
        close.clicked.connect(self.reject)
        foot.addWidget(close)
        lay.addLayout(foot)

        self._refresh_state()

    def _refresh_state(self) -> None:
        if self._auth.logged_in:
            self._state.setText("当前状态：已登录 BudecoTop")
        else:
            self._state.setText("当前状态：未登录")

    def _sync_firefox(self) -> None:
        self._ff_btn.set_busy(True)
        QApplication.processEvents()
        ok, msg = self._auth.sync_from_firefox()
        self._ff_btn.set_busy(False)
        self._refresh_state()
        if ok:
            self.accept()
        else:
            QMessageBox.information(self, "Firefox 同步", msg)

    def _open_browser(self) -> None:
        webbrowser.open(OAUTH_URL)
        self._state.setText("当前状态：未登录（已在浏览器打开登录页，登录后请同步）")

    def _import_cookie(self) -> None:
        ok, msg = self._auth.import_cookie_text(self._cookie_edit.text())
        self._refresh_state()
        if ok:
            self.accept()
        else:
            QMessageBox.information(self, "导入 Cookie", msg)

    def _logout(self) -> None:
        self._auth.logout()
        self._refresh_state()


class PanDownloadDialog(QDialog):
    """网盘分享下载：复制提取码 → 系统浏览器下载 → 守望自动拾取 zip。

    zip_path 属性在 Accepted 时有效（守望命中或用户手动选择）。
    """

    def __init__(self, addon: StoreAddon, pan_url: str, code: str,
                 parent=None):
        super().__init__(parent)
        self._addon = addon
        self._pan_url = pan_url
        self._code = code
        self.zip_path = ""
        self.setWindowTitle(f"下载 {addon.name} — 网盘分享")
        self.setMinimumWidth(560)
        self.setStyleSheet(_dlg_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        lay.addWidget(label(f"「{addon.name}」的安装包放在网盘分享里", "h1"))
        lay.addWidget(label(
            "① 点「在浏览器中下载」打开分享页（提取码已自动复制，"
            "浏览器若已有网盘登录态会直接开始下载）\n"
            "② 下载完成后本应用会自动检测到并安装，无需切回本窗口点按钮",
            "muted"))

        url_box = QFrame(self)
        url_box.setObjectName("panel")
        url_box.setStyleSheet(_frame_qss())
        ul = QVBoxLayout(url_box)
        ul.setContentsMargins(10, 10, 10, 10)
        ul.setSpacing(8)
        urow = QHBoxLayout()
        urow.setSpacing(8)
        self._url_edit = QLineEdit(pan_url)
        self._url_edit.setReadOnly(True)
        urow.addWidget(self._url_edit)
        copy_link = Button("复制链接", "secondary", "sm")
        copy_link.clicked.connect(
            lambda: self._copy(self._pan_url, "链接已复制到剪贴板。"))
        urow.addWidget(copy_link)
        ul.addLayout(urow)
        crow = QHBoxLayout()
        crow.setSpacing(8)
        crow.addWidget(label("提取码：", "body"))
        self._code_edit = QLineEdit(code)
        self._code_edit.setReadOnly(True)
        self._code_edit.setMaximumWidth(140)
        crow.addWidget(self._code_edit)
        copy_code = Button("复制提取码", "secondary", "sm")
        copy_code.clicked.connect(self._copy_code)
        crow.addWidget(copy_code)
        crow.addStretch(1)
        ul.addLayout(crow)
        if not code:
            self._code_edit.setPlaceholderText("（链接里可能已自带，无需填写）")
        lay.addWidget(url_box)

        act = QHBoxLayout()
        act.setSpacing(8)
        self._open_btn = Button("在浏览器中下载", "primary", icon_name="download")
        self._open_btn.clicked.connect(self._open_browser)
        act.addWidget(self._open_btn)
        self._pick_btn = Button("我已下载，选择 zip 文件", "secondary")
        self._pick_btn.clicked.connect(self._pick_zip)
        act.addWidget(self._pick_btn)
        act.addStretch(1)
        lay.addLayout(act)

        self._status = label("", "muted")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._dir_btn = Button("选择下载目录", "ghost", "sm")
        self._dir_btn.clicked.connect(self._pick_watch_dir)
        foot.addWidget(self._dir_btn)
        foot.addStretch(1)
        cancel = Button("取消", "ghost")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        lay.addLayout(foot)

        self._watcher = DownloadWatcher(parent=self)
        self._watcher.found.connect(self._on_watched_zip)
        self._watcher.tick.connect(self._on_tick)
        self._watcher.timeout.connect(self._on_timeout)

    # -- 动作 ----------------------------------------------------------
    def _copy(self, text: str, msg: str) -> None:
        QApplication.clipboard().setText(text)
        self._status.setText(msg)

    def _copy_code(self) -> None:
        if not self._code:
            self._status.setText("该链接未带提取码。")
            return
        self._copy(self._code, f"提取码「{self._code}」已复制。")

    def _open_browser(self) -> None:
        if not self._watcher.running:
            self._watcher.start()
        webbrowser.open(self._pan_url)
        if self._code:
            QApplication.clipboard().setText(self._code)
            self._status.setText(
                "已在浏览器打开分享页，提取码已复制到剪贴板。\n"
                "在浏览器里点「下载」即可——下完后这里会自动检测到并安装。")
        else:
            self._status.setText(
                "已在浏览器打开分享页。\n"
                "在浏览器里点「下载」即可——下完后这里会自动检测到并安装。")

    def _pick_watch_dir(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        with ND.guard_native_dialog(self):
            d = QFileDialog.getExistingDirectory(self, "选择浏览器的下载目录")
        if d:
            self._watcher.set_dirs([d])
            if not self._watcher.running:
                self._watcher.start()
            self._status.setText(f"已切换监控目录：{d}\n正在等待下载完成…")

    def _on_tick(self, seconds: int) -> None:
        self._status.setText(
            f"正在等待浏览器下载… 已等待 {seconds // 60:02d} 分 {seconds % 60:02d} 秒\n"
            "在浏览器里点「下载」即可，下完自动安装。")

    def _on_timeout(self) -> None:
        self._status.setText(
            "等待超时（15 分钟）仍未在下载目录发现新的 zip。\n"
            "若浏览器下载到了别处，可点「选择下载目录」；"
            "或直接点「我已下载，选择 zip 文件」手动安装。")

    def _on_watched_zip(self, path: str) -> None:
        self.zip_path = path
        self._status.setText(f"检测到下载完成的文件：{path}，正在安装…")
        self.accept()

    def _pick_zip(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        with ND.guard_native_dialog(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择下载好的插件压缩包", "", "ZIP 压缩包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            QMessageBox.information(
                self, "提示",
                "选中的不是 .zip。Blender 只能从 zip 安装，请选 zip 文件。")
            return
        self._watcher.stop()
        self.zip_path = path
        self.accept()

    def reject(self) -> None:
        self._watcher.stop()
        super().reject()

    def accept(self) -> None:
        self._watcher.stop()
        super().accept()


class StoreDetailDialog(QDialog):
    """在线插件详情：缩略图 + 元信息 + 下载安装。"""

    install_requested = pyqtSignal()

    def __init__(self, addon: StoreAddon, thumb_path: str = "",
                 installed: bool = False, parent=None):
        super().__init__(parent)
        self._addon = addon
        self.setWindowTitle(addon.name)
        self.setMinimumWidth(520)
        self.setStyleSheet(_dlg_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        if thumb_path:
            pm = QPixmap(thumb_path)
            if not pm.isNull():
                cover = _rounded_cover(pm, 472 * 2, 132 * 2, 12 * 2)
                cover.setDevicePixelRatio(2)
                banner = QLabel(self)
                banner.setFixedSize(472, 132)
                banner.setPixmap(cover)
                lay.addWidget(banner, 0, Qt.AlignmentFlag.AlignHCenter)

        lay.addWidget(label(addon.name, "h1"))
        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(label(
            f"by {addon.author or '未知作者'} · {addon.category}", "muted"))
        price_color = T.theme().success if addon.free else T.theme().brand_text
        price_lb = label(addon.price, "body")
        price_lb.setStyleSheet(f"color: {price_color}; font-weight: bold;")
        meta.addWidget(price_lb)
        if installed:
            meta.addWidget(label("已安装", "body"))
        meta.addStretch(1)
        lay.addLayout(meta)

        if addon.description:
            desc = label(addon.description, "body")
            desc.setWordWrap(True)
            lay.addWidget(desc)
        if addon.tags:
            tags = label(" · ".join(addon.tags), "muted")
            lay.addWidget(tags)

        act = QHBoxLayout()
        act.setSpacing(8)
        page_btn = Button("打开文章页", "secondary", icon_name="folder")
        page_btn.clicked.connect(lambda: webbrowser.open(addon.source_url))
        act.addWidget(page_btn)
        act.addStretch(1)
        self._install_btn = Button(
            "重新安装" if installed else "下载并安装", "primary",
            icon_name="download")
        self._install_btn.clicked.connect(self.install_requested.emit)
        act.addWidget(self._install_btn)
        lay.addLayout(act)


class ReleasePickerDialog(QDialog):
    """在线添加 Blender 版本：选镜像 → 拉列表 → 下载（续传 + SHA-256）→ 解包。

    解包目标就是设置里的安装目录；成功后只发信号，登记托管目录与重扫由页面做。
    """

    installed = pyqtSignal(str, str)      # (version, dest_root)
    import_requested = pyqtSignal()       # 用户改走「本地导入压缩包」

    def __init__(self, install_dir: str, parent=None):
        super().__init__(parent)
        self._install_dir = install_dir
        self._busy = False
        self._pending: Release | None = None
        self.setWindowTitle("添加 Blender 版本")
        self.setMinimumWidth(640)
        self.setStyleSheet(_dlg_qss() + _picker_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(label("添加 Blender 版本", "h1"))
        lay.addWidget(label(
            f"从镜像站选择版本，下载后解包到：{install_dir}\n"
            "支持断点续传；下载完成按官方 SHA-256 清单校验，校验不过直接丢弃。",
            "muted"))

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._mirror = QComboBox(self)
        for name, url in MIRRORS:
            self._mirror.addItem(name, url)
        self._mirror.currentIndexChanged.connect(lambda _i: self._load(False))
        bar.addWidget(self._mirror)
        self._refresh_btn = Button("刷新列表", "secondary", "sm", icon_name="update")
        self._refresh_btn.clicked.connect(lambda: self._load(True))
        bar.addWidget(self._refresh_btn)
        bar.addStretch(1)
        local_btn = Button("本地导入压缩包…", "ghost", "sm", icon_name="folder")
        local_btn.clicked.connect(self._want_local)
        bar.addWidget(local_btn)
        lay.addLayout(bar)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._list_lay = QVBoxLayout(inner)
        self._list_lay.setContentsMargins(0, 0, 8, 0)
        self._list_lay.setSpacing(8)
        self._list_lay.addWidget(label("正在获取在线版本列表…", "muted"))
        self._list_lay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        prog = QHBoxLayout()
        prog.setSpacing(8)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(180)
        self._bar.setVisible(False)
        prog.addWidget(self._bar)
        self._status = label("", "muted")
        prog.addWidget(self._status, 1)
        self._cancel_btn = Button("取消", "ghost", "sm")
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.setVisible(False)
        prog.addWidget(self._cancel_btn)
        lay.addLayout(prog)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close_btn = Button("关闭", "secondary")
        close_btn.clicked.connect(self.reject)
        foot.addWidget(close_btn)
        lay.addLayout(foot)

        self._index = ReleaseIndex(self)
        self._index.loaded.connect(self._on_loaded)
        self._dl = ReleaseDownloader(self)
        self._dl.progress.connect(self._on_progress)
        self._dl.finished.connect(self._on_dl_done)
        self._installer = BlenderInstaller(self)
        self._installer.progress.connect(
            lambda text: self._status.setText(text))
        self._installer.finished.connect(self._on_inst_done)
        self._load(False)

    # -- 列表 --------------------------------------------------------
    def _load(self, force: bool) -> None:
        self._clear_rows()
        self._status.setText("")
        self._list_lay.insertWidget(
            0, label("正在获取在线版本列表…", "muted"), 0)
        self._index.refresh(self._mirror.currentData(), force)

    def _clear_rows(self) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _on_loaded(self, rels: list, err: str) -> None:
        self._clear_rows()
        if err:
            self._status.setText(err)
        if not rels:
            self._list_lay.addWidget(label(
                "没有取到在线版本。可以换个镜像，或用上方「本地导入压缩包」。",
                "muted"))
        for rel in rels:
            self._list_lay.addWidget(self._make_row(rel))
        self._list_lay.addStretch(1)

    def _make_row(self, rel: Release) -> QWidget:
        box = QFrame(self)
        box.setObjectName("panel")
        box.setStyleSheet(_frame_qss())
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)
        row.addWidget(label(f"Blender {rel.version}", "h2"))
        if rel.is_lts:
            row.addWidget(Chip("LTS", "success"))
        meta = " · ".join(x for x in (
            detect.fmt_size(rel.size_bytes) if rel.size_bytes else "",
            rel.date[:10], rel.mirror) if x)
        # 用省略标签：行宽不够时截断而不是把「下载并安装」挤出可视区
        row.addWidget(ElidedLabel(meta, "muted"), 1)
        btn = Button("下载并安装", "primary", "sm", icon_name="download")
        btn.clicked.connect(lambda _c=False, r=rel: self._download(r))
        row.addWidget(btn)
        return box

    # -- 下载 / 解包 -------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._bar.setVisible(busy)
        self._cancel_btn.setVisible(busy)
        self._mirror.setEnabled(not busy)
        self._refresh_btn.setEnabled(not busy)
        for i in range(self._list_lay.count()):
            w = self._list_lay.itemAt(i).widget()
            if w is None:
                continue
            for b in w.findChildren(Button):
                b.setEnabled(not busy)

    def _download(self, rel: Release) -> None:
        if self._busy:
            return
        self._pending = rel
        self._set_busy(True)
        self._bar.setValue(0)
        self._status.setText(f"正在下载 Blender {rel.version}…")
        self._dl.start(rel)

    def _on_progress(self, pct: int, text: str) -> None:
        self._bar.setValue(pct)
        self._status.setText(text)

    def _cancel(self) -> None:
        self._dl.cancel()
        self._status.setText("正在取消…")

    def _on_dl_done(self, ok: bool, msg: str, zip_path: str) -> None:
        if not ok:
            self._set_busy(False)
            self._status.setText(msg)
            return
        self._bar.setRange(0, 0)          # 解包没有可靠的百分比来源，走忙碌条
        self._status.setText(f"{msg} · 正在解包…")
        self._installer.start(zip_path, self._install_dir)

    def _on_inst_done(self, ok: bool, msg: str, dest: str) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._set_busy(False)
        rel, self._pending = self._pending, None
        if not ok:
            self._status.setText(msg)
            return
        self.accept()
        if rel is not None:
            self.installed.emit(rel.version, dest)

    def _want_local(self) -> None:
        self.import_requested.emit()
        self.reject()


def confirm(parent, title: str, text: str,
            ok_text: str = "仍要安装", cancel_text: str = "取消") -> bool:
    """主题化确认框（可执行载荷等风险场景 / 批量删除等破坏性操作）。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning)
    yes = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    box.setStyleSheet(_dlg_qss() +
                      f"QMessageBox {{ background: {T.theme().bg_raised}; }}"
                      f"QPushButton {{ min-width: 72px; }}")
    box.exec()
    return box.clickedButton() is yes


def notice(parent, title: str, text: str, ok_text: str = "知道了") -> None:
    """主题化提示框（只告知不询问）：用于「外部安装不代删」这类拒绝场景。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.setStyleSheet(_dlg_qss() +
                      f"QMessageBox {{ background: {T.theme().bg_raised}; }}"
                      f"QPushButton {{ min-width: 72px; }}")
    box.exec()
