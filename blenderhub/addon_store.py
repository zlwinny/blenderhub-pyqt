"""BudecoTop 插件市场功能层：拉取 / 登录 / 下载链解析 / 安全校验 / 守望安装。

实现参照本机 D 盘 BlenderHub 正式版（反汇编核对），UI 架构不变——
本模块只提供数据与纯功能，供 AddonsPage「在线」模式与对话框调用：

- AddonStore    budeco.top 目录爬取（BeautifulSoup）+ JSON 缓存 + 缩略图缓存 + 后台刷新
- BudecoAuth    登录态管理：Firefox cookie 同步 / 手动粘贴 / QSettings 持久化 / 定期复验
- resolve_download  文章页 → 下载链解析：直链 zip 或网盘分享（链接 + 提取码）
- AddonVerifier 安装包纵深校验：HTTP 头 / zip 魔数 / 压缩炸弹 / 路径遍历 / 符号链接 /
                 可执行载荷 / SHA-256 审计日志（install_audit.jsonl）
- DownloadWatcher  下载目录守望：用户在自己的浏览器里下完 zip 后自动拾取安装
- install_zip  校验通过的 zip 解压落位到目标 Blender 版本插件目录

线程约定：所有网络 IO 必须放进 QRunnable 经 QThreadPool 执行；
结果经 pyqtSignal 队列回主线程；requests.Session 非线程安全，内部加锁。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlparse, parse_qs

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal

log = logging.getLogger("blenderhub.addon_store")

# ---------------------------------------------------------------- 常量
BASE = "https://budeco.top"
PAGE_URL = f"{BASE}/category/plugins/page/{{page}}"
OAUTH_URL = f"{BASE}/oauth/socialogin/?act=login&type=qq"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 单篇文章页最多尝试的下载候选数，避免对几十个 href 串行发起请求
_MAX_DOWNLOAD_CANDIDATES = 6

# 文章页未登录时的特征标记（命中任一即视为未登录）
_LOGIN_MARKERS = ("nav-login no", "signin-loader")
# WordPress 登录 cookie 前缀
_LOGIN_COOKIE = "wordpress_logged_in"

# 网盘域名（命中即判定为网盘分流而非直链）
_PAN_DOMAINS = (
    "pan.baidu.com", "www.aliyundrive.com", "aliyundrive.com",
    "pan.quark.cn", "www.lanzou", "lanzoux.com", "lanzoui.com",
    "www.123pan.com", "123pan.com",
)

_RE_DOWNLOAD_HREF = re.compile(r"""href=["']?(download\.php\?[^"'\s>]+)""")
_RE_POSTID = re.compile(r"erphpdown/download\.php\?postid=(\d+)")
_RE_CODE = re.compile(r"""erphpdown-code["\']?>([^<]{1,12})<""")
_RE_HREF_URL = re.compile(r"""href=["'](https?://[^"'\s>]+)""")

# Blender 插件可执行载荷扩展名（正常插件不需要，出现即要求人工确认）
_EXEC_EXTS = {
    ".cmd", ".dll", ".sh", ".exe", ".com", ".msi", ".scr",
    ".jar", ".vbs", ".ps1", ".bat",
}

# 校验阈值
_MAX_ENTRIES = 5000            # 条目数上限
_MAX_UNCOMPRESSED = 512 << 20  # 解压后总体积上限 512MB（防 zip 炸弹）
_MAX_ENTRY_SIZE = 128 << 20    # 单条目上限 128MB
_MAX_FILE_SIZE = 512 << 20     # 安装包文件本体上限
_RATIO_WARN = 100.0            # 压缩比超过 100:1 记警告

CATEGORY_CHIPS = ["全部", "建模", "雕刻", "渲染", "动画", "导入导出", "社区精选"]

# 分类推断关键词表（名称 + 描述命中即归入）
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("建模", ("model", "mesh", "boolean", "bevel", "extrude", "retopo",
             "topology", "cad", "建筑", "墙体")),
    ("雕刻", ("sculpt", "brush", "texture paint", "雕刻", "笔刷")),
    ("渲染", ("render", "material", "shader", "light", "texture",
             "材质", "灯光", "渲染")),
    ("动画", ("rig", "animation", "armature", "pose", "骨骼", "绑定", "动画", "动作")),
    ("导入导出", ("import", "export", "io", "format", "导入", "导出", "格式")),
]

_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("建模", ("建模", "model", "mesh", "boolean", "拓扑", "retopo")),
    ("渲染", ("渲染", "render", "材质", "material", "shader", "灯光", "light", "纹理", "贴图")),
    ("动画", ("动画", "animation", "rig", "骨骼", "绑定", "动作")),
    ("导入导出", ("导入", "导出", "import", "export", "格式")),
    ("雕刻", ("雕刻", "sculpt", "笔刷")),
    ("特效", ("粒子", "particle", "流体", "fluid", "火焰", "烟雾", "爆炸",
             "破碎", "布料", "毛发")),
    ("UV", ("UV", "展UV", "贴图")),
    ("节点", ("节点", "node", "几何节点", "geonode")),
    ("硬表面", ("硬表面", "hard surface", "布尔", "切割")),
    ("建筑", ("建筑", "arch", "城市", "室内", "景观")),
    ("AI", ("AI", "人工智能", "stable diffusion")),
    ("效率", ("效率", "工具", "toolkit", "工作流")),
    ("程序化", ("程序化", "procedural", "参数化")),
]


# ---------------------------------------------------------------- 数据结构
@dataclass
class StoreAddon:
    """BudecoTop 插件条目。"""

    id: str = ""
    name: str = ""
    author: str = ""
    category: str = ""
    description: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    license: str = ""
    source: str = "budeco"
    source_url: str = ""
    download_url: str = ""
    thumbnail_url: str = ""
    thumb_path: str = ""
    free: bool = True
    price: str = "免费"
    cached_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "StoreAddon":
        known = set(StoreAddon.__dataclass_fields__)
        return StoreAddon(**{k: v for k, v in d.items() if k in known})


@dataclass
class DownloadTarget:
    """一次下载链解析的结果。ok=False 时 note 为原因。"""

    ok: bool = False
    kind: str = ""        # "zip" 直链 / "pan" 网盘
    url: str = ""
    code: str = ""        # 网盘提取码
    note: str = ""
    page_url: str = ""

    @property
    def is_direct_zip(self) -> bool:
        return self.ok and self.kind == "zip"


@dataclass
class VerifyResult:
    """安装包校验结果。"""

    ok: bool = True
    sha256: str = ""
    size_bytes: int = 0
    entry_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risky_payloads: list[str] = field(default_factory=list)

    @property
    def requires_user_confirmation(self) -> bool:
        return bool(self.risky_payloads)

    def summary(self) -> str:
        if self.errors:
            return "校验失败：" + "；".join(self.errors[:3])
        parts = [f"SHA-256: {self.sha256[:16]}…",
                 f"大小: {self.size_bytes / 1024:.0f} KB",
                 f"文件数: {self.entry_count}"]
        if self.risky_payloads:
            parts.append("⚠ 含可执行载荷：" + ", ".join(self.risky_payloads[:5]))
        for w in self.warnings[:2]:
            parts.append("⚠ " + w)
        return " · ".join(parts)


class NetBridge(QObject):
    """后台网络任务的信号桥（GUI 线程创建，工作线程 emit 自动队列化）。

    progress(pct) / finished(ok, message) / payload(任意 Python 对象)。
    """

    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    payload = pyqtSignal(object)


def _requests():
    """延迟导入 requests；缺失时返回 None（功能降级，不崩启动）。"""
    try:
        import requests
        return requests
    except Exception:
        return None


def _bs4():
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup
    except Exception:
        return None


def has_net_deps() -> bool:
    """在线市场依赖（requests + beautifulsoup4）是否可用。"""
    return _requests() is not None and _bs4() is not None


def cache_root() -> str:
    """插件市场缓存根目录：%LOCALAPPDATA%\\BlenderHub\\addons_cache。"""
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    root = os.path.join(local, "BlenderHub", "addons_cache")
    os.makedirs(root, exist_ok=True)
    return root


def audit_log_path() -> str:
    return os.path.join(cache_root(), "install_audit.jsonl")


# ---------------------------------------------------------------- 安全校验
class AddonVerifier:
    """插件安装包校验器（纯逻辑，无 GUI 依赖）。

    能挡住传输损坏与明显恶意载荷，挡不住有组织的投毒源站——
    因此命中风险特征时不直接拦死，而是要求用户显式确认。
    """

    # -- zip 结构（字节流与文件共用） ----------------------------------
    def _scan_zip(self, zf: zipfile.ZipFile, r: VerifyResult) -> None:
        names = zf.namelist()
        r.entry_count = len(names)
        if not names:
            r.ok = False
            r.errors.append("压缩包为空")
            return
        if len(names) > _MAX_ENTRIES:
            r.ok = False
            r.errors.append(f"条目数异常（{len(names)} > {_MAX_ENTRIES}）")
            return
        total = 0
        has_init = False
        for info in zf.infolist():
            name = info.filename
            total += info.file_size
            if total > _MAX_UNCOMPRESSED:
                r.ok = False
                r.errors.append(
                    f"解压后体积异常（{total / 1048576:.0f} MB > "
                    f"{_MAX_UNCOMPRESSED // 1048576} MB），疑似压缩炸弹")
                return
            if info.file_size > _MAX_ENTRY_SIZE:
                r.ok = False
                r.errors.append(f"条目过大：{name}（{info.file_size // 1048576} MB）")
                return
            # 统一分隔符再判断：Windows 恶意 zip 可用反斜杠绕过 `/` 拆分
            norm = name.replace("\\", "/")
            if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
                r.ok = False
                r.errors.append(f"压缩包含绝对路径条目：{name}")
                return
            if ".." in norm.split("/"):
                r.ok = False
                r.errors.append(f"压缩包含上级目录穿越条目：{name}")
                return
            # 符号链接：external_attr 高 16 位是 st_mode
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                r.ok = False
                r.errors.append(f"压缩包含符号链接条目：{name}")
                return
            base = name.rsplit("/", 1)[-1]
            if base in ("__init__.py", "blender_manifest.toml"):
                has_init = True
            ext = os.path.splitext(base)[1].lower()
            if ext in _EXEC_EXTS:
                r.risky_payloads.append(name)
        if not has_init:
            r.ok = False
            r.errors.append("未找到 __init__.py 或 blender_manifest.toml，"
                            "不是有效的 Blender 插件包")
            return
        comp = sum(i.compress_size for i in zf.infolist()) or 1
        ratio = total / comp
        if ratio > _RATIO_WARN:
            r.warnings.append(
                f"压缩比异常高（{ratio:.0f}:1），内容可能经过刻意混淆")

    # -- HTTP 响应 ------------------------------------------------------
    def verify_response(self, status_code: int, content: bytes,
                        content_type: str = "",
                        content_length: int | None = None) -> VerifyResult:
        import io

        r = VerifyResult()
        r.size_bytes = len(content)
        r.sha256 = hashlib.sha256(content).hexdigest()
        if status_code != 200:
            r.ok = False
            r.errors.append(f"HTTP 状态码 {status_code}")
            return r
        if not content:
            r.ok = False
            r.errors.append("响应内容为空")
            return r
        if "html" in (content_type or "").lower():
            r.ok = False
            r.errors.append(f"返回的是网页而非安装包（Content-Type: {content_type}）")
            return r
        if content_length is not None and content_length != len(content):
            r.ok = False
            r.errors.append(
                f"传输不完整：声明 {content_length} 字节，实际收到 {len(content)} 字节")
            return r
        if content[:2] != b"PK":
            r.ok = False
            r.errors.append("不是有效的 zip 文件（缺少 PK 文件头）")
            return r
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                self._scan_zip(zf, r)
        except zipfile.BadZipFile as e:
            r.ok = False
            r.errors.append(f"zip 文件损坏或格式非法：{e}")
        except Exception as e:
            r.ok = False
            r.errors.append(f"校验过程出错：{e}")
        return r

    # -- 本地文件 -------------------------------------------------------
    def verify_file(self, path: str) -> VerifyResult:
        r = VerifyResult()
        try:
            r.size_bytes = os.path.getsize(path)
        except OSError as e:
            r.ok = False
            r.errors.append(f"无法读取安装包：{e}")
            return r
        if r.size_bytes > _MAX_FILE_SIZE:
            r.ok = False
            r.errors.append(
                f"安装包过大（{r.size_bytes / 1048576:.0f} MB > "
                f"{_MAX_FILE_SIZE // 1048576} MB）")
            return r
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                head = f.read(4)
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError as e:
            r.ok = False
            r.errors.append(f"无法读取安装包：{e}")
            return r
        r.sha256 = h.hexdigest()
        if head[:2] != b"PK":
            r.ok = False
            r.errors.append("不是有效的 zip 文件（缺少 PK 文件头）")
            return r
        try:
            with zipfile.ZipFile(path) as zf:
                self._scan_zip(zf, r)
        except zipfile.BadZipFile as e:
            r.ok = False
            r.errors.append(f"zip 文件损坏或格式非法：{e}")
        except Exception as e:
            r.ok = False
            r.errors.append(f"校验过程出错：{e}")
        return r

    # -- 审计 -----------------------------------------------------------
    def record_audit(self, addon_id: str, addon_name: str, source_url: str,
                     result: VerifyResult,
                     target_version: str | None = None) -> None:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "addon_id": addon_id,
            "addon_name": addon_name,
            "source_url": source_url,
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "entry_count": result.entry_count,
            "warnings": result.warnings,
            "risky_payloads": result.risky_payloads,
            "target_version": target_version,
        }
        try:
            os.makedirs(os.path.dirname(audit_log_path()), exist_ok=True)
            with open(audit_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("审计日志写入失败：%s", e)

    def read_audit(self, limit: int = 50) -> list[dict]:
        path = audit_log_path()
        if not os.path.isfile(path):
            return []
        out: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        return out[-limit:][::-1]


# ---------------------------------------------------------------- 登录态
def _firefox_profile_dirs() -> list[str]:
    """枚举 Firefox profile 目录（含 profiles.ini 自定义路径）。"""
    appdata = os.environ.get("APPDATA", "")
    base = os.path.join(appdata, "Mozilla", "Firefox")
    if not os.path.isdir(base):
        return []
    dirs: list[str] = []
    ini = os.path.join(base, "profiles.ini")
    if os.path.isfile(ini):
        try:
            with open(ini, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in re.finditer(r"\[Profile[^\]]*\]([^[]+)", text):
                body = m.group(1)
                rel = re.search(r"IsRelative=(\d)", body)
                path = re.search(r"^Path=(.+)$", body, re.M)
                if not path:
                    continue
                p = path.group(1).strip()
                full = p if (rel and rel.group(1) == "0") else os.path.join(base, p)
                if os.path.isfile(os.path.join(full, "cookies.sqlite")):
                    dirs.append(full)
        except OSError:
            pass
    profiles = os.path.join(base, "Profiles")
    if os.path.isdir(profiles):
        try:
            for n in sorted(os.listdir(profiles)):
                d = os.path.join(profiles, n)
                if os.path.isfile(os.path.join(d, "cookies.sqlite")) and d not in dirs:
                    dirs.append(d)
        except OSError:
            pass
    return dirs


def _copy_cookie_db(profile: str, work: str) -> str:
    db = os.path.join(work, "cookies.sqlite")
    shutil.copy2(os.path.join(profile, "cookies.sqlite"), db)
    for suffix in ("-wal", "-shm"):
        src = os.path.join(profile, "cookies.sqlite" + suffix)
        if os.path.isfile(src):
            shutil.copy2(src, db + suffix)
    return db


def _read_firefox_profile(profile: str, domain: str = "budeco.top"
                          ) -> tuple[dict[str, str], int, str]:
    """读单个 profile 的 cookie（复制 sqlite 到临时目录，只读不解锁）。"""
    work = tempfile.mkdtemp(prefix="bh_ff_cookie_")
    try:
        try:
            db = _copy_cookie_db(profile, work)
        except OSError as e:
            return {}, 0, f"复制失败: {e}"
        cookies: dict[str, str] = {}
        expired = 0
        try:
            con = sqlite3.connect(db)
            try:
                cur = con.execute(
                    "SELECT name, value, host, path, expiry, isSecure "
                    "FROM moz_cookies WHERE host LIKE ? ORDER BY path",
                    (f"%{domain}%",),
                )
                now = time.time()
                for name, value, _host, _path, expiry, _secure in cur.fetchall():
                    # Firefox expiry 为秒；异常大的值按毫秒
                    if expiry and expiry > 1e11:
                        expiry //= 1000
                    if expiry and expiry < now:
                        expired += 1
                        continue
                    cookies[name] = value
            finally:
                con.close()
        except sqlite3.Error as e:
            return {}, 0, f"读取失败: {e}"
        return cookies, expired, ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _profile_login_expiry(profile: str, cookie_name: str) -> float:
    work = tempfile.mkdtemp(prefix="bh_ff_cookie_")
    try:
        try:
            db = _copy_cookie_db(profile, work)
        except OSError:
            return -1.0
        try:
            con = sqlite3.connect(db)
            try:
                cur = con.execute(
                    "SELECT MAX(expiry) FROM moz_cookies WHERE name = ?",
                    (cookie_name,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    exp = float(row[0])
                    return exp / 1000 if exp > 1e11 else exp
            finally:
                con.close()
        except sqlite3.Error:
            pass
        return -1.0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def collect_firefox_cookies(domain: str = "budeco.top"):
    """从所有 Firefox profile 里挑一份最新有效的登录 cookie。

    返回 (cookies, message)。优先含 wordpress_logged_in_* 的 profile；
    都含则取该 cookie 过期时间最晚的（通常是最新登录的 profile）。
    """
    dirs = _firefox_profile_dirs()
    if not dirs:
        return {}, "未找到 Firefox 用户配置目录（本机可能没装 Firefox）"
    best: dict[str, str] = {}
    best_exp = -1.0
    scanned = 0
    notes: list[str] = []
    for d in dirs:
        cookies, _expired, err = _read_firefox_profile(d, domain)
        scanned += 1
        if err:
            notes.append(f"{os.path.basename(d)}: {err}")
            continue
        login_names = [k for k in cookies if k.startswith(_LOGIN_COOKIE)]
        if not login_names:
            continue
        exp = _profile_login_expiry(d, login_names[0])
        if exp > best_exp:
            best_exp = exp
            best = cookies
    if best:
        return best, f"已从 Firefox（{scanned} 个 profile）读取到登录 Cookie"
    if notes:
        return {}, "；".join(notes[:2])
    return {}, "Firefox 里没有找到 budeco.top 的有效登录态，请先在 Firefox 登录该站点"


class BudecoAuth(QObject):
    """BudecoTop 登录态：cookie 持久化 + Firefox 同步 + 登录复验 + 下载链解析。"""

    login_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._requests = _requests()
        self._session = None
        self._lock = threading.Lock()
        self._logged_in = False
        self._verified_at = 0.0
        self._verify_interval = 120.0
        if self._requests is not None:
            self._session = self._requests.Session()
            self._session.headers.update({"User-Agent": UA, "Referer": BASE})
        self._load_cookies()

    # -- cookie 持久化 --------------------------------------------------
    def _settings(self):
        from PyQt6.QtCore import QSettings
        return QSettings("ZLStudio", "BlenderHub")

    def _load_cookies(self) -> None:
        if self._session is None:
            return
        s = self._settings()
        raw = s.value("budeco_auth_cookies", "")
        if not raw:
            return
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    self._session.cookies.set(k, v, domain=".budeco.top", path="/")
                self._logged_in = any(k.startswith(_LOGIN_COOKIE) for k in data)
        except (ValueError, TypeError):
            pass

    def _save_cookies(self) -> None:
        if self._session is None:
            return
        data = {c.name: c.value for c in self._session.cookies}
        self._settings().setValue("budeco_auth_cookies",
                                  json.dumps(data, ensure_ascii=False))

    def logout(self) -> None:
        if self._session is not None:
            with self._lock:
                self._session.cookies.clear()
        self._settings().remove("budeco_auth_cookies")
        self._logged_in = False
        self._verified_at = 0.0
        self.login_changed.emit(False)

    def ingest_cookies(self, cookies: dict[str, str]) -> None:
        """把外部 cookie 注入会话并持久化（Firefox 同步 / 手动粘贴都走这里）。"""
        if self._session is None or not cookies:
            return
        # CookieJar 非线程安全：worker 线程可能正持锁用同一 Session 发请求
        with self._lock:
            for k, v in cookies.items():
                self._session.cookies.set(k, v, domain=".budeco.top", path="/")
            self._save_cookies()
        self._logged_in = any(k.startswith(_LOGIN_COOKIE) for k in cookies)
        self._verified_at = 0.0
        self.login_changed.emit(self._logged_in)

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def available(self) -> bool:
        return self._session is not None

    def cookie_text(self) -> str:
        """当前会话 cookie 序列化（供对话框展示/复制）。"""
        if self._session is None:
            return ""
        return "; ".join(f"{c.name}={c.value}" for c in self._session.cookies)

    # -- 登录复验 -------------------------------------------------------
    def verify_login(self, force: bool = False) -> bool:
        """向站点确认 cookie 是否真的有效（带节流，非 force 时 120s 内复用）。"""
        if self._session is None:
            return False
        now = time.time()
        if not force and self._logged_in and \
                now - self._verified_at < self._verify_interval:
            return True
        if not any(c.name.startswith(_LOGIN_COOKIE)
                   for c in self._session.cookies):
            self._logged_in = False
            self.login_changed.emit(False)
            return False
        try:
            with self._lock:
                resp = self._session.get(BASE, timeout=15)
            html = resp.text
        except Exception:
            return self._logged_in  # 网络抖动时不改状态
        ok = not any(m in html for m in _LOGIN_MARKERS)
        if ok != self._logged_in:
            self._logged_in = ok
            self.login_changed.emit(ok)
        self._verified_at = now
        return ok

    def sync_from_firefox(self) -> tuple[bool, str]:
        cookies, msg = collect_firefox_cookies()
        if not cookies:
            return False, msg
        self.ingest_cookies(cookies)
        if self.verify_login(force=True):
            return True, "Firefox 登录态同步成功，已登录 BudecoTop"
        return False, "Firefox 里的登录态已失效，请先在 Firefox 重新登录该站点"

    def import_cookie_text(self, text: str) -> tuple[bool, str]:
        """解析手动粘贴的 cookie 串（k=v; k2=v2 或 JSON）。"""
        text = (text or "").strip()
        if not text:
            return False, "cookie 内容为空"
        cookies: dict[str, str] = {}
        try:
            if text.startswith("{"):
                data = json.loads(text)
                if isinstance(data, dict):
                    cookies = {str(k): str(v) for k, v in data.items()}
        except ValueError:
            pass
        if not cookies:
            for part in text.split(";"):
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                k, v = k.strip(), v.strip()
                if k:
                    cookies[k] = v
        if not cookies:
            return False, "无法解析 cookie 内容"
        self.ingest_cookies(cookies)
        if self.verify_login(force=True):
            return True, "Cookie 导入成功，已登录 BudecoTop"
        return False, "Cookie 已导入但登录验证未通过，请确认复制的是 budeco.top 的登录 Cookie"

    # -- 下载链解析 -----------------------------------------------------
    def _pan_target(self, html: str, page_url: str) -> DownloadTarget:
        code = ""
        m = _RE_CODE.search(html)
        if m:
            code = m.group(1).strip()
        pan_url = ""
        for mm in _RE_HREF_URL.finditer(html):
            u = mm.group(1)
            if any(d in u for d in _PAN_DOMAINS):
                pan_url = u
                if not code:
                    q = parse_qs(urlparse(u).query)
                    code = (q.get("pwd") or [""])[0]
                break
        if not pan_url:
            return DownloadTarget(
                ok=False, page_url=page_url,
                note="文章页未找到可用下载地址（可能需要登录或站点结构变化）")
        return DownloadTarget(ok=True, kind="pan", url=pan_url, code=code,
                              page_url=page_url)

    def resolve_download(self, page_url: str) -> DownloadTarget:
        """解析文章页真实下载链：直链 zip 或网盘分享（链接 + 提取码）。"""
        if self._session is None:
            return DownloadTarget(ok=False, page_url=page_url,
                                  note="requests 不可用，无法解析下载链")
        try:
            with self._lock:
                resp = self._session.get(page_url, timeout=20)
            html = resp.text
        except Exception as e:
            return DownloadTarget(ok=False, page_url=page_url,
                                  note=f"打开文章页失败：{e}")
        if any(m in html for m in _LOGIN_MARKERS):
            return DownloadTarget(ok=False, page_url=page_url,
                                  note="需要登录 BudecoTop 账号才能下载")
        candidates = [urljoin(page_url, h) for h in _RE_DOWNLOAD_HREF.findall(html)]
        candidates += [urljoin(page_url, f"download.php?postid={pid}")
                       for pid in _RE_POSTID.findall(html)]
        seen: set[str] = set()
        tried = 0
        for dl in candidates:
            if dl in seen or tried >= _MAX_DOWNLOAD_CANDIDATES:
                continue
            seen.add(dl)
            tried += 1
            try:
                with self._lock:
                    r = self._session.get(dl, timeout=20, stream=True,
                                          allow_redirects=True)
                    ct = (r.headers.get("Content-Type") or "").lower()
                    final = r.url
                    r.close()
            except Exception:
                continue
            if "zip" in ct or final.lower().split("?")[0].endswith(".zip"):
                return DownloadTarget(ok=True, kind="zip", url=final,
                                      page_url=page_url)
            # HTML 页面：可能是网盘中转页
            try:
                with self._lock:
                    r2 = self._session.get(dl, timeout=20)
                    try:
                        sub = r2.text
                    finally:
                        r2.close()
            except Exception:
                continue
            t = self._pan_target(sub, page_url)
            if t.ok:
                return t
        return self._pan_target(html, page_url)

    def download_direct(self, url: str, dest: str,
                        on_progress=None) -> tuple[bool, str]:
        """直链下载到 dest；on_progress(pct) 允许来自工作线程。"""
        if self._session is None:
            return False, "requests 不可用"
        part = dest + ".part"
        try:
            with self._lock:
                resp = self._session.get(url, timeout=(10, 120), stream=True)
                try:
                    if resp.status_code != 200:
                        return False, f"下载失败：HTTP {resp.status_code}"
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(part, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            if on_progress and total:
                                on_progress(min(99, int(done * 100 / total)))
                    if on_progress:
                        on_progress(100)
                finally:
                    resp.close()
            with open(part, "rb") as f:
                if f.read(2) != b"PK":
                    os.remove(part)
                    return False, "下载内容不是有效的 ZIP 包"
            # 校验通过后再原子落位，中途失败不会留下半截 zip
            os.replace(part, dest)
            return True, dest
        except Exception as e:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
            return False, f"下载失败：{e}"


# ---------------------------------------------------------------- 目录拉取
def _guess_category(name: str, desc: str) -> str:
    text = f"{name} {desc}".lower()
    for cat, kws in _CATEGORY_RULES:
        if any(k.lower() in text for k in kws):
            return cat
    return "社区精选"


def _extract_tags(name: str, desc: str) -> list[str]:
    text = f"{name} {desc}".lower()
    return [t for t, kws in _TAG_RULES if any(k.lower() in text for k in kws)][:4]


@lru_cache(maxsize=8192)
def norm_name(name: str) -> str:
    """插件名归一化：小写、去空白/连接符/版本号，用于本地与在线目录匹配。

    版本号要拆两步处理：
    1) 带点号的版本号（v1.2.3 / 4.2.1）；
    2) 孤立的版本整数（Hard Ops 9 → hardops、Zen UV 4 → zenuv）——
       在线标题常写 "BoxCutter +HardOps" 这类无空格拼法，不去横坐标就匹配不上。
    字母数字粘连的部分必须保留（gltf2 / x3d / pbr → 不能被当成版本号删掉），
    所以第 2 步要求数字两侧都不挨字母数字。
    """
    s = (name or "").lower()
    s = re.sub(r"v?\d+(?:\.\d+)+", "", s)
    s = re.sub(r"(?<![a-z0-9])v?\d+(?![a-z0-9])", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)
    return s


class _FetchTask(QRunnable):
    """后台爬取 budeco.top 目录（不含缩略图，缩略图在 catalog 就绪后补）。"""

    def __init__(self, store: "AddonStore", incremental: int):
        super().__init__()
        self._store = store
        self._incremental = incremental

    def run(self) -> None:
        try:
            ok, msg = self._store._crawl(self._incremental)
        except Exception as e:
            ok, msg = False, f"拉取失败：{e}"
        self._store.fetch_done.emit(ok, msg)
        if ok:
            try:
                self._store._download_thumbs()
                self._store.thumbs_done.emit()
            except Exception as e:
                log.warning("缩略图批量下载失败：%s", e)
        self._store._refreshing = False


class _ThumbTask(QRunnable):
    """按需下载指定插件条目的缩略图（本地匹配命中但未缓存的）。"""

    def __init__(self, store: "AddonStore", addons: list["StoreAddon"]):
        super().__init__()
        self._store = store
        self._addons = addons

    def run(self) -> None:
        try:
            self._store._download_thumbs(self._addons)
        except Exception as e:
            log.warning("按需缩略图下载失败：%s", e)
        # 先复位 busy 再发信号，槽里据此续发排队批次才不会被自己挡住
        self._store._thumb_busy = False
        try:
            self._store.thumbs_done.emit()
        except Exception:
            pass


class AddonStore(QObject):
    """BudecoTop 插件目录服务：爬取 / 缓存 / 搜索 / 缩略图。"""

    fetch_done = pyqtSignal(bool, str)
    thumbs_done = pyqtSignal()

    MIN_REFRESH_INTERVAL = 300
    MAX_PAGES = 80                    # 硬上限防死循环
    PAGE_CONCURRENCY = 4
    MAX_THUMBS = 240
    ONDEMAND_THUMBS = 120             # 单批按需补下载上限（可见即下载）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = cache_root()
        self._catalog_path = os.path.join(self._root, "budeco_catalog.json")
        self._thumbnails_dir = os.path.join(self._root, "thumbnails")
        os.makedirs(self._thumbnails_dir, exist_ok=True)
        self._addons: list[StoreAddon] = []
        self._last_refresh_ts = 0.0
        self._refreshing = False
        self._thumb_busy = False
        self._lock = threading.Lock()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._load_catalog()

    # -- 磁盘缓存 -------------------------------------------------------
    def _load_catalog(self) -> None:
        if not os.path.isfile(self._catalog_path):
            return
        try:
            with open(self._catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = [StoreAddon.from_dict(d) for d in data.get("addons", [])]
            with self._lock:
                self._addons = items
            self._last_refresh_ts = float(data.get("ts") or 0.0)
        except (OSError, ValueError, TypeError) as e:
            log.warning("在线目录缓存读取失败，忽略：%s", e)

    def _save_catalog(self) -> None:
        # 序列化与落盘都在锁内：避免 _FetchTask / _ThumbTask 并发写同一个
        # 固定 .tmp 导致交错截断；唯一临时名 + os.replace 保证原子替换。
        with self._lock:
            items = list(self._addons)
            payload = {"ts": time.time(), "addons": [a.to_dict() for a in items]}
            self._write_atomic(self._catalog_path,
                               json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _write_atomic(path: str, text: str) -> None:
        d = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("写缓存失败 %s：%s", path, e)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # -- 查询 -----------------------------------------------------------
    @property
    def addons(self) -> list[StoreAddon]:
        with self._lock:
            return list(self._addons)

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    @property
    def last_refresh_ts(self) -> float:
        return self._last_refresh_ts

    def get(self, addon_id: str) -> StoreAddon | None:
        for a in self.addons:
            if a.id == addon_id:
                return a
        return None

    def search(self, keyword: str = "", category: str = "全部") -> list[StoreAddon]:
        key = (keyword or "").strip().lower()
        out = []
        for a in self.addons:
            if key and key not in a.name.lower() \
                    and key not in a.description.lower():
                continue
            if category and category != "全部" and a.category != category:
                continue
            out.append(a)
        return out

    def categories(self) -> list[tuple[str, int]]:
        cats: dict[str, int] = {}
        for a in self.addons:
            cats[a.category] = cats.get(a.category, 0) + 1
        return sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))

    def thumb_pixmap_path(self, addon: StoreAddon) -> str:
        if addon.thumb_path and os.path.isfile(addon.thumb_path):
            return addon.thumb_path
        return ""

    def match_local_thumbnail(self, name: str) -> str:
        """本地插件名 → 已缓存的在线缩略图路径；无匹配返回空串。"""
        a = self.find_local(name)
        if a is not None and a.thumb_path and os.path.isfile(a.thumb_path):
            return a.thumb_path
        return ""

    def find_local(self, name: str) -> StoreAddon | None:
        """本地插件名 → 在线目录条目（不要求缩略图已下载）。

        本地插件目录名常带版本 / 作者后缀（如 "Zen UV 4.2.1"），
        按归一化名精确匹配优先，其次取能被包含的最长目录名。
        """
        key = norm_name(name)
        if len(key) < 3:
            return None
        best, best_len = None, 0
        for a in self.addons:
            nk = norm_name(a.name)
            if not nk:
                continue
            if nk == key:
                return a
            if len(nk) >= 4 and len(key) >= 4 and (nk in key or key in nk):
                if len(nk) > best_len:
                    best, best_len = a, len(nk)
        return best

    # -- 后台刷新 -------------------------------------------------------
    def refresh_async(self, incremental: int = 0, force: bool = False) -> bool:
        """后台刷新目录；带节流。返回 False 表示被节流/已在刷新。"""
        if self._refreshing:
            return False
        now = time.time()
        if not force and now - self._last_refresh_ts < self.MIN_REFRESH_INTERVAL:
            return False
        self._refreshing = True
        self._pool.start(_FetchTask(self, incremental))
        return True

    def download_thumbs_async(self, addons: list[StoreAddon]) -> bool:
        """按需后台下载指定条目的缩略图（本地插件匹配到的在线条目）。

        目录可达上千条而全量下载有上限，本地匹配命中的条目可能还没下载过；
        这里补下载，完成后经 thumbs_done 通知页面重贴缩略图。
        """
        if self._thumb_busy or not addons:
            return False
        self._thumb_busy = True
        self._pool.start(_ThumbTask(self, addons[:self.ONDEMAND_THUMBS]))
        return True

    @property
    def thumb_busy(self) -> bool:
        """是否正在跑缩略图下载任务（供调用方做排队，避免任务被丢弃）。"""
        return self._thumb_busy

    # -- 爬取 -----------------------------------------------------------
    def _fetch_page(self, url: str) -> str:
        requests = _requests()
        if requests is None:
            raise RuntimeError("requests 不可用")
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def _parse_card(self, post) -> StoreAddon | None:
        a = StoreAddon()
        img = post.find("img", class_="thumb") or post.find("img")
        if img is not None:
            a.thumbnail_url = img.get("data-src") or img.get("src") or ""
        h3 = post.find("h3")
        title_el = h3.find("a") if h3 is not None else None
        if title_el is None and h3 is not None:
            title_el = h3
        if title_el is None:
            return None
        a.name = title_el.get_text(strip=True)
        href = title_el.get("href", "") if hasattr(title_el, "get") else ""
        a.source_url = urljoin(BASE, href) if href else ""
        m = re.search(r"/(\d+)\.html", a.source_url)
        a.id = f"budeco:{m.group(1)}" if m else \
            f"budeco:{hash(a.source_url) & 0xffffff:x}"
        ex = post.find("div", class_="excerpt")
        a.description = ex.get_text(" ", strip=True)[:300] if ex else ""
        meta = post.find("div", class_="meta")
        if meta is not None:
            au = meta.find("span", class_="author-name")
            a.author = au.get_text(strip=True) if au else "Blender布的"
            a.free = meta.find("span", class_="vip-tag") is None
        else:
            a.author = "Blender布的"
        a.price = "免费" if a.free else "付费/充电"
        a.category = _guess_category(a.name, a.description)
        a.tags = _extract_tags(a.name, a.description)
        a.cached_at = time.time()
        return a

    def _crawl(self, incremental: int = 0) -> tuple[bool, str]:
        """爬取目录（并发翻页到空页或硬上限）；incremental>0 只爬前 N 页。"""
        if _requests() is None or _bs4() is None:
            return False, "requests / beautifulsoup4 未安装，无法拉取插件目录"
        BeautifulSoup = _bs4()
        from concurrent.futures import ThreadPoolExecutor, as_completed

        old_by_id = {a.id: a for a in self.addons}
        found: dict[str, StoreAddon] = {}
        pages_done = 0
        empty_streak = 0
        max_pages = incremental if incremental > 0 else self.MAX_PAGES
        page = 1
        try:
            with ThreadPoolExecutor(max_workers=self.PAGE_CONCURRENCY,
                                    thread_name_prefix="addon-refresh") as ex:
                while page <= max_pages:
                    batch = list(range(page, min(page + self.PAGE_CONCURRENCY,
                                                 max_pages + 1)))
                    futs = {ex.submit(self._fetch_page,
                                      PAGE_URL.format(page=p)): p for p in batch}
                    got_any = False
                    for fut in as_completed(futs):
                        try:
                            html = fut.result()
                        except Exception:
                            continue
                        soup = BeautifulSoup(html, "html.parser")
                        posts = soup.find_all("div", class_="post")
                        if not posts:
                            continue
                        got_any = True
                        for post in posts:
                            try:
                                a = self._parse_card(post)
                            except Exception:
                                continue
                            if a is not None and a.name:
                                found[a.id] = a
                        pages_done += 1
                    if not got_any:
                        empty_streak += 1
                        if empty_streak >= 2 and incremental <= 0:
                            break
                    else:
                        empty_streak = 0
                    page += self.PAGE_CONCURRENCY
        except Exception as e:
            return False, f"拉取失败：{e}"
        if not found:
            return False, "未爬到任何插件（站点结构可能变化或网络不通）"
        # 合并：新条目优先，旧条目保留（本地缩略图路径不丢）
        merged: dict[str, StoreAddon] = {}
        for aid, a in old_by_id.items():
            if aid in found:
                na = found[aid]
                na.thumb_path = a.thumb_path or na.thumb_path
                merged[aid] = na
            else:
                merged[aid] = a
        for aid, a in found.items():
            if aid not in merged:
                merged[aid] = a
        with self._lock:
            self._addons = sorted(merged.values(),
                                  key=lambda x: x.cached_at, reverse=True)
        self._last_refresh_ts = time.time()
        self._save_catalog()
        return True, (f"已拉取 {len(found)} 个插件（本次扫描 {pages_done} 页，"
                      f"库内共 {len(merged)} 个）")

    def _download_thumbs(self, only: list[StoreAddon] | None = None) -> None:
        requests = _requests()
        if requests is None:
            return
        if only is None:
            todo = [a for a in self.addons
                    if a.thumbnail_url and not a.thumb_path][:self.MAX_THUMBS]
        else:
            todo = [a for a in only
                    if a.thumbnail_url
                    and not (a.thumb_path and os.path.isfile(a.thumb_path))
                    ][:self.MAX_THUMBS]
        if not todo:
            return
        from concurrent.futures import ThreadPoolExecutor

        def one(a: StoreAddon) -> None:
            try:
                name = hashlib.md5(
                    a.thumbnail_url.encode("utf-8")).hexdigest() + ".png"
                path = os.path.join(self._thumbnails_dir, name)
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    a.thumb_path = path
                    return
                resp = requests.get(a.thumbnail_url,
                                    headers={"User-Agent": UA, "Referer": BASE},
                                    timeout=15, stream=True)
                part = path + ".part"
                try:
                    if resp.status_code != 200:
                        return
                    with open(part, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 15):
                            if chunk:
                                f.write(chunk)
                    # 写临时文件再原子替换，中断不会留下被误判为已缓存的半截图
                    if os.path.getsize(part) > 0:
                        os.replace(part, path)
                        a.thumb_path = path
                    else:
                        os.remove(part)
                except Exception:
                    if os.path.exists(part):
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                    raise
                finally:
                    resp.close()
            except Exception:
                pass

        try:
            with ThreadPoolExecutor(max_workers=4,
                                    thread_name_prefix="addon-thumb") as ex:
                list(ex.map(one, todo))
        except Exception as e:
            log.warning("缩略图批量下载失败：%s", e)
        self._save_catalog()


# ---------------------------------------------------------------- 守望
def default_watch_dirs() -> list[str]:
    """常见下载目录（按优先级）：用户 Downloads → 桌面。"""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, "Downloads"), os.path.join(home, "Desktop")]
    one = os.path.join(home, "OneDrive")
    if os.path.isdir(one):
        dirs += [os.path.join(one, "Downloads"), os.path.join(one, "Desktop")]
    return [d for d in dirs if os.path.isdir(d)]


class DownloadWatcher(QObject):
    """轮询下载目录，命中「新写完的 zip」时发出 found(path)。

    判定规则（缺一不可）：① .zip 后缀；② mtime 不早于开始等待的时刻；
    ③ 大小 ≥ min_size_kb；④ 连续 stable_ms 内大小不变（已写完）。
    """

    found = pyqtSignal(str)
    tick = pyqtSignal(int)      # 已等待秒数
    timeout = pyqtSignal()

    def __init__(self, dirs: list[str] | None = None, min_size_kb: int = 10,
                 stable_ms: int = 1500, timeout_s: int = 900,
                 interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self._dirs = dirs if dirs is not None else default_watch_dirs()
        self._min_size = min_size_kb * 1024
        self._stable_s = stable_ms / 1000.0
        self._timeout_s = timeout_s
        self._since = 0.0
        self._started_at = 0.0
        self._sizes: dict[str, tuple[int, float]] = {}
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

    def set_dirs(self, dirs: list[str]) -> None:
        self._dirs = list(dirs)
        self._sizes.clear()

    def start(self) -> None:
        if self._running:
            return
        self._since = time.time()
        self._started_at = self._since
        self._sizes.clear()
        self._running = True
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._running

    def _poll(self) -> None:
        if not self._running:
            return
        now = time.time()
        self.tick.emit(int(now - self._started_at))
        if now - self._started_at > self._timeout_s:
            self.stop()
            self.timeout.emit()
            return
        hits: list[str] = []
        for d in self._dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not n.lower().endswith(".zip"):
                    continue
                p = os.path.join(d, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < self._since or st.st_size < self._min_size:
                    continue
                prev = self._sizes.get(p)
                if prev is None or prev[0] != st.st_size:
                    # 首次见到或大小变化：重新记录，稳定计时从此刻算起
                    self._sizes[p] = (st.st_size, now)
                elif now - prev[1] >= self._stable_s:
                    hits.append(p)
        if hits:
            self.stop()
            self.found.emit(hits[0])


# ---------------------------------------------------------------- 安装落位
def blender_user_versions() -> list[str]:
    """本机 Blender 用户配置下的版本目录（新→旧）。"""
    base = os.path.join(os.environ.get("APPDATA", ""),
                        "Blender Foundation", "Blender")
    if not os.path.isdir(base):
        return []
    vers = [n for n in os.listdir(base) if re.fullmatch(r"\d+\.\d+", n)]
    return sorted(vers, key=lambda v: tuple(int(x) for x in v.split(".")),
                  reverse=True)


def install_zip(path: str, version: str | None = None
                ) -> tuple[bool, str, str]:
    """把插件 zip 安装进目标 Blender 版本插件目录。

    返回 (ok, message, name)。version=None 时用最新版本目录。
    """
    from . import detect

    versions = blender_user_versions()
    if version:
        versions = [version] + [v for v in versions if v != version]
    if not versions:
        return False, "未检测到 Blender 用户配置目录，请先运行一次 Blender", ""
    ver = versions[0]
    appdata = os.environ.get("APPDATA", "")
    target_root = os.path.join(appdata, "Blender Foundation", "Blender",
                               ver, "extensions", "user_default")
    if not os.path.isdir(target_root):
        target_root = os.path.join(appdata, "Blender Foundation", "Blender",
                                   ver, "scripts", "addons")
    tmp = tempfile.mkdtemp(prefix="blenderhub_zip_")
    try:
        try:
            os.makedirs(target_root, exist_ok=True)
        except OSError as e:
            return False, f"创建插件目录失败：{e}", ""
        ok, msg = detect.extract_archive(path, tmp)
        if not ok:
            return False, msg, ""
        entries = [e for e in os.listdir(tmp)
                   if not e.startswith(("__MACOSX", "."))]
        src, name = tmp, os.path.splitext(os.path.basename(path))[0]
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
            inner = os.path.join(tmp, entries[0])
            if any(os.path.exists(os.path.join(inner, f))
                   for f in ("__init__.py", "blender_manifest.toml")):
                src, name = inner, entries[0]
        dest = os.path.join(target_root, name)
        if os.path.exists(dest):
            return False, f"已存在同名插件目录：{name}", name
        shutil.move(src, dest)
    except (OSError, shutil.Error) as e:
        return False, f"安装失败：{e}", ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True, f"已安装 {name} 到 Blender {ver} 插件目录", name


# ---------------------------------------------------------------- 后台任务
class ResolveTask(QRunnable):
    """后台解析下载链，结果经 bridge.payload 回传 DownloadTarget。"""

    def __init__(self, auth: BudecoAuth, page_url: str, bridge: NetBridge):
        super().__init__()
        self._auth = auth
        self._url = page_url
        self._bridge = bridge

    def run(self) -> None:
        try:
            target = self._auth.resolve_download(self._url)
        except Exception as e:
            target = DownloadTarget(ok=False, page_url=self._url, note=str(e))
        self._bridge.payload.emit(target)


class VerifyLoginTask(QRunnable):
    """后台复验登录态（verify_login 带网络 IO，不能放 GUI 线程）。"""

    def __init__(self, auth: BudecoAuth, bridge: NetBridge):
        super().__init__()
        self._auth = auth
        self._bridge = bridge

    def run(self) -> None:
        try:
            ok = self._auth.verify_login(force=True)
        except Exception:
            ok = self._auth.logged_in
        self._bridge.payload.emit(ok)


class DirectDownloadTask(QRunnable):
    """后台直链下载 + 进度回报。"""

    def __init__(self, auth: BudecoAuth, url: str, dest: str,
                 bridge: NetBridge):
        super().__init__()
        self._auth = auth
        self._url = url
        self._dest = dest
        self._bridge = bridge

    def run(self) -> None:
        ok, msg = self._auth.download_direct(
            self._url, self._dest,
            on_progress=lambda pct: self._bridge.progress.emit(pct))
        self._bridge.finished.emit(ok, msg)


class VerifyZipTask(QRunnable):
    """后台校验本地 zip，结果经 bridge.payload 回传 VerifyResult。"""

    def __init__(self, verifier: AddonVerifier, zip_path: str,
                 bridge: NetBridge):
        super().__init__()
        self._verifier = verifier
        self._path = zip_path
        self._bridge = bridge

    def run(self) -> None:
        try:
            result = self._verifier.verify_file(self._path)
        except Exception as e:
            result = VerifyResult(ok=False, errors=[f"校验出错：{e}"])
        self._bridge.payload.emit(result)


class InstallZipTask(QRunnable):
    """后台安装（校验已通过）并写审计日志。"""

    def __init__(self, verifier: AddonVerifier, zip_path: str,
                 addon: StoreAddon, result: VerifyResult,
                 bridge: NetBridge):
        super().__init__()
        self._verifier = verifier
        self._path = zip_path
        self._addon = addon
        self._result = result
        self._bridge = bridge

    def run(self) -> None:
        try:
            ok, msg, name = install_zip(self._path)
            if ok:
                self._verifier.record_audit(
                    self._addon.id, name or self._addon.name,
                    self._addon.source_url, self._result)
            self._bridge.finished.emit(ok, msg)
        except Exception as e:
            self._bridge.finished.emit(False, f"安装过程出错：{e}")
