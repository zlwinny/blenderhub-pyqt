"""真实数据检测：Blender 安装 / .blend 项目 / 插件 / 磁盘容量。

所有数据均来自本机文件系统的真实探测，不掺 Mock：
- 安装：复用 system_ops.find_blender_installs() 的真实探测结果；
- 项目：扫描桌面 / 文档 / 下载 / 安装目录 / Blender recent-files.txt 中的目录，
  逐个读取 .blend 文件头解析真实版本号；
- 插件：扫描各 Blender 安装与用户配置目录下的 scripts/addons 与
  extensions/user_default，解析 bl_info / blender_manifest.toml；
- 体积 / 时间 / 磁盘剩余：全部取真实值。

扫描带时间预算与数量上限，保证启动耗时可控；任何单点失败只跳过该项，
不影响其余数据。
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import struct
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from .addon_store import canonical_category
from .system_ops import find_blender_installs

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "AppData", "Application Data",
    "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin",
    "System Volume Information", "venv", ".venv", ".cache", "Temp", "Tmp",
}
_MAX_PROJECTS = 300
_MAX_RECENT_DIRS = 12
_MAX_ADDONS = 200


@dataclass
class Install:
    exe: str
    name: str          # 目录名，如 blender-4.5.3-lts
    version: str       # "4.5"
    tag: str           # LTS / 测试版 / 正式版
    size_bytes: int
    mtime: float       # blender.exe 修改时间（安装时间近似）


@dataclass
class Project:
    name: str          # 文件名（不含扩展名）
    path: str
    version: str       # .blend 文件头解析出的版本，如 "4.5"；失败为 "未知"
    size_bytes: int
    mtime: float


@dataclass
class Addon:
    name: str
    author: str
    version: str
    category: str
    blender_ver: str   # 所属 Blender 版本目录
    path: str
    size_bytes: int
    builtin: bool = False   # True = 随 Blender 发行包自带（在线市场没有对应条目）


def _clean_category(cat: str) -> str:
    """清洗插件分类：去空白、限长 16，过滤乱码（须含 ASCII 字母或中文）。"""
    cat = (cat or "").strip()
    if not cat or len(cat) > 16:
        return ""
    ok = any(
        (ch.isascii() and ch.isalpha()) or "\u4e00" <= ch <= "\u9fff"
        for ch in cat
    )
    return cat if ok else ""


@dataclass
class Snapshot:
    installs: list[Install] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    addons: list[Addon] = field(default_factory=list)
    addon_roots: list[str] = field(default_factory=list)
    scanned_at: datetime = field(default_factory=datetime.now)
    elapsed: float = 0.0
    default_exe: str | None = None

    @property
    def default_install(self) -> Install | None:
        for inst in self.installs:
            if inst.exe == self.default_exe:
                return inst
        return self.installs[0] if self.installs else None

    @property
    def latest_install(self) -> Install | None:
        return self.installs[0] if self.installs else None

    @property
    def active_projects_7d(self) -> int:
        cutoff = time.time() - 7 * 86400
        return sum(1 for p in self.projects if p.mtime >= cutoff)

    @property
    def project_versions(self) -> list[str]:
        seen: dict[str, int] = {}
        for p in self.projects:
            seen[p.version] = seen.get(p.version, 0) + 1
        return [v for v, _ in sorted(seen.items(), key=lambda kv: -kv[1]) if v != "未知"]

    @property
    def addon_categories(self) -> list[str]:
        seen: dict[str, int] = {}
        for a in self.addons:
            cat = self.addon_chip_category(a)
            seen[cat] = seen.get(cat, 0) + 1
        return [c for c, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]

    def addon_chip_category(self, addon: Addon) -> str:
        return canonical_category(_clean_category(addon.category))


# ---------------------------------------------------------------- 安装
def _install_info(exe: str) -> Install:
    d = os.path.dirname(exe)
    name = os.path.basename(d)
    low = name.lower()
    m = re.search(r"(\d+\.\d+)(?:\.\d+)?", name)
    version = m.group(1) if m else "未知"
    if "lts" in low:
        tag = "LTS"
    elif "beta" in low or "alpha" in low or "rc" in low:
        tag = "测试版"
    else:
        tag = "正式版"
    try:
        st = os.stat(exe)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, 0
    size = size or dir_size(d)
    return Install(exe=exe, name=name, version=version, tag=tag,
                   size_bytes=size, mtime=mtime)


def _pick_default(installs: list[Install], default_version: str) -> str | None:
    if not installs:
        return None
    want = (default_version or "").strip().lower()
    if want:
        for inst in installs:
            label = f"{inst.version} {inst.tag}".lower()
            if label.startswith(want) or want in label:
                return inst.exe
    return installs[0].exe


# ---------------------------------------------------------------- 项目
def read_blend_version(path: str) -> str | None:
    """读取 .blend 文件头版本码并转换为 x.y 显示。

    文件头：b"BLENDER" + 指针宽度 + 字节序 + 3 位版本码（主版本 + 2 位），
    如 v450 → 4.5、v293 → 2.93、v500 → 5.0、v405 → 4.0。支持 gzip 压缩的 .blend。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        if head[:2] == b"\x1f\x8b":
            with gzip.open(path, "rb") as f:
                head = f.read(12)
    except OSError:
        return None
    if len(head) < 12 or head[:7] != b"BLENDER":
        return None
    try:
        code = head[9:12].decode("ascii")
        if not code.isdigit():
            return None
        n = int(code)
    except (UnicodeDecodeError, ValueError):
        return None
    major, rest = divmod(n, 100)
    if major >= 3:
        return f"{major}.{rest // 10}"
    return f"{major}.{rest // 10}" if rest % 10 == 0 else f"{major}.{rest}"


def read_blend_thumbnail(path: str, max_bytes: int = 32_000_000
                         ) -> tuple[int, int, bytes] | None:
    """提取 .blend 内嵌缩略图：TEST 块 = 2×int32(宽, 高) + 原始 RGBA 像素。

    返回 (宽, 高, RGBA字节)；无内嵌缩略图或解析失败返回 None。
    兼容 gzip 压缩保存的 .blend。缩略图由 Blender 保存文件时写入，
    即文件浏览器里看到的预览图。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:2] == b"\x1f\x8b":
                with gzip.open(path, "rb") as g:
                    return _scan_thumb_block(g, max_bytes)
            if len(head) < 12 or head[:7] != b"BLENDER":
                return None
            ptr = 8 if head[7:8] == b"-" else 4
            little = head[8:9] != b"V"
            return _scan_thumb_block(f, max_bytes, ptr, little)
    except (OSError, EOFError):
        return None


def _scan_thumb_block(f, max_bytes: int, ptr: int = 4,
                      little: bool = True) -> tuple[int, int, bytes] | None:
    """遍历 .blend 文件块，定位 TEST 块并取出宽高与 RGBA 像素。"""
    fmt = "<" if little else ">"
    hdr_len = 16 + ptr
    for _ in range(100_000):
        hdr = f.read(hdr_len)
        if len(hdr) < hdr_len:
            return None
        code = hdr[:4]
        size = struct.unpack(fmt + "I", hdr[4:8])[0]
        if code == b"TEST":
            data = f.read(size)
            if len(data) < 8 or size > max_bytes:
                return None
            w, h = struct.unpack(fmt + "ii", data[:8])
            if not (0 < w <= 8192 and 0 < h <= 8192):
                return None
            if len(data) < 8 + w * h * 4:
                return None
            return w, h, data[8:8 + w * h * 4]
        if code == b"ENDB" or size > max_bytes:
            return None
        f.seek(size, os.SEEK_CUR)
    return None


def _recent_dirs() -> list[str]:
    """Blender recent-files.txt 中出现过的目录（去重、限量）。"""
    base = os.path.join(
        os.environ.get("APPDATA", ""), "Blender Foundation", "Blender"
    )
    dirs: list[str] = []
    seen: set[str] = set()
    if not os.path.isdir(base):
        return dirs
    try:
        entries = sorted(os.listdir(base), reverse=True)
    except OSError:
        return dirs
    for name in entries:
        txt = os.path.join(base, name, "config", "recent-files.txt")
        if not os.path.isfile(txt):
            continue
        try:
            with open(txt, encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            p = line.strip().strip('"')
            if not p.lower().endswith(".blend"):
                continue
            d = os.path.dirname(p)
            key = os.path.normcase(os.path.normpath(d))
            if key not in seen and os.path.isdir(d):
                seen.add(key)
                dirs.append(d)
                if len(dirs) >= _MAX_RECENT_DIRS:
                    return dirs
    return dirs


def _walk_blend(root: str, max_depth: int, out: list[str], cap: int) -> None:
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(("$", "."))
        ]
        for fn in filenames:
            if fn.lower().endswith(".blend"):
                out.append(os.path.join(dirpath, fn))
                if len(out) >= cap:
                    return


def scan_projects(install_dir: str | None = None,
                  budget_s: float = 4.0) -> list[Project]:
    """扫描真实 .blend 项目：桌面 / 文档 / 下载 / 安装目录 / recent 目录。"""
    t0 = time.monotonic()
    home = os.path.expanduser("~")
    roots: list[tuple[str, int]] = []
    for sub in ("Desktop", "Documents", "Downloads"):
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            roots.append((p, 3))
    if install_dir and os.path.isdir(install_dir):
        roots.append((install_dir, 3))
    for d in _recent_dirs():
        if time.monotonic() - t0 > budget_s:
            break
        roots.append((d, 2))

    found: list[str] = []
    seen: set[str] = set()
    for root, depth in roots:
        batch: list[str] = []
        _walk_blend(root, depth, batch, _MAX_PROJECTS - len(found))
        for p in batch:
            key = os.path.normcase(os.path.normpath(p))
            if key not in seen:
                seen.add(key)
                found.append(p)
        if len(found) >= _MAX_PROJECTS:
            break

    projects: list[Project] = []
    for p in found:
        try:
            st = os.stat(p)
        except OSError:
            continue
        projects.append(Project(
            name=os.path.splitext(os.path.basename(p))[0],
            path=p,
            version=read_blend_version(p) or "未知",
            size_bytes=st.st_size,
            mtime=st.st_mtime,
        ))
    projects.sort(key=lambda x: -x.mtime)
    return projects


# ---------------------------------------------------------------- 插件
def _parse_bl_info(init_py: str) -> dict[str, str] | None:
    """从 __init__.py 提取 bl_info 的 name / author / version / category。"""
    try:
        with open(init_py, encoding="utf-8", errors="ignore") as f:
            text = f.read(65536)
    except OSError:
        return None
    m = re.search(r"bl_info\s*=\s*\{", text)
    if not m:
        return None
    depth, i, start = 0, m.end() - 1, m.end()
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = text[start:i]

    def _s(key: str) -> str:
        mm = re.search(rf'"{key}"\s*:\s*"([^"]*)"', block)
        return mm.group(1) if mm else ""

    ver = re.search(r'"version"\s*:\s*\(([^)]*)\)', block)
    version = ""
    if ver:
        nums = re.findall(r"\d+", ver.group(1))
        version = ".".join(nums[:3])
    return {"name": _s("name"), "author": _s("author"),
            "version": version, "category": _clean_category(_s("category"))}


def _parse_manifest(toml_path: str) -> dict[str, str] | None:
    """从 blender_manifest.toml（4.2+ 扩展）提取 name / version。"""
    try:
        with open(toml_path, encoding="utf-8", errors="ignore") as f:
            text = f.read(32768)
    except OSError:
        return None

    def _s(key: str) -> str:
        mm = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.M)
        return mm.group(1) if mm else ""

    name = _s("name")
    if not name:
        return None
    return {"name": name, "author": _s("maintainer"),
            "version": _s("version"), "category": "扩展"}


def _addon_roots(installs: list[Install]) -> list[tuple[str, str]]:
    """返回 (插件目录, Blender 版本) 列表：各安装的 scripts/addons + 用户配置目录。"""
    roots: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(path: str, ver: str) -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen and os.path.isdir(path):
            seen.add(key)
            roots.append((path, ver))

    for inst in installs:
        d = os.path.dirname(inst.exe)
        for sub in (os.path.join(d, inst.version, "scripts", "addons"),
                    os.path.join(d, "scripts", "addons"),
                    os.path.join(d, inst.version, "scripts", "addons_contrib")):
            _add(sub, inst.version)
    base = os.path.join(
        os.environ.get("APPDATA", ""), "Blender Foundation", "Blender"
    )
    if os.path.isdir(base):
        try:
            for name in os.listdir(base):
                if not re.fullmatch(r"\d+\.\d+", name):
                    continue
                _add(os.path.join(base, name, "scripts", "addons"), name)
                ext = os.path.join(base, name, "extensions", "user_default")
                if os.path.isdir(ext):
                    roots.append((ext, name))
        except OSError:
            pass
    return roots


def is_builtin_root(root: str, installs: list[Install]) -> bool:
    """插件目录是否落在 Blender 发行包内（而非用户配置目录）。

    内置插件在线市场一般没有对应条目，页面据此标注来源，避免看起来像「封面没加载」。
    """
    r = os.path.normcase(os.path.normpath(root))
    for inst in installs:
        d = os.path.normcase(os.path.normpath(os.path.dirname(inst.exe)))
        try:
            if os.path.commonpath([r, d]) == d:
                return True
        except ValueError:      # 不同盘符
            continue
    return False


def scan_addons(installs: list[Install]) -> tuple[list[Addon], list[str]]:
    """扫描真实已安装插件；同名插件只保留 Blender 版本最高的一份。"""
    roots = _addon_roots(installs)
    best: dict[str, Addon] = {}
    for root, blender_ver in roots:
        if len(best) >= _MAX_ADDONS:
            break
        builtin = is_builtin_root(root, installs)
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            info = _parse_manifest(os.path.join(path, "blender_manifest.toml")) \
                or _parse_bl_info(os.path.join(path, "__init__.py"))
            if info is None:
                continue
            key = os.path.normcase(name)
            addon = Addon(
                name=info.get("name") or name,
                author=info.get("author") or "",
                version=info.get("version") or "",
                category=info.get("category") or "",
                blender_ver=blender_ver,
                path=path,
                size_bytes=dir_size(path),
                builtin=builtin,
            )
            old = best.get(key)
            if old is None or _ver_key(blender_ver) > _ver_key(old.blender_ver):
                best[key] = addon
    addons = sorted(best.values(), key=lambda a: a.name.lower())
    return addons, [r for r, _ in roots]


def _ver_key(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", ver)) or (0,)


# ---------------------------------------------------------------- 工具
def dir_size(path: str, cap: int = 2_000_000) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
        if total > cap:
            break
    return total


def fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def fmt_disk_free(drive: str = "C:\\") -> str:
    try:
        free = shutil.disk_usage(drive).free
    except OSError:
        return "未知"
    return fmt_size(free)


def rel_time(ts: float) -> str:
    if ts <= 0:
        return "未知"
    days = (datetime.now() - datetime.fromtimestamp(ts)).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 7:
        return f"{days} 天前"
    if days < 30:
        return f"{days // 7} 周前"
    if days < 365:
        return f"{days // 30} 个月前"
    return f"{days // 365} 年前"


def install_age(mtime: float) -> str:
    if mtime <= 0:
        return "安装时间未知"
    days = (datetime.now() - datetime.fromtimestamp(mtime)).days
    if days <= 0:
        return "今天安装"
    if days < 30:
        return f"已安装 {days} 天"
    if days < 365:
        return f"已安装 {days // 30} 个月"
    return f"已安装 {days // 365} 年"


def _safe_extract_zip(zf: zipfile.ZipFile, dest_dir: str) -> tuple[bool, str]:
    """校验 zip 条目路径不越界后再解包，防止 zip-slip 把文件写到目标目录之外。

    zipfile.extractall 不会拦截 ``..`` 或绝对路径，反斜杠也能绕过仅按 ``/``
    的检查，因此这里统一分隔符、拒绝绝对路径 / 盘符 / 上级穿越，并规范化后
    确认落点仍在目标目录内。
    """
    base = os.path.abspath(dest_dir)
    for info in zf.infolist():
        norm = info.filename.replace("\\", "/")
        if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
            return False, f"压缩包含绝对路径条目：{info.filename}"
        if ".." in norm.split("/"):
            return False, f"压缩包含上级目录穿越条目：{info.filename}"
        target = os.path.abspath(os.path.join(base, *norm.split("/")))
        if target != base and not target.startswith(base + os.sep):
            return False, f"压缩包含越界条目：{info.filename}"
    zf.extractall(dest_dir)
    return True, ""


def extract_archive(path: str, dest_dir: str) -> tuple[bool, str]:
    """真实解包 .zip / .tar.xz / .tar.gz 到 dest_dir，返回 (成功, 信息)。"""
    low = path.lower()
    try:
        os.makedirs(dest_dir, exist_ok=True)
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                ok, msg = _safe_extract_zip(zf, dest_dir)
                if not ok:
                    return False, msg
        elif low.endswith((".tar.xz", ".txz", ".tar.gz", ".tgz", ".tar")):
            with tarfile.open(path) as tf:
                tf.extractall(dest_dir, filter="data")
        else:
            return False, "不支持的压缩格式（仅 .zip / .tar.xz）"
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
        return False, f"解包失败：{e}"
    return True, dest_dir


# ---------------------------------------------------------------- 汇总
def scan(install_dir: str | None = None,
         default_version: str = "") -> Snapshot:
    """执行一次完整真实检测，返回快照。"""
    t0 = time.monotonic()
    installs = [_install_info(e) for e in find_blender_installs()]
    projects = scan_projects(install_dir)
    addons, roots = scan_addons(installs)
    return Snapshot(
        installs=installs,
        projects=projects,
        addons=addons,
        addon_roots=roots,
        scanned_at=datetime.now(),
        elapsed=time.monotonic() - t0,
        default_exe=_pick_default(installs, default_version),
    )
