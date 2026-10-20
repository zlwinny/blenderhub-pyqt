""".blend 内嵌缩略图的后台提取与缓存。

缩略图解析是纯文件 IO，放在工作线程池执行，避免启动/切页时阻塞 UI；
结果以 QImage 经 queued 信号回到主线程，再由页面填进卡片。
QPixmap 只能在 GUI 线程创建，因此线程内只产出 QImage（线程安全）。
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage

from . import detect


class _ExtractTask(QRunnable):
    """单个 .blend 文件的缩略图提取任务。"""

    def __init__(self, path: str, on_done):
        super().__init__()
        self._path = path
        self._on_done = on_done

    def run(self) -> None:
        raw = None
        try:
            raw = detect.read_blend_thumbnail(self._path)
        except Exception:
            raw = None
        self._on_done(self._path, raw)


class ThumbLoader(QObject):
    """后台提取 .blend 缩略图；loaded(path, QImage) 队列回主线程。

    同一路径只提取一次：成功与失败均进缓存，重复 request 直接回放缓存。
    """

    loaded = pyqtSignal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._cache: dict[str, QImage | None] = {}
        self._pending: set[str] = set()

    def request(self, path: str) -> None:
        if path in self._cache:
            img = self._cache[path]
            if img is not None:
                self.loaded.emit(path, img)
            return
        if path in self._pending:
            return
        self._pending.add(path)
        self._pool.start(_ExtractTask(path, self._on_done))

    def _on_done(self, path: str, raw) -> None:
        self._pending.discard(path)
        img = QImage()
        if raw is not None:
            w, h, data = raw
            img = QImage(data, w, h, QImage.Format.Format_RGBA8888).copy()
        # None 不能传给 QImage 值类型信号参数（PyQt 原生层直接崩），失败发空 QImage
        self._cache[path] = None if img.isNull() else img
        self.loaded.emit(path, img)
