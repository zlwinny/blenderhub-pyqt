"""QSettings 持久化：主题 / 安装目录 / 默认版本 / 更新检查 / 下载并发数。

存放于注册表 HKCU\\Software\\ZLStudio\\BlenderHub，重启后自动恢复。
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QSettings

DEFAULTS = {
    "theme": "dark",
    "install_dir": r"C:\Blender",
    "default_version": "4.5 LTS",
    "check_updates": True,
    "concurrency": "3",
    # 本应用装过东西的安装根目录：卸载保护只认这些目录之内的版本
    "managed_roots": [],
}


_MAX_CKPT_BYTES = 1 << 20  # 1 MB：超限滚动截断，避免日志无限增长

# 诊断开关：BLENDERHUB_DEBUG=1 才写启动检查点与 diag 日志。
# 崩溃日志（main._install_crash_log）不受此开关控制，始终写。
DEBUG = os.environ.get("BLENDERHUB_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _start_log() -> str:
    return os.path.join(os.environ.get("TEMP", "."), "blenderhub_start.log")


def begin_session() -> None:
    """新一轮启动：清掉上一次留下的检查点日志。"""
    if not DEBUG:
        return
    try:
        os.remove(_start_log())
    except OSError:
        pass


def ckpt(msg: str) -> None:
    """启动检查点日志（定位打包版启动卡点用）。

    追加写入 %TEMP%\\blenderhub_start.log；超过 1 MB 时保留尾部半程继续写，
    防止长期使用后日志无限膨胀。
    """
    if not DEBUG:
        return
    try:
        p = _start_log()
        if os.path.isfile(p) and os.path.getsize(p) > _MAX_CKPT_BYTES:
            with open(p, "rb") as f:
                f.seek(-(_MAX_CKPT_BYTES // 2), os.SEEK_END)
                tail = f.read()
            with open(p, "wb") as f:
                f.write(tail)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


def load() -> dict:
    s = QSettings("ZLStudio", "BlenderHub")
    data = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if not s.contains(key):
            continue
        raw = s.value(key)
        if isinstance(default, bool):
            data[key] = str(raw) in ("1", "true", "True")
        else:
            data[key] = raw
    return data


def save(**kwargs) -> None:
    s = QSettings("ZLStudio", "BlenderHub")
    for key, value in kwargs.items():
        s.setValue(key, "1" if value is True else "0" if value is False else value)
    s.sync()


def managed_roots() -> list[str]:
    """本应用装过 Blender 的安装根目录：当前安装目录 + 历史登记且仍存在的。

    卸载保护只认这些目录之内的安装；已经不存在的目录要滤掉，
    否则删掉又重建的无关目录会被当成合法根。
    """
    data = load()
    extra = data.get("managed_roots") or []
    if isinstance(extra, str):
        extra = [extra]
    roots: list[str] = []
    seen: set[str] = set()
    for r in [data.get("install_dir") or "", *extra]:
        r = str(r or "")
        key = os.path.normcase(os.path.normpath(r)) if r else ""
        if not r or key in seen or not os.path.isdir(r):
            continue
        seen.add(key)
        roots.append(r)
    return roots


def remember_managed_root(root: str) -> None:
    """登记安装根目录（保存即创建：这样读回来的目录一定存在）。"""
    root = str(root or "")
    if not root:
        return
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass
    cur = load().get("managed_roots") or []
    if isinstance(cur, str):
        cur = [cur]
    out = [str(x) for x in cur if str(x)]
    if os.path.normcase(os.path.normpath(root)) not in {
            os.path.normcase(os.path.normpath(x)) for x in out}:
        out.append(root)
        save(managed_roots=out)


# -- 窗口几何 ---------------------------------------------------------
# 与业务配置分开存放：geometry 是 QByteArray（Qt saveGeometry 二进制），
# 走 load()/save() 的字符串转换会把它写坏，必须用 setValue 原样存取。
_GEOM_KEY = "window_geometry"


def load_geometry():
    """读取上次保存的窗口几何（QByteArray）；无记录返回 None。"""
    s = QSettings("ZLStudio", "BlenderHub")
    if not s.contains(_GEOM_KEY):
        return None
    val = s.value(_GEOM_KEY)
    return val if val is not None else None


def save_geometry(blob) -> None:
    s = QSettings("ZLStudio", "BlenderHub")
    s.setValue(_GEOM_KEY, blob)
    s.sync()
