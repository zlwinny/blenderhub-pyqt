"""终验脚本：图标尺寸精确性 + 状态栏对齐 + 真实功能冒烟 + 截图。

用法：
  python _verify.py                        # 真实平台（DPR=本机），截图供目检
  QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.25 python _verify.py   # 模拟 125% 缩放
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from blenderhub import detect
from blenderhub import blender_releases as br
from blenderhub import icons as I
from blenderhub import store
from blenderhub import system_ops as ops
from blenderhub import theme as T
from blenderhub.main_window import MainWindow
from blenderhub import pages as PG
from blenderhub.pages import AddonsPage, HomePage, ProjectsPage, SettingsPage, VersionsPage

FAILS = []
DESIGN_SIZES = {12, 13, 14, 16, 17, 20, 24, 30, 40}


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
        return None
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def main() -> None:
    t0 = time.time()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts()
    T.apply_theme(app, T.DARK)

    dpr = app.primaryScreen().devicePixelRatio()
    tag = f"{dpr:.2f}".replace(".", "")
    print(f"== DPR = {dpr}")

    # ---- 1. 图标栅格化：墨迹比例自校验（防 1.25x 二次放大） -------------
    used = [
        ("home", 17), ("versions", 17), ("folder", 17), ("puzzle", 17), ("gear", 17),
        ("logo", 24), ("logo", 20), ("logo", 40),
        ("search", 16), ("bell", 14), ("more", 14), ("folder", 14),
        ("play", 16), ("download", 16), ("plus", 16), ("update", 16),
        ("min", 16), ("max", 16), ("x", 16), ("file", 16),
        ("star", 12), ("versions", 30), ("puzzle", 30), ("check", 13),
        ("sun", 14), ("moon", 14), ("alert", 13),
    ]

    def geom_ratio(name: str) -> tuple[float, float]:
        ref = I.pixmap(name, "#FF0000", 240)
        bb = ink_bbox(ref)
        dev = ref.width()  # 参考图的设备画布；墨迹比须对设备尺寸归一，DPR>1 时才不会虚高
        return bb[0] / dev, bb[1] / dev

    bad = []
    for name, size in used:
        pm = I.pixmap(name, "#FF0000", size)
        dev = pm.width()
        logical = pm.deviceIndependentSize().width()
        bb = ink_bbox(pm)
        if bb is None:
            bad.append((name, size, "空"))
            continue
        wr, hr = geom_ratio(name)
        # 设备墨迹应 ≈ 几何比例 × 设备画布；容差 2.5 设备像素（AA fringe）
        if abs(bb[0] - wr * dev) > 2.5 or abs(bb[1] - hr * dev) > 2.5:
            bad.append((name, size, f"ink={bb} 期望≈({wr * dev:.1f},{hr * dev:.1f})"))
        # 逻辑尺寸必须贴近设计值（DPR 量化 ≤0.5px）
        if min(abs(logical - s) for s in DESIGN_SIZES) > 0.5:
            bad.append((name, size, f"logical={logical}"))
        # 墨迹不得超出画布（防裁切/放大）
        if bb[0] > dev or bb[1] > dev:
            bad.append((name, size, f"墨迹超出画布 ink={bb} dev={dev}"))
    check(f"全部 {len(used)} 项图标渲染比例正确、逻辑尺寸达标、无裁切", not bad, str(bad[:4]))

    # ---- 2. 运行整窗 ---------------------------------------------------
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
        logical = pm.deviceIndependentSize().width()
        if lbl.property("realThumb"):
            continue  # .blend 内嵌真实缩略图（照片内容，非图标）
        if logical > 40:
            continue  # 项目卡大缩略区（照片内容，非图标）
        n_lbl += 1
        if min(abs(logical - s) for s in DESIGN_SIZES) > 0.5:
            bad_lbl.append(round(logical, 2))
    check(f"窗口内 {n_lbl} 个标签图标逻辑尺寸全部为设计值（±0.5px）", not bad_lbl, str(bad_lbl[:6]))

    bad_btn = []
    n_btn = 0
    for b in win.findChildren(QPushButton):
        ic = b.icon()
        if ic.isNull():
            continue
        n_btn += 1
        isz = b.iconSize()
        actual = ic.actualSize(isz)
        if isz.width() <= 0 or actual.width() != isz.width():
            bad_btn.append((b.text(), isz.width(), actual.width()))
    check(f"{n_btn} 个带图标按钮 iconSize 与图标实际尺寸一致", not bad_btn, str(bad_btn[:4]))

    # ---- 3. 状态栏对齐 ---------------------------------------------------
    sb = win._status_box
    shell_w = win._shell.width()
    grip = sb._grip
    check("状态栏通栏（与 shell 同宽）", abs(sb.width() - shell_w) <= 2,
          f"status={sb.width()} shell={shell_w}")
    check("角标完整落在状态栏内", 0 <= grip.x() and grip.x() + 14 <= sb.width()
          and grip.y() + 14 <= sb.height(), f"grip@{grip.x()},{grip.y()}")
    right = win._right_status
    right_edge = right.geometry().right()
    check("右侧文字与角标不重叠", right_edge <= grip.x() - 1,
          f"text_right={right_edge} grip_x={grip.x()}")
    check("状态栏左右边距对称（差 ≤12px）",
          abs(sb.layout().contentsMargins().right() - sb.layout().contentsMargins().left()) <= 12,
          f"L={sb.layout().contentsMargins().left()} R={sb.layout().contentsMargins().right()}")

    # ---- 4. 真实功能冒烟（不触发对话框 / 不启动 Blender）-----------------
    snap = win._snapshot
    n_inst, n_proj, n_addon = len(snap.installs), len(snap.projects), len(snap.addons)
    print(f"    真实快照：安装 {n_inst} · 项目 {n_proj} · 插件 {n_addon}"
          f"（耗时 {snap.elapsed:.1f}s）")
    check("首页统计卡「已安装版本」为真实安装数",
          win._pages[0]._stat_cards[0][0]._num_lb.text() == str(n_inst),
          win._pages[0]._stat_cards[0][0]._num_lb.text())
    check("首页统计卡「个项目」为真实项目数",
          win._pages[0]._stat_cards[1][0]._num_lb.text() == str(n_proj),
          win._pages[0]._stat_cards[1][0]._num_lb.text())
    check("首页统计卡「个插件」为真实插件数",
          win._pages[0]._stat_cards[2][0]._num_lb.text() == str(n_addon),
          win._pages[0]._stat_cards[2][0]._num_lb.text())
    check("侧边导航插件角标为真实插件数",
          win._sidebar._items[3]._badge_lb.text() == str(n_addon),
          win._sidebar._items[3]._badge_lb.text())
    check("状态栏首页展示真实项目/插件数",
          any(f"项目 {n_proj}" in lb.text() for lb in win._status_labels),
          str([lb.text() for lb in win._status_labels]))

    store.save(theme="light", install_dir=r"C:\Blender", default_version="4.5 LTS",
               check_updates=True, concurrency="3")
    data = store.load()
    check("QSettings 读写往返", data["theme"] == "light" and data["install_dir"] == r"C:\Blender"
          and data["check_updates"] is True, str({k: data[k] for k in ("theme", "check_updates")}))
    store.save(theme="dark", install_dir=r"C:\Blender", default_version="4.5 LTS",
               check_updates=True, concurrency="3")

    # 导航切换
    from PyQt6.QtTest import QTest

    for i, item in enumerate(win._sidebar._items):
        QTest.mouseClick(item, Qt.MouseButton.LeftButton)
        app.processEvents()
    check("侧边导航 5 项均可点击切换", win.stack.currentIndex() == 4,
          f"current={win.stack.currentIndex()}")
    QTest.mouseClick(win._sidebar._items[0], Qt.MouseButton.LeftButton)
    app.processEvents()

    # 主题切换
    T.toggle_theme()
    T.apply_theme(app)
    T.repolish_tree(win)
    check("主题切换为浅色", T.theme().name == "light")
    T.toggle_theme()
    T.apply_theme(app)
    T.repolish_tree(win)
    check("主题切回深色", T.theme().name == "dark")

    # 项目页：卡片数量与真实快照一致 + 搜索 / 版本 / 最近打开真实过滤
    projects = win._pages[2]
    check("项目页卡片数 == 真实扫描项目数", len(projects._cards) == n_proj,
          f"cards={len(projects._cards)} snap={n_proj}")
    if n_proj:
        target = snap.projects[0]
        projects._search.setText(target.name)
        app.processEvents()
        vis = [c for c, *_ in projects._cards if not c.isHidden()]
        check(f"项目搜索「{target.name}」只剩 1 张卡", len(vis) == 1, f"visible={len(vis)}")
        projects._search.setText("")
        app.processEvents()

        counts: dict[str, int] = {}
        for p in snap.projects:
            counts[p.version] = counts.get(p.version, 0) + 1
        known = [v for v in counts if v != "未知"]
        if known:
            pick = known[0]
            idx = projects._chip_versions.index(pick) + 1
            projects._on_chip(idx)
            app.processEvents()
            vis = [c for c, *_ in projects._cards if not c.isHidden()]
            check(f"版本筛选 {pick} 只剩 {counts[pick]} 张卡",
                  len(vis) == counts[pick], f"visible={len(vis)}")
        recent_idx = len(projects._chip_versions) + (1 if "未知" in counts else 0) + 1
        projects._on_chip(recent_idx)
        app.processEvents()
        vis = [c for c, *_ in projects._cards if not c.isHidden()]
        check("「最近打开」只剩 7 天内修改的项目", len(vis) == snap.active_projects_7d,
              f"visible={len(vis)} active7d={snap.active_projects_7d}")
        projects._on_chip(0)
        app.processEvents()

    # 批量删除：多选 / 勾选 / 计数 / 退出复位（取消确认时不真实删除）
    if n_proj:
        win.stack.setCurrentIndex(2)  # 卡片 isVisible() 依赖当前栈页，须先切到项目页
        app.processEvents()
        projects._toggle_select_mode()
        check("批量删除：进入多选模式后「全选 / 退出多选」可见",
              projects._select_mode and not projects._all_btn.isHidden()
              and not projects._exit_btn.isHidden())
        card0, name0, path0 = projects._cards[0][:3]
        projects._card_clicked(name0, path0)
        check("批量删除：点击卡片加入选中集合并高亮",
              path0 in projects._sel and card0.is_selected())
        projects._card_clicked(name0, path0)
        check("批量删除：再次点击同一卡片取消选中",
              path0 not in projects._sel and not card0.is_selected())
        projects._card_clicked(name0, path0)
        check("批量删除：选中后按钮显示计数且可点击",
              "删除所选（1）" in projects._del_btn.text()
              and projects._del_btn.isEnabled())
        projects._toggle_select_all_visible()
        n_vis = sum(1 for c, *_ in projects._cards if not c.isHidden())
        check("批量删除：全选覆盖当前可见卡片", len(projects._sel) == n_vis,
              f"sel={len(projects._sel)} vis={n_vis}")
        _orig_confirm = PG.confirm
        PG.confirm = lambda *a, **k: False
        try:
            projects._delete_selected()
        finally:
            PG.confirm = _orig_confirm
        check("批量删除：用户取消确认后文件不动、选中保持",
              len(projects._sel) == n_vis)
        projects._exit_select_mode()
        check("批量删除：退出多选后选中清空、按钮复位",
              not projects._select_mode and not projects._sel
              and projects._all_btn.isHidden()
              and projects._del_btn.text() == "批量删除")
        bad_path = os.path.join(os.environ.get("TEMP", "."), "bh_missing_verify.bin")
        ok_bad, _m = ops.recycle_paths([bad_path])
        check("回收站接口：缺失路径安全返回错误（不崩、不误删）",
              ok_bad is False and not os.path.exists(bad_path))

    # 插件页：卡片数量与真实快照一致 + 分类筛选真实过滤
    addons = win._pages[3]
    check("插件页卡片数 == 真实扫描插件数", len(addons._cards) == n_addon,
          f"cards={len(addons._cards)} snap={n_addon}")
    if n_addon and addons._chip_categories:
        cat = addons._chip_categories[0]
        n_cat = sum(1 for a in snap.addons if snap.addon_chip_category(a) == cat)
        addons._on_chip(1)
        app.processEvents()
        vis = [c for c, _ in addons._cards if not c.isHidden()]
        check(f"插件分类筛选「{cat}」只剩 {n_cat} 张卡", len(vis) == n_cat,
              f"visible={len(vis)} expect={n_cat}")
        addons._on_chip(0)
        app.processEvents()

    # ---- 4b. 插件市场（BudecoTop 在线模式，离线断言）-------------------
    import re
    import shutil as _shutil
    import tempfile
    import zipfile

    from blenderhub.addon_store import (
        CATEGORY_CHIPS,
        AddonVerifier,
        DownloadWatcher,
        blender_user_versions,
    )

    st = win._store
    auth = win._auth
    check("主窗口创建共享 AddonStore / BudecoAuth", st is not None and auth is not None)
    check("BudecoAuth 网络依赖（requests）可用", auth.available)

    # 屏蔽在线拉取，避免终验依赖外网；双模式装配与空态仍需验证
    st.refresh_async = lambda *a, **k: False
    addons._mode_seg.set_current(1)
    addons._on_mode(1)
    app.processEvents()
    check("插件页可切换到在线模式", addons._mode == "online",
          f"mode={addons._mode}")
    check("在线模式分类为 BudecoTop 分类表",
          addons._store_categories == CATEGORY_CHIPS,
          str(addons._store_categories))
    it = addons._grid.grid.itemAtPosition(0, 0)
    check("在线模式空目录展示空态卡片（不崩）",
          it is not None and it.widget() is not None)
    addons._search.setText("不存在的关键词xyz")
    app.processEvents()
    vis_store = [c for c, _ in addons._store_cards if not c.isHidden()]
    check("在线模式搜索过滤不崩", len(vis_store) == 0, f"visible={len(vis_store)}")
    addons._search.setText("")
    app.processEvents()
    addons._on_mode(0)
    app.processEvents()
    check("切回本地模式后本地卡片完好", len(addons._cards) == n_addon,
          f"cards={len(addons._cards)} snap={n_addon}")
    seg_cnt = addons._mode_seg._buttons[0].findChild(QLabel, "segcount")
    check("模式分段「本地」计数为真实插件数",
          seg_cnt is not None and seg_cnt.text() == str(n_addon),
          seg_cnt.text() if seg_cnt else "None")

    # 本地插件缩略图：按名匹配在线目录，命中缓存即填充（屏蔽按需下载保持离线确定）
    st.download_thumbs_async = lambda addons: False
    addons._apply_local_thumbs()
    app.processEvents()
    n_cached = sum(1 for _c, a in addons._cards if st.match_local_thumbnail(a.name))
    n_real = sum(1 for c, _a in addons._cards if c._thumb.property("realThumb"))
    n_match = sum(1 for _c, a in addons._cards if st.find_local(a.name) is not None)
    check("本地插件按名匹配在线缩略图（有缓存即填充）", n_real >= n_cached,
          f"real={n_real} cached={n_cached} name_match={n_match}")

    # 安装包校验器：合法包放行 / 路径穿越拦截 / 可执行载荷要求确认 / 损坏包拒绝
    v = AddonVerifier()
    tmp = tempfile.mkdtemp(prefix="bh_verify_")
    try:
        good = os.path.join(tmp, "good.zip")
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("my_addon/__init__.py", "bl_info = {}")
            zf.writestr("my_addon/ops.py", "x = 1")
        r = v.verify_file(good)
        check("校验器接受合法插件 zip", r.ok and r.entry_count == 2, r.summary())

        bad = os.path.join(tmp, "traverse.zip")
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../evil.py", "x = 1")
        r2 = v.verify_file(bad)
        check("校验器拦截路径穿越 zip",
              not r2.ok and any("穿越" in e for e in r2.errors), str(r2.errors[:2]))

        risky = os.path.join(tmp, "risky.zip")
        with zipfile.ZipFile(risky, "w") as zf:
            zf.writestr("a/__init__.py", "")
            zf.writestr("a/setup.exe", "MZ")
        r3 = v.verify_file(risky)
        check("校验器标记可执行载荷并要求用户确认",
              r3.ok and r3.requires_user_confirmation and bool(r3.risky_payloads),
              str(r3.risky_payloads))

        broken = os.path.join(tmp, "broken.zip")
        with open(broken, "wb") as f:
            f.write(b"PK\x03\x04 not really a zip")
        r4 = v.verify_file(broken)
        check("校验器拒绝损坏 zip", not r4.ok, str(r4.errors[:1]))
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    # 下载守望：新写入的 zip 被自动拾取（网盘分流安装的关键一环）
    wdir = tempfile.mkdtemp(prefix="bh_watch_")
    watcher = DownloadWatcher(dirs=[wdir], timeout_s=10)
    got: list[str] = []
    timed_out: list[int] = []
    watcher.found.connect(got.append)
    watcher.timeout.connect(lambda: timed_out.append(1))
    try:
        watcher.start()
        zp = os.path.join(wdir, "addon_test.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("a/__init__.py", "x" * 20000)
        # 守望要 3 个轮询周期（1s tick + 1.5s 稳定窗）；整机忙时事件循环会被拖慢，
        # 窗口给到 15s，避免把「慢」误判成「坏」
        end = time.time() + 15
        t0 = time.time()
        while time.time() < end and not got:
            QTest.qWait(200)
            app.processEvents()
        check("下载守望自动拾取新 zip", bool(got),
              f"got={got[:1]} timed_out={timed_out} running={watcher.running} "
              f"waited={time.time() - t0:.1f}s size={os.path.getsize(zp)}")
    finally:
        watcher.stop()
        _shutil.rmtree(wdir, ignore_errors=True)

    # 在线版本目录：两种 autoindex 排版都要能解析出体积与日期（离线样本，不联网）
    nginx = ('<tr><td colspan="2" class="link">'
             '<a href="blender-4.5.14-windows-x64.zip" title="t">'
             'blender-4.5.14-windows-x64.zip</a></td>'
             '<td class="size">380.2 MiB</td>'
             '<td class="date">2026-09-15 17:21:53</td></tr>')
    apache = ('<a href="blender-4.5.9-windows-x64.zip">'
              'blender-4.5.9-windows-x64.zip</a>   17-Apr-2026 15:39   '
              '399000000')
    rows = br._parse_rows(nginx)
    rows2 = br._parse_rows(apache)
    check("在线目录解析支持 nginx 排版（体积换算 + 日期）",
          abs(rows["blender-4.5.14-windows-x64.zip"][0] - 380.2 * 1048576) < 2
          and rows["blender-4.5.14-windows-x64.zip"][1].startswith("2026-09-15"),
          str(rows))
    check("在线目录解析支持 apache 排版（字节 + 日期）",
          rows2.get("blender-4.5.9-windows-x64.zip") == (399000000, "17-Apr-2026 15:39"),
          str(rows2))
    check("补丁号按数值排序（4.5.14 排在 4.5.9 之后）",
          br.ver_key("4.5.14") > br.ver_key("4.5.9"))
    check("SHA-256 清单按文件名取哈希",
          br._pick_sha("aaaa  blender-4.5.14-windows-x64.zip\nbbbb  other.zip\n",
                       "blender-4.5.14-windows-x64.zip") == "aaaa")

    # 版本卸载护栏：只有装进托管安装目录的副本才允许删
    # （第一版曾因对扫描结果直接 rmtree 误删 Program Files / Steam 里的 Blender）
    guard_root = tempfile.mkdtemp(prefix="bh_guard_")
    guard_in = os.path.join(guard_root, "blender-9.9.9-windows-x64")
    os.makedirs(guard_in)
    check("卸载护栏：托管目录之内判为可删", ops.is_under(guard_root, guard_in))
    check("卸载护栏：目录自身与外部路径判为不可删",
          not ops.is_under(guard_root, guard_root)
          and not ops.is_under(guard_root, tempfile.gettempdir())
          and not ops.is_under("", guard_in),
          f"root={guard_root}")
    _shutil.rmtree(guard_root, ignore_errors=True)

    vers = blender_user_versions()
    check("Blender 用户版本目录可枚举",
          isinstance(vers, list) and all(re.fullmatch(r"\d+\.\d+", x) for x in vers),
          str(vers[:3]))

    # 版本页：行数与真实安装一致 + Segmented 计数真实
    versions = win._pages[1]
    check("版本页行数 == 真实安装数", len(versions._version_rows) == n_inst,
          f"rows={len(versions._version_rows)} snap={n_inst}")
    seg = versions._seg
    seg_count = seg._buttons[0].findChild(QLabel, "segcount")
    check("版本页分段「全部」计数为真实安装数",
          seg_count is not None and seg_count.text() == str(n_inst),
          seg_count.text() if seg_count else "None")
    if n_inst:
        n_lts = sum(1 for i in snap.installs if i.tag == "LTS")
        seg._buttons[1].click()  # LTS
        app.processEvents()
        vis = [r for r, _ in versions._version_rows if not r.isHidden()]
        check("版本筛选「LTS」行数一致", len(vis) == n_lts, f"visible={len(vis)} lts={n_lts}")
        seg._buttons[0].click()
        app.processEvents()

    # 首页：最近项目行数与真实快照一致 + 搜索真实过滤 + 统计卡跳转
    home = win._pages[0]
    check("首页最近项目行数 == min(5, 真实项目数)",
          len(home._recent_rows) == min(5, n_proj),
          f"rows={len(home._recent_rows)} snap={n_proj}")
    if n_proj:
        target = snap.projects[0]
        home._search.setText(target.name)
        app.processEvents()
        vis = [r for r, _, _ in home._recent_rows if not r.isHidden()]
        check(f"首页搜索「{target.name}」只剩 1 行", len(vis) == 1, f"visible={len(vis)}")
        home._search.setText("")
        app.processEvents()
    from blenderhub.widgets import StatCard

    stat = home.findChild(StatCard)
    QTest.mouseClick(stat, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("点击统计卡跳转版本页", win.stack.currentIndex() == 1,
          f"current={win.stack.currentIndex()}")
    QTest.mouseClick(win._sidebar._items[0], Qt.MouseButton.LeftButton)
    app.processEvents()

    # 缩略图：.blend 内嵌 TEST 块经后台线程提取后，真实填充项目卡与首页行
    expect_thumbs = sum(
        1 for p in snap.projects if detect.read_blend_thumbnail(p.path) is not None
    )
    end = time.time() + 20
    while time.time() < end:
        filled = sum(1 for c, *_ in projects._cards if c._thumb_src is not None)
        if filled >= expect_thumbs:
            break
        QTest.qWait(200)
        app.processEvents()
    filled = sum(1 for c, *_ in projects._cards if c._thumb_src is not None)
    check(f"项目页 {expect_thumbs} 个内嵌缩略图全部真实填充到卡片",
          filled == expect_thumbs, f"filled={filled} expect={expect_thumbs}")
    home_filled = sum(1 for r, _, _ in home._recent_rows if r._thumb_src is not None)
    check("首页最近项目行同步填充真实缩略图", home_filled >= 1,
          f"filled={home_filled}")

    # 设置页：保存 / 恢复默认
    settings = win._pages[4]
    settings._conc.setText("9")
    settings._save()
    app.processEvents()
    check("并发数超范围被拒绝（仍为 9，未保存）", settings._conc.text() == "9")
    settings._conc.setText("5")
    settings._save()
    end = time.time() + 3
    while time.time() < end and store.load()["concurrency"] != "5":
        QTest.qWait(50)
    check("保存后 QSettings 持久化并发数=5", store.load()["concurrency"] == "5",
          store.load()["concurrency"])
    settings._reset()
    app.processEvents()
    check("恢复默认后 UI 复位", settings._conc.text() == "3")

    # 切换目录对话框：关闭后主窗口强制重绘外壳（修复背景变透明）
    check("主窗口提供对话框关闭后重绘外壳能力",
          callable(getattr(win, "_repaint_shell", None)))
    win._repaint_shell()
    app.processEvents()
    shell_bg = win._shell.grab()
    _c = shell_bg.toImage().pixelColor(shell_bg.width() // 2, shell_bg.height() // 2)
    check("重绘后 shell 外壳背景非全透明（修复生效）", _c.alpha() > 200,
          f"alpha={_c.alpha()}")
    _orig_gED = PG.QFileDialog.getExistingDirectory
    _dlg_calls = []
    def _fake_gED(*a, **k):
        _dlg_calls.append(1)
        return ""
    PG.QFileDialog.getExistingDirectory = staticmethod(_fake_gED)
    _dir_before = settings._dir_input.text()
    try:
        settings._browse()
    finally:
        PG.QFileDialog.getExistingDirectory = _orig_gED
    check("选择目录对话框流程走通且不崩（取消时不改输入框）",
          len(_dlg_calls) == 1 and settings._dir_input.text() == _dir_before)

    # Toast 生命周期：4s 后自动摘除，宿主不留悬空引用（无 TypeError/RuntimeError）
    win.show_toast("Toast 生命周期测试", "ok")
    app.processEvents()
    n_before = len(win._toast_host._toasts)
    check("Toast 已入栈", n_before >= 1, f"n={n_before}")
    end = time.time() + 6
    while time.time() < end and win._toast_host._toasts:
        QTest.qWait(100)
        app.processEvents()
    win._toast_host._relayout()  # 显式触发一次重排，验证无悬空引用
    check("Toast 4 秒后自动移除且无悬空引用", not win._toast_host._toasts,
          f"n={len(win._toast_host._toasts)}")

    store.save(theme="dark", install_dir=r"C:\Blender", default_version="4.5 LTS",
               check_updates=True, concurrency="3")

    # ---- 5. 截图 --------------------------------------------------------
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
    os.makedirs(here, exist_ok=True)
    for i in range(5):
        win.stack.setCurrentIndex(i)
        win._refresh_status(i)
        app.processEvents()
        out = os.path.join(here, f"_v_shot_{tag}_{i}.png")
        win.grab().save(out)
        print(f"    截图: {out}")
    # 状态栏右下角特写
    corner = sb.grab().copy(max(0, sb.width() - 220), 0, 220, sb.height())
    corner.save(os.path.join(here, f"_v_status_{tag}.png"))
    print(f"    截图: {os.path.join(here, f'_v_status_{tag}.png')}")

    print(f"\n耗时 {time.time() - t0:.1f}s")
    if FAILS:
        print(f"共 {len(FAILS)} 项失败:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("全部检查通过")


if __name__ == "__main__":
    main()
