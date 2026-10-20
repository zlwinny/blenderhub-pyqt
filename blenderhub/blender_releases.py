"""在线版本目录与下载 —— 抓镜像站的 Blender 官方便携版 zip，供「添加版本」安装。

与第一版的两处改进：
1. 镜像的目录列表本身带体积与日期，照实显示（第一版硬编码「约 350 MB」、日期留空）；
2. 下载完按同目录的 blender-<版本>.sha256 清单核对哈希（第一版完全没有完整性校验）。
   哈希与安装包同源，只能防截断 / 传输损坏 / 镜像缓存脏数据，不防镜像被投毒。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .addon_store import cache_root

log = logging.getLogger(__name__)

# 顺序即优先级：官方源偶发 Cloudflare 人机验证，镜像站是纯目录列表
MIRRORS: list[tuple[str, str]] = [
    ("南京大学镜像", "https://mirror.nju.edu.cn/blender/release/"),
    ("清华大学镜像", "https://mirrors.tuna.tsinghua.edu.cn/blender/release/"),
    ("Blender 官方", "https://download.blender.org/release/"),
]

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BlenderHub/1.0"}
_TIMEOUT = (10, 30)
# 大文件流式下载单独收紧读超时：读超时也是 socket 静默超时，
# 30s 意味着链路卡住时「取消」最坏要 30s 才生效
_DL_TIMEOUT = (10, 12)
_CHUNK = 1 << 16
_MAX_RETRIES = 3
_MINORS_KEPT = 8          # 目录里历史版本上百个，列表只取最近的若干小版本
_CACHE_TTL = 6 * 3600
_REUSE_MIN_BYTES = 20 << 20

_LTS_MINORS = frozenset({"2.83", "2.93", "3.3", "3.6", "3.10", "4.2", "4.5", "5.2"})

_MINOR_RE = re.compile(r"Blender(\d+\.\d+)/")
_ZIP_RE = re.compile(r"blender-(\d+\.\d+\.\d+)-windows-x64\.zip$")
# nginx autoindex：<a href="X">X</a></td><td class="size">381.4 MiB</td><td class="date">…</td>
_NGINX_ROW = re.compile(
    r'<a href="(?P<name>[^"?/][^"]*)"[^>]*>[^<]*</a></td>'
    r'<td class="size">(?P<size>[^<]*)</td>'
    r'<td class="date">(?P<date>[^<]*)</td>')
# apache autoindex：<a href="X">X</a>  15-Jul-2025 08:40  376170648
_APACHE_ROW = re.compile(
    r'<a href="(?P<name>[^"?/][^"]*)">[^<]*</a>\s*'
    r'(?P<date>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})\s*(?P<size>\d+)')
_UNIT = {"": 1, "B": 1, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3,
         "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3}


@dataclass
class Release:
    version: str          # "4.5.14"
    minor: str            # "4.5"
    url: str
    mirror: str           # 镜像显示名
    size_bytes: int = 0
    date: str = ""
    is_lts: bool = False

    @property
    def file_name(self) -> str:
        return os.path.basename(self.url)


def ver_key(ver: str) -> tuple[int, ...]:
    # 字符串比较会让 4.5.14 排在 4.5.9 之前（第一版踩过这个坑）
    return tuple(int(x) for x in re.findall(r"\d+", ver)) or (0,)


def _to_bytes(text: str) -> int:
    m = re.fullmatch(r"([\d.]+)\s*([A-Za-z]{0,3})", (text or "").strip())
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _UNIT.get(m.group(2).upper(), 1))
    except ValueError:
        return 0


def _get(url: str, stream: bool = False, headers: dict | None = None):
    import requests

    r = requests.get(url, headers={**_UA, **(headers or {})},
                     timeout=_TIMEOUT, stream=stream)
    # 必须 raise：第一版漏了这步，404 的人机验证页被当成 zip 写进了磁盘
    r.raise_for_status()
    return r


def _parse_rows(html: str) -> dict[str, tuple[int, str]]:
    """目录页 → {文件名: (体积, 日期)}；两种 autoindex 排版都支持。"""
    out: dict[str, tuple[int, str]] = {}
    for rx in (_NGINX_ROW, _APACHE_ROW):
        for m in rx.finditer(html):
            out[m.group("name")] = (_to_bytes(m.group("size")),
                                     m.group("date").strip())
        if out:
            break
    return out


def fetch_releases(mirror: str) -> list[Release]:
    """逐小版本取最新 patch：一次索引请求 + 每个小版本一次目录请求。"""
    minors = sorted(set(_MINOR_RE.findall(_get(mirror).text)),
                    key=ver_key)[-_MINORS_KEPT:]
    out: list[Release] = []
    name = next((n for n, u in MIRRORS if u == mirror), mirror)
    for minor in sorted(minors, key=ver_key, reverse=True):
        try:
            rows = _parse_rows(_get(f"{mirror}Blender{minor}/").text)
        except Exception as e:  # noqa: BLE001 - 单个小版本失败不影响整张列表
            log.warning("读取 Blender%s 目录失败：%s", minor, e)
            continue
        best: tuple[str, int, str] | None = None
        for fname, (size, date) in rows.items():
            m = _ZIP_RE.search(fname)
            if not m:
                continue
            v = m.group(1)
            if best is None or ver_key(v) > ver_key(best[0]):
                best = (v, size, date)
        if best:
            out.append(Release(
                version=best[0], minor=minor,
                url=f"{mirror}Blender{minor}/blender-{best[0]}-windows-x64.zip",
                mirror=name, size_bytes=best[1], date=best[2],
                is_lts=minor in _LTS_MINORS))
    return out


# ---------------------------------------------------------------- 列表缓存
def _cache_file(mirror: str) -> str:
    from urllib.parse import urlparse

    slug = re.sub(r"[^a-z0-9]+", "_", urlparse(mirror).netloc.lower()).strip("_")
    return os.path.join(cache_root(), f"blender_releases_{slug or 'default'}.json")


def _load_cache(path: str) -> list[Release]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [Release(**d) for d in data.get("releases", [])]
    except (OSError, ValueError, TypeError):
        return []


def _fresh(path: str) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < _CACHE_TTL
    except OSError:
        return False


def available_versions(mirror: str = "", force: bool = False
                       ) -> tuple[list[Release], str]:
    """返回 (版本列表, 错误信息)。网络失败时回落到旧缓存（哪怕已过期）。"""
    from .addon_store import has_net_deps

    if not has_net_deps():
        return _load_cache(_cache_file(mirror or MIRRORS[0][1])), \
            "缺少 requests / beautifulsoup4 依赖，无法获取在线版本（pip install requests）"
    mirror = mirror or MIRRORS[0][1]
    path = _cache_file(mirror)
    if not force and _fresh(path):
        cached = _load_cache(path)
        if cached:
            return cached, ""
    try:
        rels = fetch_releases(mirror)
    except Exception as e:  # noqa: BLE001 - 断网 / 镜像挂掉都要能继续用
        stale = _load_cache(path)
        note = "（显示的是上次缓存）" if stale else "（无缓存，可改用其它镜像或本地导入）"
        return stale, f"获取在线版本失败{note}：{e}"
    if not rels:
        return _load_cache(path), "镜像目录里没有解析到 windows-x64 安装包"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mirror": mirror,
                       "releases": [asdict(r) for r in rels]}, f,
                      ensure_ascii=False, indent=1)
    except OSError as e:
        log.warning("版本列表缓存写入失败：%s", e)
    return rels, ""


def _pick_sha(text: str, file_name: str) -> str:
    """从 sha256 清单（"<hash>  <file>" 每行一条）里取指定文件的哈希。"""
    for line in text.splitlines():
        cols = line.split(None, 1)
        if len(cols) == 2 and cols[1].strip() == file_name:
            return cols[0].strip().lower()
    return ""


def sha256_of(rel: Release) -> str:
    """取该版本 sha256 清单里对应 zip 的哈希；拿不到返回空串（跳过校验）。"""
    base, _, _ = rel.url.rpartition("blender-")
    url = f"{base}blender-{rel.version}.sha256"
    try:
        text = _get(url).text
    except Exception as e:  # noqa: BLE001
        log.warning("读取 %s 校验清单失败：%s", rel.version, e)
        return ""
    return _pick_sha(text, rel.file_name)


# ---------------------------------------------------------------- 下载
class _Cancelled(Exception):
    pass


class ReleaseIndex(QObject):
    """列表拉取放在后台：镜像要 8~9 个请求，站在 UI 线程上会卡住窗口。"""

    loaded = pyqtSignal(list, str)

    def refresh(self, mirror: str = "", force: bool = False) -> None:
        from PyQt6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(_IndexTask(self, mirror, force))

    def _run(self, mirror: str, force: bool) -> None:
        try:
            rels, err = available_versions(mirror, force)
        except Exception as e:  # noqa: BLE001 - 槽里的未捕获异常会 abort 进程
            rels, err = [], f"获取在线版本失败：{e}"
        self.loaded.emit(rels, err)


class _IndexTask(QRunnable):
    def __init__(self, owner: ReleaseIndex, mirror: str, force: bool):
        super().__init__()
        self._owner, self._mirror, self._force = owner, mirror, force

    def run(self) -> None:
        self._owner._run(self._mirror, self._force)


def downloads_dir() -> str:
    d = os.path.join(cache_root(), "downloads")
    os.makedirs(d, exist_ok=True)
    return d


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _open_stream(url: str, have: int):
    """返回 (响应, 已有字节, 总字节, 是否续写)。服务器不理 Range 就从头下。"""
    import requests

    if have:
        r = requests.get(url, headers={**_UA, "Range": f"bytes={have}-"},
                         timeout=_DL_TIMEOUT, stream=True)
        r.raise_for_status()
        if r.status_code == 206:
            # 206 的 Content-Length 是「剩余」字节，当总长用进度会算反
            return r, have, have + int(r.headers.get("Content-Length") or 0), True
        r.close()
    r = requests.get(url, headers=_UA, timeout=_DL_TIMEOUT, stream=True)
    r.raise_for_status()
    return r, 0, int(r.headers.get("Content-Length") or 0), False


def _stream_to(url: str, part: str, total_hint: int, cancel: threading.Event,
               report) -> None:
    """边下边写 .part，断点续传；report(已下字节, 总字节) 用于进度。"""
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    r, done, total, resume = _open_stream(url, have)
    total = total or total_hint
    if total and done > total:          # .part 比目标还大：是别次的残留
        done, resume = 0, False
    got = done
    shown, last = -1, 0.0
    with open(part, "ab" if resume else "wb") as f:
        for chunk in r.iter_content(_CHUNK):
            if cancel.is_set():
                raise _Cancelled()
            if not chunk:
                continue
            f.write(chunk)
            got += len(chunk)
            now = time.monotonic()
            pct = int(got * 100 / total) if total else 0
            # 380MB / 64KB ≈ 6000 次回调，全抛进 Qt 队列会淹掉界面：
            # 只在百分比变化或 200ms 心跳时上报
            if pct != shown or now - last > 0.2:
                shown, last = pct, now
                report(got, total)
    if total and got < total:
        raise OSError(f"下载不完整（{got}/{total} 字节）")


class ReleaseDownloader(QObject):
    """一个下载任务：进度百分比 + 文案，结束时给出 zip 路径。"""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, rel: Release, verify: bool = True) -> None:
        from PyQt6.QtCore import QThreadPool

        self._cancel.clear()
        QThreadPool.globalInstance().start(
            _DownloadTask(self, rel, verify))

    def _run(self, rel: Release, verify: bool) -> None:
        dest = os.path.join(downloads_dir(), rel.file_name)
        part = dest + ".part"
        want = sha256_of(rel) if verify else ""
        try:
            if (os.path.isfile(dest)
                    and os.path.getsize(dest) >= _REUSE_MIN_BYTES):
                if not want or _sha256_file(dest) == want:
                    self.progress.emit(100, "复用已下载的安装包")
                    self.finished.emit(True, "已复用本机下载好的安装包", dest)
                    return
                os.remove(dest)         # 旧包校验不过：删掉重下
            last_err = ""
            for attempt in range(1, _MAX_RETRIES + 1):
                if self._cancel.is_set():
                    self.finished.emit(False, "已取消下载", "")
                    return
                try:
                    _stream_to(
                        rel.url, part, rel.size_bytes, self._cancel,
                        lambda got, total: self.progress.emit(
                            min(99, int(got * 100 / total)) if total else 0,
                            f"{got / 1048576:.0f} / {total / 1048576:.0f} MB"))
                    last_err = ""
                    break
                except _Cancelled:
                    self.finished.emit(False, "已取消下载", "")
                    return
                except Exception as e:  # noqa: BLE001 - 网络抖动要重试
                    last_err = str(e) or type(e).__name__
                    if attempt >= _MAX_RETRIES:
                        break
                    self.progress.emit(
                        0, f"第 {attempt} 次失败：{last_err[:40]}，重试中…")
                    time.sleep(2 ** (attempt - 1))
            if last_err:
                self.finished.emit(False, f"下载失败：{last_err}", "")
                return
            if rel.size_bytes and os.path.getsize(part) != rel.size_bytes:
                self.finished.emit(
                    False,
                    f"体积不符（{os.path.getsize(part)} ≠ {rel.size_bytes}）", "")
                return
            if want and _sha256_file(part) != want:
                os.remove(part)
                self.finished.emit(False, "SHA-256 校验不通过，已丢弃该文件", "")
                return
            os.replace(part, dest)
            self.finished.emit(True, f"下载完成：{rel.file_name}", dest)
        except OSError as e:
            self.finished.emit(False, f"写入失败：{e}", "")


class _DownloadTask(QRunnable):
    def __init__(self, owner: ReleaseDownloader, rel: Release, verify: bool):
        super().__init__()
        self._owner, self._rel, self._verify = owner, rel, verify

    def run(self) -> None:
        try:
            self._owner._run(self._rel, self._verify)
        except Exception as e:  # noqa: BLE001
            # PyQt6 槽里未捕获的异常会直接 abort 进程，这里必须兜住
            self._owner.finished.emit(False, f"下载出错：{e}", "")


class BlenderInstaller(QObject):
    """把下载好的 zip 解包进安装目录（380MB 解包要几十秒，不能占着主线程）。"""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)

    def start(self, zip_path: str, dest_root: str) -> None:
        from PyQt6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(_InstallTask(self, zip_path, dest_root))

    def _run(self, zip_path: str, dest_root: str) -> None:
        from . import detect

        self.progress.emit("正在解包…")
        try:
            ok, msg = detect.extract_archive(zip_path, dest_root)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"解包出错：{e}"
        self.finished.emit(ok, msg, dest_root if ok else "")


class _InstallTask(QRunnable):
    def __init__(self, owner: BlenderInstaller, zip_path: str, dest_root: str):
        super().__init__()
        self._owner, self._zip, self._dest = owner, zip_path, dest_root

    def run(self) -> None:
        try:
            self._owner._run(self._zip, self._dest)
        except Exception as e:  # noqa: BLE001
            self._owner.finished.emit(False, f"解包出错：{e}", "")
