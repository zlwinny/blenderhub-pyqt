"""原生对话框与半透明外壳的协同（背景变透明的修复工具）。

Windows 上 FramelessWindowHint + WA_TranslucentBackground 是 layered window：
原生模态对话框（QFileDialog）、或焦点切到资源管理器再切回来时，DWM 可能
丢弃最上层窗口的合成内容，表现为「窗口背景变透明」——只剩圆角描边和控件。

一次性 update() 不够（还在和对话框的销毁竞争），需要在多个时间点补刷。
这里统一提供：

    repaint_shell(widget)           多点延迟重绘所属主窗口外壳
    guard_native_dialog(widget)     上下文管理器，包裹原生对话框调用
    install_repaint_guard(window)   给主窗口装 qApp 级激活监听器（兜底）
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtWidgets import QApplication

# 延迟补刷时间点（毫秒）：首刷立即，其余覆盖对话框销毁 / DWM 重新合成的几个节拍
_REPAINT_DELAYS = (0, 80, 240, 520)

_ACTIVE_EVENTS = frozenset((
    QEvent.Type.ApplicationActivate,
    QEvent.Type.WindowActivate,
))


def find_shell(widget) -> "object | None":
    """向上找到主窗口外壳（objectName == "shell" 的部件）。"""
    w = widget
    seen = 0
    while w is not None and seen < 64:
        try:
            if w.objectName() == "shell":
                return w
        except RuntimeError:
            return None
        w = w.parentWidget()
        seen += 1
    return None


def _paint(win, shell) -> None:
    for w in (shell, win):
        if w is None:
            continue
        try:
            w.update()
            w.repaint()
        except RuntimeError:
            return


def repaint_shell(widget) -> None:
    """多点延迟重绘 widget 所属主窗口的半透明外壳。

    widget 可以是页面、卡片或对话框——只要它挂在主窗口树下。
    """
    if widget is None:
        return
    try:
        win = widget.window()
    except RuntimeError:
        return
    if win is None:
        return
    shell = find_shell(win) or win
    # 只调重载样式的方法，不调 _repaint_shell（MainWindow._repaint_shell 会委托回本函数）
    style = getattr(win, "_apply_shell_style", None)
    if callable(style):
        try:
            style()
        except RuntimeError:
            return
    for ms in _REPAINT_DELAYS:
        QTimer.singleShot(ms, lambda w=win, s=shell: _paint(w, s))


class guard_native_dialog:
    """包裹原生对话框调用，结束后补齐外壳重绘。

    用法：
        with guard_native_dialog(self):
            path, _ = QFileDialog.getOpenFileName(self, ...)
    """

    def __init__(self, widget):
        self._widget = widget

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        repaint_shell(self._widget)
        return False


class _AppGuard(QObject):
    """qApp 级事件过滤：任何窗口重新激活时补刷外壳。

    覆盖「手动调用了 QFileDialog 但忘记重绘」以及「切到资源管理器再切回」的场景。
    """

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def eventFilter(self, obj, e):
        if obj is QApplication.instance() and e.type() in _ACTIVE_EVENTS:
            repaint_shell(self._win)
        return False


def install_repaint_guard(window) -> None:
    """给主窗口安装激活守卫（幂等）。"""
    app = QApplication.instance()
    if app is None:
        return
    if getattr(window, "_app_guard", None) is not None:
        return
    guard = _AppGuard(window)
    window._app_guard = guard
    app.installEventFilter(guard)
