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
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from . import native_dialog as ND
from . import theme as T
from .addon_store import OAUTH_URL, DownloadWatcher, StoreAddon
from .widgets import Button, _rounded_cover, label


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


def _frame_qss() -> str:
    t = T.theme()
    return (f"background: {t.bg_surface}; border: 1px solid {t.border}; "
            f"border-radius: 12px;")


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


def confirm(parent, title: str, text: str,
            ok_text: str = "仍要安装", cancel_text: str = "取消") -> bool:
    """主题化确认框（可执行载荷等风险场景 / 批量删除等破坏性操作）。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning)
    yes = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setStyleSheet(_dlg_qss() +
                      f"QMessageBox {{ background: {T.theme().bg_raised}; }}"
                      f"QPushButton {{ min-width: 72px; }}")
    box.exec()
    return box.clickedButton() is yes
