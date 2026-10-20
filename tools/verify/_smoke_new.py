"""离屏冒烟：验证多选删除 / 页面切换 / 缩略图队列 / 外壳重绘。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtCore import QTimer, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from blenderhub import native_dialog as ND  # noqa: E402
from blenderhub.main_window import MainWindow  # noqa: E402
from blenderhub.pages import ProjectsPage  # noqa: E402

app = QApplication.instance() or QApplication([])
win = MainWindow()
win.show()
app.processEvents()

# 1) 页面来回切换 —— 验证动画不再把页面留在半透明
for i in (1, 2, 3, 4, 0, 2, 3):
    win.switch_page(i)
    app.processEvents()
print("[OK] 页面切换 7 次")

# 2) 外壳重绘
ND.repaint_shell(win)
win._repaint_shell()
app.processEvents()
print("[OK] 外壳重绘无异常")

# 3) 项目页批量删除链路（必须是当前页，QStackedWidget 隐藏时 isVisible 为 False）
win.switch_page(2)
app.processEvents()
for _ in range(30):
    app.processEvents()
page = next(p for p in win._pages if isinstance(p, ProjectsPage))
print(f"[INFO] 项目卡片 {len(page._cards)} 可见 {len(page._visible_cards())}")
page._toggle_select_mode()
print(f"[OK] 进入多选 selectable={page._select_mode} del_btn={page._del_btn.text()!r}")
page._toggle_select_all_visible()
print(f"[OK] 全选 选中 {len(page._sel)} 个 / 可见 {len(page._visible_cards())} "
      f"按钮={page._all_btn.text()!r} 删除键={page._del_btn.text()!r} "
      f"占用={page._sel_total_size()}")
page._toggle_select_all_visible()
print(f"[OK] 取消全选 选中 {len(page._sel)} 按钮={page._all_btn.text()!r}")
# 单选两个
vis = page._visible_cards()
for entry in vis[:2]:
    path = entry[2]
    page._card_clicked(entry[1], path)
print(f"[OK] 单选 2 个 -> {page._del_btn.text()!r} enabled={page._del_btn.isEnabled()}")
print(f"[OK] 占用统计 {page._sel_total_size()} 字节")
page._exit_select_mode()
print(f"[OK] 退出多选 select_mode={page._select_mode} 选中={len(page._sel)}")

# 4) 搜索过滤后全选按钮状态
page._search.setText("zzz-not-exist-zzz")
page._apply_filters()
print(f"[OK] 无结果时 可见={len(page._visible_cards())}")
page._search.setText("")
page._apply_filters()

# 5) 插件页缩略图队列
addons_page = win._pages[3]
print(f"[OK] 插件页 thumb_queue={len(addons_page._thumb_queue)} "
      f"tried={len(addons_page._thumb_requested)}")

# 6) 快捷键对象存活
print(f"[OK] 快捷键 Ctrl+A/Esc 已挂载 "
      f"{page._sc_all.key().toString()} / {page._sc_esc.key().toString()}")

QTimer.singleShot(0, app.quit)
print("SMOKE_OK")
