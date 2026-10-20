"""验证窗口几何：自适应首启 / 记忆还原 / 跨屏收回。用真实平台（不 show）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

KEY = "window_geometry"
cfg = QSettings("ZLStudio", "BlenderHub")
had = cfg.contains(KEY)
orig = cfg.value(KEY) if had else None
print(f"[备份] 原有 geometry: {'有' if had else '无'}")

app = QApplication(sys.argv)
for s in app.screens():
    ava = s.availableGeometry()
    print(f"[屏幕] {s.name()} 全屏 {s.geometry().width()}x{s.geometry().height()} "
          f"工作区 {ava.x()},{ava.y()} {ava.width()}x{ava.height()} dpr={s.devicePixelRatio()}")

from blenderhub.main_window import MainWindow  # noqa: E402

# 1) 清掉记录 -> 走自适应首启
cfg.remove(KEY)
cfg.sync()
w1 = MainWindow()
g = w1.geometry()
print(f"[1 首启] {g.x()},{g.y()} {g.width()}x{g.height()}")

# 2) 模拟「上次在双屏上拉到 3499 宽」
def roundtrip(rect, label):
    """写一条 geometry 记录 -> 新建 MainWindow -> 看还原结果。"""
    from PyQt6.QtCore import QRect
    w = MainWindow()
    w.setGeometry(QRect(*rect))
    blob = w.saveGeometry()
    cfg.setValue(KEY, blob)
    cfg.sync()
    from blenderhub.main_window import saved_frame_rect  # noqa: E402
    from blenderhub import store as st  # noqa: E402
    reloaded = st.load_geometry()
    want = saved_frame_rect(reloaded)
    print(f"[{label}] blob={blob.size()}B 解析矩形={want}  "
          f"type={type(reloaded).__name__}")
    probe = MainWindow()
    print(f"[{label}] 还原前可见比例={probe._visible_ratio(want) if want else 'NA'} "
          f"maximized={probe.isMaximized()}")
    got = probe.geometry()
    print(f"[{label}] 写入 {rect[2]}x{rect[3]} @({rect[0]},{rect[1]})"
          f"  ->  还原 {got.width()}x{got.height()} @({got.x()},{got.y()})")
    return got


# 2) 双屏下完整可见的大窗口（200..3699 落在 3840 宽的扩展桌面内）-> 应原样保留
roundtrip((200, 126, 3499, 905), "2 双屏完整可见")

# 3) 大半在屏外（右边界 6499 远超 3840）-> 应收回并缩小
roundtrip((3000, 126, 3499, 905), "3 大半在屏外")

# 4) 常规尺寸 -> 原样还原
roundtrip((300, 180, 1280, 860), "4 常规尺寸")

# 还原现场
if had:
    cfg.setValue(KEY, orig)
else:
    cfg.remove(KEY)
cfg.sync()
print("[备份] 已还原")
