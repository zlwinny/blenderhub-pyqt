"""入口：加载字体 → 应用深色主题 → 启动主窗口。"""
import os
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from blenderhub import theme as T
from blenderhub import store
from blenderhub.main_window import MainWindow


def _app_icon_path() -> Path | None:
    """定位应用图标。

    exe 文件图标由 spec 的 icon= 参数嵌入（资源管理器/任务栏静态显示）；
    窗口与任务栏运行时图标则要靠 setWindowIcon，这里给出它的来源：
    打包后随 datas 落在 _MEIPASS 根目录，开发环境在 assets/ 下。
    """
    cands: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(Path(__file__).resolve().parent / "assets" / "icon.ico")
    for p in cands:
        if p.is_file():
            return p
    return None


def _diag(app: QApplication) -> None:
    """诊断快照：图标可用性 / 字体 / 打包环境，写入 %TEMP%\\blenderhub_diag.log。

    仅在 BLENDERHUB_DEBUG=1 时调用（图标墨迹扫描要逐像素读图，正式启动不做）。
    """
    lines = []
    lines.append(f"frozen={getattr(sys, 'frozen', False)}")
    try:
        from PyQt6.QtCore import QLibraryInfo
        lines.append(f"qt={QLibraryInfo.version().toString()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"qt=ERR {e}")
    from blenderhub import icons
    lines.append(f"HAS_SVG={icons._HAS_SVG}")
    fams = set(app.font().families())
    lines.append(f"font_families={len(fams)}")
    for probe in ("Microsoft YaHei", "Noto Sans SC", "Space Grotesk", "Consolas"):
        lines.append(f"  font[{probe}]={'Y' if probe in fams else 'N'}")
    for name in ("home", "gear", "search"):
        pm = icons.pixmap(name, "#FF0000", 20)
        img = pm.toImage()
        ink = sum(
            1
            for y in range(img.height())
            for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 10
        )
        total = img.width() * img.height()
        lines.append(
            f"  icon[{name}] logical={pm.deviceIndependentSize().width()} "
            f"dev={pm.width()}x{pm.height()} dpr={pm.devicePixelRatio()} ink={ink}/{total}"
        )
    log = os.path.join(os.environ.get("TEMP", "."), "blenderhub_diag.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # 打包版（console=False）sys.stderr 为 None，直接写会启动即崩
    try:
        if sys.stderr is not None:
            sys.stderr.write(f"[diag] written to {log}\n")
    except Exception:
        pass


def _install_crash_log() -> None:
    """窗口版（console=False）未处理异常会静默退出，落一份崩溃日志便于定位。"""
    import traceback

    def hook(exc_type, exc_value, exc_tb) -> None:
        log = os.path.join(os.environ.get("TEMP", "."), "blenderhub_crash.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def main() -> None:
    _install_crash_log()
    store.begin_session()
    store.ckpt("main-enter")
    app = QApplication(sys.argv)
    store.ckpt("qapp")
    app.setApplicationName("BlenderHub")
    app.setApplicationDisplayName("BlenderHub")
    app.setStyle("Fusion")
    # 窗口 / 任务栏 / Alt-Tab 图标（exe 文件图标由 spec 的 icon= 负责）
    _ip = _app_icon_path()
    if _ip is not None:
        app.setWindowIcon(QIcon(str(_ip)))
    T.load_fonts()
    store.ckpt("fonts")
    # 恢复上次保存的主题（QSettings，注册表 HKCU\Software\ZLStudio\BlenderHub）
    data = store.load()
    store.ckpt("store-load")
    T.apply_theme(app, T.LIGHT if data["theme"] == "light" else T.DARK)
    store.ckpt("theme")
    if store.DEBUG:
        _diag(app)
    store.ckpt("diag")
    window = MainWindow()
    store.ckpt("mw-done")
    window.show()
    store.ckpt("shown")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
