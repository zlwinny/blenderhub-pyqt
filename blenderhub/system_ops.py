"""真实系统操作：Blender 安装检测 / 启动 / 打开目录与 .blend 项目。

原则：所有操作都基于真实文件系统校验，Mock 数据中的路径不存在时如实返回失败，
由调用方降级为提示，不假装成功。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess


def _fixed_drives() -> list[str]:
    """本机固定磁盘（DRIVE_FIXED）根目录列表；非 Windows 或失败时回退 C:/D:。"""
    if os.name != "nt":
        return []
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(256)
        n = kernel32.GetLogicalDriveStringsW(256, buf)
        if not n:
            return []
        # 返回值是各盘符 NUL 连接的总长；buf.value 会在首个 NUL 处截断，须按长度取
        drives = [d for d in ctypes.wstring_at(buf, n).split("\0") if d]
        return [d for d in drives if kernel32.GetDriveTypeW(d) == 3]
    except Exception:
        return ["C:\\", "D:\\"]


# 便携版目录名：Blender 4.5 / blender-4.2.3 / Blender_3.6 等
# （不匹配 "Blender Hub"、"OctaneBlender" 等无关目录）
_PORTABLE_RE = re.compile(r"^blender[\s._-]*\d|^blender$", re.I)


def find_blender_installs() -> list[str]:
    r"""扫描各磁盘常见安装位置与 PATH，返回去重后的 blender.exe（新版本优先）。

    覆盖两类形态：
    - 标准安装：<根>\Blender Foundation\<版本目录>\[子目录\]blender.exe
      （根 = Program Files 环境目录 / 各固定磁盘根目录 / 常见软件目录）；
    - 便携版：<根>\<Blender x.y 目录>\blender.exe。
    """
    roots: list[str] = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        p = os.environ.get(env)
        if p and os.path.normcase(p) not in {os.path.normcase(r) for r in roots}:
            roots.append(p)
    for drive in _fixed_drives():
        for sub in ("", "Program Files", "Program Files (x86)",
                    "Blender", "BlenderFoundation", "Soft", "Software",
                    "Tools", "Apps"):
            p = os.path.normpath(os.path.join(drive, sub)) if sub else drive
            if os.path.normcase(p) not in {os.path.normcase(r) for r in roots}:
                roots.append(p)

    found: list[str] = []

    def _add_exe(d: str) -> None:
        exe = os.path.join(d, "blender.exe")
        if os.path.isfile(exe):
            found.append(exe)

    for root in roots:
        for base_name in ("Blender Foundation", "BlenderFoundation"):
            bf = os.path.join(root, base_name)
            if not os.path.isdir(bf):
                continue
            try:
                names = os.listdir(bf)
            except OSError:
                continue
            for name in names:
                vdir = os.path.join(bf, name)
                if not os.path.isdir(vdir):
                    continue
                _add_exe(vdir)
                try:
                    subs = os.listdir(vdir)
                except OSError:
                    continue
                for s in subs:
                    if os.path.isdir(os.path.join(vdir, s)):
                        _add_exe(os.path.join(vdir, s))
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if _PORTABLE_RE.match(name):
                _add_exe(os.path.join(root, name))

    on_path = shutil.which("blender")
    if on_path:
        found.append(on_path)
    seen: set[str] = set()
    unique: list[str] = []
    for p in found:
        key = os.path.normcase(os.path.normpath(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)

    def _ver_key(p: str) -> tuple[int, ...]:
        return tuple(int(x) for x in
                     re.findall(r"\d+", os.path.basename(os.path.dirname(p)))) or (0,)

    unique.sort(key=_ver_key, reverse=True)
    return unique


def launch_blender(exe: str | None = None, blend: str | None = None) -> tuple[bool, str]:
    """启动 Blender，可附带打开一个 .blender 项目。

    优先使用指定 exe；指定路径不存在时回退到本机检测到的最新安装。
    返回 (是否成功, 启动路径或失败原因)。
    """
    if blend and not os.path.isfile(blend):
        return False, "项目文件不存在（Mock 数据）"
    target = exe if exe and os.path.isfile(exe) else None
    if target is None:
        installs = find_blender_installs()
        target = installs[0] if installs else None
    if target is None:
        return False, "未检测到 Blender 安装"
    try:
        cmd = [target, blend] if blend else [target]
        subprocess.Popen(cmd, close_fds=True)
        return True, target
    except OSError as e:
        return False, f"启动失败：{e}"


def open_path(path: str) -> tuple[bool, str]:
    """资源管理器打开目录 / 默认程序打开文件；路径不存在时如实返回 False。"""
    if not path or not os.path.exists(path):
        return False, "路径不存在"
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - 交给 Windows Shell 用默认关联打开
        else:
            subprocess.Popen(["xdg-open", path], start_new_session=True)
        return True, path
    except OSError as e:
        return False, f"打开失败：{e}"


def open_dir_or_fallback(path: str) -> tuple[bool, str]:
    """打开目录；指定目录不存在时回退到本机检测到的 Blender 安装目录。"""
    ok, msg = open_path(path)
    if ok:
        return True, msg
    installs = find_blender_installs()
    if installs:
        return open_path(os.path.dirname(installs[0]))
    return False, "未找到安装目录"


def _recycle_win(paths: list[str]) -> tuple[bool, str]:
    """Windows：SHFileOperationW + FOF_ALLOWUNDO 移入回收站（可恢复）。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.USHORT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400
    # pFrom 要求双 NUL 结尾的路径列表
    buf = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    op = SHFILEOPSTRUCTW(
        hwnd=None, wFunc=FO_DELETE, pFrom=buf, pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        fAnyOperationsAborted=0, hNameMappings=None, lpszProgressTitle=None,
    )
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc == 0:
        return True, ""
    return False, f"回收站操作被拒绝或失败（错误码 {rc}），文件可能被占用"


def recycle_paths(paths: list[str]) -> tuple[bool, str]:
    """把文件移入回收站（可恢复）；任一失败都不做永久删除，返回 (成功, 信息)。"""
    paths = [p for p in paths if p]
    if not paths:
        return False, "没有待删除的文件"
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return False, f"{len(missing)} 个文件不存在（可能已被删除或移动）"
    if os.name == "nt":
        try:
            return _recycle_win(paths)
        except Exception as e:
            msg = f"调用回收站失败：{e}"
    else:
        msg = "当前系统不支持回收站"
    try:
        import send2trash  # type: ignore

        for p in paths:
            send2trash.send2trash(p)
        return True, ""
    except ImportError:
        return False, msg
    except Exception as e:
        return False, f"{msg}；send2trash 也失败：{e}"
