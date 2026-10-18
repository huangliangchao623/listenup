"""Media acquisition: local files, yt-dlp (Bilibili / many sites), and the
Bilibili caption API.

Credentials policy
------------------
This module never ships or stores an account for the user.  A Bilibili caption
lookup needs the *user's own* logged-in session, so the SESSDATA value is read
from the `LISTENUP_BILI_SESSDATA` environment variable (or a cookies file the
user points at).  When it is absent, or the API returns nothing, we fall back to
local ASR - which always works and needs no credentials.

The API shape below (endpoints and field names) is deliberately kept minimal:
only what is needed to fetch a subtitle track.  Do not expand this into a
published API reference.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import config as C

BILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
BILI_PLAYER_API = "https://api.bilibili.com/x/player/v2"
BILI_BVID = re.compile(r"(BV[0-9A-Za-z]{10})")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class SourceError(RuntimeError):
    pass


@dataclass
class FetchResult:
    """Everything the pipeline needs after the acquisition step."""
    kind: str                       # "local" | "url"
    source_url: str | None = None
    platform: str | None = None
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    media_path: Path | None = None  # audio or video file
    caption_path: Path | None = None
    caption_lang: str | None = None
    caption_kind: str | None = None  # "manual" | "auto" | "ai"
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# platform detection
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "bilibili.com" in host or host in ("b23.tv", "bili2233.cn"):
        return "bilibili"
    if "youtube.com" in host or host == "youtu.be":
        return "youtube"
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "douyin"
    if "tiktok.com" in host:
        return "tiktok"
    return "generic"


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------

def _ytdlp_cmd(*args: str) -> list[str]:
    """Run yt-dlp as a Python module.

    The PyInstaller single-file build cannot start in this sandbox (it unpacks
    to %TEMP%, which is denied), but the Python package works fine, so we invoke
    the module directly.
    """
    return [sys.executable, "-m", "yt_dlp", *args]


def _ffmpeg_args() -> list[str]:
    """Point yt-dlp at our bundled ffmpeg.

    Without this, `-x/--audio-format` postprocessing fails with "ffprobe and
    ffmpeg not found" - the binaries are in tools/bin, which is not on PATH.
    """
    if C.FFMPEG.exists():
        return ["--ffmpeg-location", str(C.BIN_DIR)]
    return []


def _network_args() -> list[str]:
    """Anti-flakiness: Bilibili CDN connections drop mid-transfer regularly."""
    return [
        "--retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "exp=1:20",
        "--socket-timeout", "30",
        "--continue",
    ]


def cookies_file_path() -> Path:
    """Where the user's exported cookies file lives.

    Douyin (and a few other sites) refuse requests without a session.  Reading
    cookies straight out of a running browser is fragile - the browser locks
    its cookie database while open, which surfaces as a confusing
    "Could not copy Chrome cookie database" error.  An exported Netscape cookie
    file is stable and only has to be produced once.
    """
    env = os.environ.get("LISTENUP_COOKIES_FILE")
    if env:
        return Path(env)
    return C.DATA_DIR / "cookies.txt"


def cookies_available() -> tuple[bool, str]:
    """Is a usable cookie source configured?  Returns (ok, human description)."""
    path = cookies_file_path()
    if path.exists() and path.stat().st_size > 0:
        return True, f"cookies 文件：{path}"
    browser = os.environ.get("LISTENUP_COOKIES_FROM_BROWSER")
    if browser:
        return True, f"从浏览器读取：{browser}（需先完全关闭该浏览器）"
    return False, (f"未配置。可把 cookies 导出到 {path}，"
                   f"或设置 LISTENUP_COOKIES_FROM_BROWSER=edge")


def _cookies_args() -> list[str]:
    path = cookies_file_path()
    if path.exists() and path.stat().st_size > 0:
        return ["--cookies", str(path)]
    from_browser = os.environ.get("LISTENUP_COOKIES_FROM_BROWSER")
    if from_browser:
        return ["--cookies-from-browser", from_browser]
    return []


# Platforms whose pages carry no usable caption track at all, so there is no
# point running the subtitle downloader for them.
NO_CAPTION_PLATFORMS = {"douyin", "tiktok"}


def needs_cookies(platform: str) -> bool:
    """Sites that reject anonymous requests outright."""
    return platform in {"douyin", "tiktok"}


def ytdlp_probe(url: str) -> dict:
    """Fetch metadata only (no download)."""
    r = subprocess.run(
        _ytdlp_cmd("--dump-single-json", "--no-warnings", "--no-playlist",
                   *_network_args(), *_cookies_args(), url),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        env=_subprocess_env(),
    )
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        raise SourceError(_friendly_error(err, url))
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise SourceError(f"yt-dlp 输出无法解析：{e}") from e


def _friendly_error(err: str, url: str) -> str:
    """Turn yt-dlp's terse failures into something actionable."""
    low = err.lower()
    platform = detect_platform(url)

    if "fresh cookies" in low or "cookies are needed" in low:
        ok, desc = cookies_available()
        state = f"当前状态：{desc}" if not ok else f"已配置但可能过期（{desc}）"
        return (f"这个平台需要登录信息才能解析。{state}\n"
                f"解决办法见下方「让抖音能用」说明。")
    if "could not copy chrome cookie database" in low:
        return ("浏览器正在运行，cookie 数据库被锁住了。\n"
                "请完全退出 Edge（包括后台进程），或改用导出的 cookies 文件。")
    if "unsupported url" in low:
        return (f"yt-dlp 不认识这个链接。\n"
                f"抖音请用视频页地址（www.douyin.com/video/数字ID），"
                f"不要用已经失效的分享短链。")
    return f"yt-dlp 无法解析该链接：{err[-400:] or '（无错误输出）'}"


def list_subtitles(url: str) -> dict:
    """Return {'manual': {...lang: ext}, 'auto': {...}} without downloading."""
    info = ytdlp_probe(url)
    return {
        "manual": info.get("subtitles") or {},
        "auto": info.get("automatic_captions") or {},
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": float(info.get("duration") or 0.0),
        "id": info.get("id") or "",
    }


def download_media(url: str, out_dir: Path, audio_only: bool = True,
                   progress=None) -> Path:
    """Download the media.  Audio-only by default: a listening trainer does not
    need the video track, and it saves an order of magnitude of bandwidth."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(id)s.%(ext)s")
    args = [
        "--no-playlist", "--no-warnings", "--newline",
        "-o", tmpl,
        *_network_args(),
        *_cookies_args(),
        *_ffmpeg_args(),
    ]
    if audio_only:
        # Prefer the best muxed audio, then fall back.  Bilibili exposes
        # 30280/30232/30216 as audio-only DASH streams; yt-dlp picks the best
        # automatically, and higher bitrate measurably helps ASR accuracy.
        args += ["-f", "bestaudio/best", "-x", "--audio-format", "wav",
                 "--audio-quality", "0"]
    else:
        args += ["-f", "bv*+ba/b"]
    args.append(url)

    # stderr MUST be merged into stdout.  With separate pipes the stderr buffer
    # fills up (yt-dlp is chatty), nobody drains it, and the child blocks
    # forever - a classic pipe deadlock that hangs the whole import.
    proc = subprocess.Popen(
        _ytdlp_cmd(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=_subprocess_env(),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        if progress and "%" in line:
            m = re.search(r"(\d+(?:\.\d+)?)%", line)
            if m:
                progress(min(0.95, float(m.group(1)) / 100.0), "下载中…")
    proc.wait()
    if proc.returncode != 0:
        raise SourceError("yt-dlp 下载失败：\n" + "".join(tail[-12:]).strip())

    files = sorted(out_dir.glob("*.wav")) or sorted(out_dir.glob("*"))
    files = [f for f in files if f.suffix.lower() in
             (".wav", ".m4a", ".mp3", ".webm", ".mp4", ".mkv", ".flac", ".opus")]
    if not files:
        raise SourceError("yt-dlp 没有产出媒体文件")
    return max(files, key=lambda p: p.stat().st_size)


def download_subtitles(url: str, out_dir: Path, langs: str = "en.*,en") -> list[Path]:
    """Try to fetch uploader subtitles, then auto-generated ones.

    Returns whatever .srt/.vtt files ended up on disk.  An empty list is a
    normal outcome - the caller falls back to ASR.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(out_dir.glob("*.vtt")) | set(out_dir.glob("*.srt"))

    for flag in ("--write-subs", "--write-auto-subs"):
        subprocess.run(
            _ytdlp_cmd(
                "--no-playlist", "--no-warnings", "--skip-download",
                flag, "--sub-langs", langs,
                "--convert-subs", "srt",
                "-o", str(out_dir / "%(id)s.%(ext)s"),
                *_network_args(), *_cookies_args(), *_ffmpeg_args(), url,
            ),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
            env=_subprocess_env(),
        )
        after = set(out_dir.glob("*.vtt")) | set(out_dir.glob("*.srt"))
        new = sorted(after - before)
        if new:
            return new
    return []


def _subprocess_env() -> dict:
    env = dict(os.environ)
    env.setdefault("TEMP", str(C.TOOLS / "tmp"))
    env.setdefault("TMP", str(C.TOOLS / "tmp"))
    env["PYTHONPATH"] = str(C.PYDEPS) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
# Bilibili captions
# ---------------------------------------------------------------------------

def _bili_cookie() -> str | None:
    sess = os.environ.get("LISTENUP_BILI_SESSDATA")
    if sess:
        return f"SESSDATA={sess}"
    return None


def bilibili_subtitles(url: str) -> list[dict]:
    """List available subtitle tracks for a Bilibili video.

    Requires the user's own SESSDATA: without login the platform returns an
    empty subtitle list.  Returns [] on any failure - the caller falls back to
    ASR, so this is an optimisation, never a hard dependency.
    """
    import urllib.request

    cookie = _bili_cookie()
    if not cookie:
        return []

    m = BILI_BVID.search(url)
    if not m:
        return []
    bvid = m.group(1)

    def get(api: str, params: dict) -> dict:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        req = urllib.request.Request(
            f"{api}?{q}",
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com",
                     "Cookie": cookie},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    try:
        view = get(BILI_VIEW_API, {"bvid": bvid})
        if view.get("code") != 0:
            return []
        data = view.get("data") or {}
        pages = data.get("pages") or []
        cid = (pages[0].get("cid") if pages else None) or data.get("cid")
        if not cid:
            return []
        player = get(BILI_PLAYER_API, {"bvid": bvid, "cid": cid})
        if player.get("code") != 0:
            return []
        subs = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        return [s for s in subs if s.get("subtitle_url")]
    except Exception:
        return []


def pick_english_track(tracks: list[dict]) -> dict | None:
    """Prefer a real English CC track over a machine-translated one."""
    if not tracks:
        return None
    def score(t: dict) -> int:
        lan = (t.get("lan") or "").lower()
        doc = (t.get("lan_doc") or "")
        s = 0
        if lan.startswith("en"):
            s += 100
        if "英语" in doc or "英文" in doc:
            s += 50
        if lan.startswith("ai-"):
            s -= 20          # machine-generated: lower confidence
        return s
    best = max(tracks, key=score)
    return best if score(best) > 0 else None


def download_bilibili_subtitle(track: dict, dst: Path) -> Path:
    import urllib.request

    url = track["subtitle_url"]
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        body = r.read()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(body)
    return dst
