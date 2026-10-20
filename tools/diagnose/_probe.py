"""离屏验证探针：图标尺寸精确性 + 按钮点击反馈 + 主题切换 + 五页截图。

用法： QT_QPA_PLATFORM=offscreen python _probe.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import theme as T
from blenderhub.main_window import MainWindow
from blenderhub.pages import (
    AddonsPage,
    HomePage,
    ProjectsPage,
    SettingsPage,
    VersionsPage,
)
from blenderhub.widgets import Toast

FAILS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name} {detail}")
    if not cond:
        FAILS.append(f"{name} {detail}")


def ink_bbox(pm: QPixmap):
    """返回位图中非透明像素的边界框 (w, h)；全透明返回 None。"""
    img = pm.toImage()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts()
    T.apply_theme(app, T.DARK)

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()

    # ---- 1. 标签图标：逻辑尺寸必须等于设计值 -------------------------
    label_checks = [
        ("NavItem 图标(17)", win._sidebar._items[0]._ic, 17),
        ("侧边栏 Logo(24)", win._sidebar._logo, 24),
        ("标题栏 Logo(20)", win._titlebar._logo, 20),
        ("关于 Logo(40)", None, None),  # 占位，下面按类型查
    ]
    for name, lb, expect in label_checks:
        if lb is None:
            continue
        pm = lb.pixmap()
        got = pm.deviceIndependentSize()
        check(
            name,
            not pm.isNull() and got.width() == expect and got.height() == expect,
            f"logical={got.width()}x{got.height()} expect={expect}",
        )

    # 页面内图标标签按类型抽查
    home = win._pages[0]
    for lbl in home.findChildren(QLabel):
        pm = lbl.pixmap()
        if pm is None or pm.isNull():
            continue
        got = pm.deviceIndependentSize()
        # 逻辑尺寸必须是整数且在合理图标区间（10~41），不允许 12.5 之类的碎值
        check(
            f"首页标签图标 {lbl.__class__.__name__}@{id(lbl)%10000}",
            got.width() == int(got.width()) and 10 <= got.width() <= 41,
            f"logical={got.width()}x{got.height()}",
        )

    # ---- 2. 按钮图标：iconSize 与实际可用尺寸一致 --------------------
    btn_bad = 0
    btn_total = 0
    for b in win.findChildren(QPushButton):
        ic = b.icon()
        if ic.isNull():
            continue
        btn_total += 1
        isz = b.iconSize()
        actual = ic.actualSize(isz)
        if isz.width() <= 0 or actual.width() != isz.width():
            btn_bad += 1
            print(f"    按钮图标异常: iconSize={isz.width()}x{isz.height()} "
                  f"actual={actual.width()}x{actual.height()} text={b.text()!r}")
    check("全部带图标按钮 iconSize 与图标实际尺寸一致", btn_bad == 0,
          f"共 {btn_total} 个按钮，异常 {btn_bad} 个")

    # ---- 3. 渲染级验证：图标墨迹与 SVG 几何成比例 --------------------
    # 预期墨迹 = 逻辑尺寸 × SVG几何跨度/24（viewBox 24）
    dpr = app.primaryScreen().devicePixelRatio()

    def expect_ink(logical: int, extent: float) -> tuple[float, float]:
        mid = logical * extent / 24.0 * dpr
        return mid * 0.75, mid * 1.25

    # max：24 视框内 5.5→18.5 方框 + 1.75 描边 ≈ 14.4
    max_btn = win._titlebar._max_btn
    bb = ink_bbox(max_btn.grab())
    lo, hi = expect_ink(16, 14.4)
    check("标题栏 max 按钮图标渲染尺寸符合几何比例",
          bb is not None and lo <= bb[0] <= hi and lo <= bb[1] <= hi,
          f"ink_bbox={bb} 预期区间=[{lo:.1f},{hi:.1f}] dpr={dpr}")

    # search：圆 r6.4 + 斜线至 20 → 跨度 15.4
    from blenderhub.widgets import SearchBox
    search_boxes = [sb for page in win._pages for sb in page.findChildren(SearchBox)]
    for sb in search_boxes:
        bb2 = ink_bbox(sb._ic.grab())
        lo, hi = expect_ink(16, 15.4)
        check("搜索框图标渲染尺寸符合几何比例",
              bb2 is not None and lo <= bb2[0] <= hi and lo <= bb2[1] <= hi,
              f"ink_bbox={bb2} 预期区间=[{lo:.1f},{hi:.1f}] dpr={dpr}")

    # 缩放一致性：同一图标 16px 与 32px 的墨迹比应≈2
    from blenderhub.icons import pixmap as mk_pixmap
    pm16, pm32 = mk_pixmap("home", "#FF0000", 16), mk_pixmap("home", "#FF0000", 32)
    b16, b32 = ink_bbox(pm16), ink_bbox(pm32)
    check("图标 16→32 渲染墨迹比≈2（缩放精确）",
          b16 is not None and b32 is not None
          and 1.7 <= b32[0] / b16[0] <= 2.3,
          f"16px={b16} 32px={b32}")

    # ---- 5. 按钮点击反馈：点击后 ToastHost 出现 Toast -----------------
    def toast_count() -> int:
        host = win._toast_host
        return len([t for t in host._toasts])

    hero_launch = None
    from blenderhub.pages import HeroCard
    hero = home.findChild(HeroCard)
    hero_launch = hero._launch_btn
    before = toast_count()
    hero_launch.click()
    app.processEvents()
    after = toast_count()
    check("点击「启动 Blender」弹出 Toast 反馈", after == before + 1,
          f"toast {before} -> {after}")

    # 版本页「检查更新」→ busy → 1.5s 后 toast
    vpage = win._pages[1]
    vpage._check_btn.click()
    app.processEvents()
    check("点击「检查更新」进入加载态", vpage._check_btn._busy is True)

    # 设置页「保存」→ busy → toast
    spage = win._pages[4]
    spage._save_btn.click()
    app.processEvents()
    check("点击「保存」进入加载态", spage._save_btn._busy is True)

    # 导航切换页面
    win._sidebar.select(win._sidebar._items[3])
    app.processEvents()
    check("点击侧边导航切换到插件页", win._current_index == 3)

    # ---- 6. 主题切换后图标重建 --------------------------------------
    win._toggle_theme()
    app.processEvents()
    pm = win._sidebar._items[0]._ic.pixmap()
    check("浅色主题下导航图标已重建且尺寸不变",
          not pm.isNull() and pm.deviceIndependentSize().width() == 17,
          f"logical={pm.deviceIndependentSize().width()}")
    win._toggle_theme()
    app.processEvents()

    # ---- 7. 五页截图 --------------------------------------------------
    for i in range(5):
        win.stack.setCurrentIndex(i)
        win._refresh_status(i)
        app.processEvents()
        pm = win.grab()
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_v2_shot_{i}.png")
        pm.save(out)
        print(f"    截图已保存: {out} ({pm.width()}x{pm.height()})")

    print()
    if FAILS:
        print(f"共 {len(FAILS)} 项失败:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("全部检查通过")


if __name__ == "__main__":
    main()
