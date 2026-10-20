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
}


_MAX_CKPT_BYTES = 1 << 20  # 1 MB：超限滚动截断，避免日志无限增长


def ckpt(msg: str) -> None:
    """启动检查点日志（定位打包版启动卡点用）。

    追加写入 %TEMP%\\blenderhub_start.log；超过 1 MB 时保留尾部半程继续写，
    防止长期使用后日志无限膨胀。
    """
    try:
        p = os.path.join(os.environ.get("TEMP", "."), "blenderhub_start.log")
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
