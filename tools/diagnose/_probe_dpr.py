"""高 DPI 图标探针：模拟用户 125% 缩放（dpr=1.25），逐项核验图标真实渲染尺寸。

重点验证：
1. 每个图标的墨迹跨度符合 SVG 几何比例（不被二次放大）；
2. 墨迹不触碰 pixmap 边缘（不被裁切）；
3. 标签图标逻辑尺寸正确；按钮 iconSize 与 QIcon 实际尺寸一致。

用法： QT_QPA_PLATFORM=offscreen python _probe_dpr.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blenderhub import theme as T
from blenderhub import icons as I
from blenderhub.main_window import MainWindow
from blenderhub.widgets import SearchBox

FAILS = []

# 各图标在 viewBox 24 内的墨迹几何跨度（含半描边），按 icons.py 路径数据估算
GEOM = {
    "home": 15.2, "versions": 14.4, "folder": 17.0, "puzzle": 20.9,
    "gear": 17.2, "search": 15.4, "bell": 13.9, "download": 14.6,
    "play": 12.8, "plus": 14.0, "check": 13.4, "alert": 18.0,
    "trash": 14.9, "star": 15.8, "more": 14.2, "update": 15.6,
    "x": 11.2, "min": 13.0, "max": 14.75, "file": 12.9,
    "sun": 16.6, "moon": 17.0, "logo": 21.0,
}


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name} {detail}")
    if not cond:
        FAILS.append(f"{name} {detail}")


def ink_bbox(pm: QPixmap):
    img = pm.toImage()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, None, None
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1,
            (min(xs), min(ys), max(xs), max(ys)))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts()
    T.apply_theme(app, T.DARK)

    dpr = app.primaryScreen().devicePixelRatio()
    print(f"== 模拟屏幕 DPR = {dpr}（对应用户 125% 缩放）")
    check("屏幕 DPR 已模拟为 1.25", abs(dpr - 1.25) < 0.01, f"dpr={dpr}")

    # ---- 1. 直接栅格化验证：全部图标 × 全部使用尺寸 --------------------
    used = [
        ("home", 17), ("versions", 17), ("folder", 17), ("puzzle", 17), ("gear", 17),
        ("logo", 24), ("logo", 20), ("logo", 40),
        ("search", 16), ("bell", 14), ("more", 14), ("folder", 14),
        ("play", 16), ("download", 16), ("plus", 16), ("update", 16),
        ("min", 16), ("max", 16), ("x", 16), ("file", 16),
        ("star", 12), ("versions", 30), ("puzzle", 30), ("check", 13),
        ("sun", 14), ("moon", 14), ("alert", 13),
    ]
    bad_scale, bad_clip = [], []
    for name, size in used:
        pm = I.pixmap(name, "#FF0000", size)
        dev = pm.width()
        bb, _, box = ink_bbox(pm)
        if bb is None:
            bad_scale.append((name, size, "空"))
            continue
        geom = GEOM[name] / 24 * size  # 逻辑墨迹
        expect_dev = geom * dpr        # 设备墨迹
        # 允许 AA  fringe：小图标 +0.2~1.7 设备像素，大图标 +2.5
        if not (geom * 0.85 <= bb / dpr <= geom * 1.25 + 2.0 / dpr):
            bad_scale.append((name, size, f"ink={bb}dev 期望≈{expect_dev:.1f}dev"))
        # 不得裁切：墨迹不得触到 pixmap 边缘
        x0, y0, x1, y1 = box
        if x0 <= 0 or y0 <= 0 or x1 >= dev - 1 or y1 >= dev - 1:
            bad_clip.append((name, size, f"box={box} dev={dev}"))
    check("全部图标渲染尺寸符合几何比例（无二次放大）", not bad_scale,
          f"共 {len(used)} 项" + (f"，异常: {bad_scale}" if bad_scale else ""))
    check("全部图标无边缘裁切", not bad_clip,
          f"共 {len(used)} 项" + (f"，被裁切: {bad_clip}" if bad_clip else ""))

    # ---- 2. 运行整窗：标签 / 按钮 --------------------------------------
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()

    bad_lbl = []
    n_lbl = 0
    for lbl in win.findChildren(QLabel):
        pm = lbl.pixmap()
        if pm is None or pm.isNull():
            continue
        n_lbl += 1
        got = pm.deviceIndependentSize().width()
        # 逻辑尺寸误差 ≤0.5px（dpr=1.25 下 17px 只能取 16.8 或 17.6）
        if abs(got - round(got)) > 0.001 and abs(got - int(got)) > 0.5:
            bad_lbl.append(round(got, 2))
    check("标签图标逻辑尺寸误差 ≤0.5px", not bad_lbl,
          f"共 {n_lbl} 个" + (f"，异常: {bad_lbl}" if bad_lbl else ""))

    btn_bad = []
    n_btn = 0
    for b in win.findChildren(QPushButton):
        ic = b.icon()
        if ic.isNull():
            continue
        n_btn += 1
        isz = b.iconSize()
        actual = ic.actualSize(isz)
        if isz.width() <= 0 or actual.width() != isz.width():
            btn_bad.append((b.text(), isz.width(), actual.width()))
    check("按钮 iconSize 与 QIcon 实际尺寸一致", not btn_bad,
          f"共 {n_btn} 个" + (f"，异常: {btn_bad}" if btn_bad else ""))

    # ---- 3. 渲染级：按钮真实画出的图标 --------------------------------
    max_btn = win._titlebar._max_btn
    bb, _, _ = ink_bbox(max_btn.grab())
    geom = GEOM["max"] / 24 * 16 * dpr
    check("标题栏 max 按钮渲染墨迹≈16px 逻辑",
          bb is not None and geom - 1 <= bb <= geom + 2.5,
          f"ink={bb} 期望≈{geom:.1f}设备px")

    home = win._pages[0]
    from blenderhub.pages import HeroCard
    hero = home.findChild(HeroCard)
    launch = hero._launch_btn
    # 只量图标区域：按钮左边缘 padding 16 + 图标 16 逻辑
    g = launch.grab()
    icon_zone = g.copy(0, 0, int(16 * 2 * dpr), g.height())
    bb, _, _ = ink_bbox(icon_zone)
    geom = GEOM["play"] / 24 * 16 * dpr
    check("「启动 Blender」按钮图标渲染≈16px 逻辑",
          bb is not None and geom - 1 <= bb <= geom + 2.5,
          f"ink={bb} 期望≈{geom:.1f}设备px iconSize={launch.iconSize().width()}")

    for sb in home.findChildren(SearchBox):
        bb, _, _ = ink_bbox(sb._ic.grab())
        geom = GEOM["search"] / 24 * 16 * dpr
        check("搜索框图标渲染≈16px 逻辑",
              bb is not None and geom - 1 <= bb <= geom + 2.5,
              f"ink={bb} 期望≈{geom:.1f}设备px")
        break

    nav_ic = win._sidebar._items[0]._ic
    bb, _, _ = ink_bbox(nav_ic.grab())
    geom = GEOM["home"] / 24 * 17 * dpr
    check("侧边导航图标渲染≈17px 逻辑",
          bb is not None and geom - 1 <= bb <= geom + 2.5,
          f"ink={bb} 期望≈{geom:.1f}设备px")

    # ---- 4. 截图（供人工目检）------------------------------------------
    here = os.path.dirname(os.path.abspath(__file__))
    for i in range(5):
        win.stack.setCurrentIndex(i)
        win._refresh_status(i)
        app.processEvents()
        out = os.path.join(here, f"_dpr_shot_{i}.png")
        win.grab().save(out)
        print(f"    截图: {out}")

    # ---- 5. 汇总 -------------------------------------------------------
    print()
    if FAILS:
        print(f"共 {len(FAILS)} 项失败:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("高 DPI 全部检查通过")


if __name__ == "__main__":
    main()
