"""FastAPI application: serves the UI plus the JSON/SSE API.

One process serves both the desktop browser (localhost) and phones on the same
Wi-Fi (the machine's LAN address), which is the whole point of route A - one
codebase, both form factors, with ffmpeg and the ASR model staying on the host.

Long jobs (download + ASR + clip cutting) run in a worker thread and stream
progress over Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config as C, sources, store, tts
from .pipeline import Builder

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="英语听力训练", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    C.apply_env()
    store.init()


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

def current_user(request: Request) -> dict:
    """Resolve the signed-in user from the session cookie, or reject with 401."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    user = auth.session_user(token)
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def optional_user(request: Request) -> dict | None:
    return auth.session_user(request.cookies.get(auth.SESSION_COOKIE))


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,          # JS 读不到，降低 XSS 窃取风险
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


class RegisterBody(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


class ProfileBody(BaseModel):
    displayName: str | None = None
    background: str | None = None
    avatar: str | None = None


class PasswordBody(BaseModel):
    oldPassword: str
    newPassword: str


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    """Is anyone signed in, and does the instance still need its first user?"""
    user = optional_user(request)
    return {
        "authenticated": user is not None,
        "user": auth.public_view(user) if user else None,
        "needsSetup": auth.count_users() == 0,
    }


@app.post("/api/auth/register")
def auth_register(body: RegisterBody, response: Response) -> dict:
    try:
        user = auth.register(body.username, body.password, body.email)
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from e
    token = auth.create_session(user["id"])
    _set_session_cookie(response, token)
    return {"user": auth.public_view(user)}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, response: Response) -> dict:
    try:
        user = auth.authenticate(body.username, body.password)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    token = auth.create_session(user["id"])
    _set_session_cookie(response, token)
    return {"user": auth.public_view(user)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(current_user)) -> dict:
    return {"user": auth.public_view(user)}


@app.post("/api/auth/profile")
def auth_profile(body: ProfileBody, user: dict = Depends(current_user)) -> dict:
    try:
        updated = auth.update_profile(user["id"], body.displayName,
                                      body.background, body.avatar)
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from e
    return {"user": auth.public_view(updated)}


@app.post("/api/auth/password")
def auth_password(body: PasswordBody, user: dict = Depends(current_user)) -> dict:
    try:
        auth.change_password(user["id"], body.oldPassword, body.newPassword)
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# ---------------------------------------------------------------------------
# job registry (in-process; single-user desktop app, so this is enough)
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, job_id: str, label: str):
        self.id = job_id
        self.label = label
        self.queue: queue.Queue = queue.Queue()
        self.status = "running"
        self.result: dict | None = None
        self.error: str | None = None
        self.lesson_id: str | None = None
        self.started = time.time()
        self.subscribers = 0
        # 哪个用户发起的导入任务；课程会归属到这个用户名下
        self.owner_id: int = 0
        self._last: tuple[float, str] = (0.0, "")

    def emit(self, frac: float, msg: str) -> None:
        # The pipeline reports the same fraction many times while streaming a
        # download; collapse duplicates so the client is not flooded.
        if abs(frac - self._last[0]) < 0.005 and msg == self._last[1]:
            return
        self._last = (frac, msg)
        self.queue.put({"type": "progress", "frac": round(frac, 4), "msg": msg})

    def finish(self, lesson) -> None:
        self.status = "done"
        self.lesson_id = lesson.lesson_id
        self.result = {
            "id": lesson.lesson_id,
            "title": lesson.title,
            "sentences": len(lesson.sentences),
            "duration": lesson.duration,
            "sentenceSource": lesson.meta.get("sentenceSource"),
        }
        self.queue.put({"type": "done", "result": self.result})

    def fail(self, message: str) -> None:
        self.status = "error"
        self.error = message
        self.queue.put({"type": "error", "message": message})


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _run_job(job: Job, fn) -> None:
    try:
        lesson = fn(job.emit)
        store.upsert_lesson(lesson, job.owner_id)
        job.finish(lesson)
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
    finally:
        job.queue.put(None)          # sentinel: close streams


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class BuildUrlBody(BaseModel):
    url: str
    keepVideo: bool = False


class BuildLocalBody(BaseModel):
    path: str
    title: str | None = None


class PlayBody(BaseModel):
    index: int
    looped: bool = False


class StateBody(BaseModel):
    lastSentence: int = 0
    position: float = 0.0


class SettingsBody(BaseModel):
    autoAdvance: bool | None = None
    showTranslation: bool | None = None
    playbackRate: float | None = None
    loopCurrent: bool | None = None
    ttsVoice: str | None = None
    ttsEngine: str | None = None
    theme: str | None = None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    ok, desc = sources.cookies_available()
    return {
        "ok": True,
        "ffmpeg": C.FFMPEG.exists(),
        "model": C.ASR_MODEL,
        "dataDir": str(C.DATA_DIR),
        "cookies": {"configured": ok, "detail": desc,
                    "path": str(sources.cookies_file_path())},
    }


@app.get("/api/access")
def api_access(request: Request) -> dict:
    """Every URL this service can be reached at, for the desktop console."""
    from . import mdns as mdns_mod

    port = request.url.port or 8765
    try:
        import zeroconf  # noqa: F401
        mdns_available = True
    except ImportError:
        mdns_available = False
    return {
        "urls": mdns_mod.access_urls(port),
        "mdns": mdns_available,
        "hostname": __import__("socket").gethostname(),
        "lanIp": mdns_mod.lan_ip(),
    }


@app.get("/api/platforms")
def api_platforms() -> dict:
    """What each platform can and cannot do, so the UI can set expectations."""
    ok, desc = sources.cookies_available()
    return {
        "cookies": {"configured": ok, "detail": desc},
        "platforms": [
            {"id": "local", "name": "本地文件",
             "captions": True, "note": "最稳定，零风险"},
            {"id": "bilibili", "name": "B 站",
             "captions": True, "note": "英文字幕覆盖较好；未登录时字幕为空，会自动改用语音识别"},
            {"id": "youtube", "name": "YouTube",
             "captions": True, "note": "本机网络直连超时，当前不可用"},
            {"id": "douyin", "name": "抖音",
             "captions": False, "needsCookies": True,
             "note": "平台没有字幕接口，只能用本地语音识别；且必须提供登录信息"},
            {"id": "tiktok", "name": "TikTok",
             "captions": False, "needsCookies": True,
             "note": "同上，且 yt-dlp 侧长期不稳定"},
        ],
    }


@app.get("/api/lessons")
def api_lessons(user: dict = Depends(current_user)) -> dict:
    return {"lessons": store.list_lessons(user["id"])}


def _owned_lesson(lesson_id: str, user: dict) -> dict:
    """Fetch a lesson, but only if it belongs to this user."""
    owner = store.lesson_owner(lesson_id)
    if owner is not None and owner != user["id"]:
        raise HTTPException(404, "课程不存在")
    data = store.get_lesson(lesson_id, user["id"])
    if not data:
        raise HTTPException(404, "课程不存在")
    return data


@app.get("/api/lessons/{lesson_id}")
def api_lesson(lesson_id: str, user: dict = Depends(current_user)) -> dict:
    return _owned_lesson(lesson_id, user)


@app.delete("/api/lessons/{lesson_id}")
def api_delete(lesson_id: str, user: dict = Depends(current_user)) -> dict:
    if not store.delete_lesson(lesson_id, user["id"]):
        raise HTTPException(404, "课程不存在")
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/play")
def api_play(lesson_id: str, body: PlayBody,
             user: dict = Depends(current_user)) -> dict:
    _owned_lesson(lesson_id, user)
    store.record_play(lesson_id, body.index, body.looped, user["id"])
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/state")
def api_state(lesson_id: str, body: StateBody,
              user: dict = Depends(current_user)) -> dict:
    _owned_lesson(lesson_id, user)
    store.save_state(lesson_id, body.lastSentence, body.position, user["id"])
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/reset")
def api_reset(lesson_id: str, user: dict = Depends(current_user)) -> dict:
    _owned_lesson(lesson_id, user)
    store.reset_progress(lesson_id, user["id"])
    return {"ok": True}


@app.get("/api/stats")
def api_stats(user: dict = Depends(current_user)) -> dict:
    return store.stats(user["id"])


@app.get("/api/settings")
def api_get_settings(user: dict = Depends(current_user)) -> dict:
    return store.get_settings(user["id"])


@app.post("/api/settings")
def api_set_settings(body: SettingsBody,
                     user: dict = Depends(current_user)) -> dict:
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    return store.save_settings(values, user["id"])


@app.get("/api/browse")
def api_browse(path: str | None = None,
               user: dict = Depends(current_user)) -> dict:
    """Browse the filesystem so a local video can be picked in the UI.

    Only the admin may browse arbitrary paths: exposing the whole filesystem of
    the host to every registered user would leak far more than it helps.  Normal
    users are confined to the shared 我的素材 folder.
    """
    is_admin = (user.get("role") or "user") == "admin"
    shared = C.WORKSPACE / "我的素材"
    try:
        shared.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    if not is_admin:
        target = shared
    else:
        target = Path(path).expanduser() if path else Path.home()

    try:
        target = target.resolve()
    except OSError:
        raise HTTPException(400, "路径无效")

    if not is_admin:
        # Refuse to escape the shared folder with ../ tricks.
        try:
            target.relative_to(shared.resolve())
        except ValueError:
            target = shared

    if not target.is_dir():
        raise HTTPException(400, "不是目录")

    media, dirs = [], []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
                elif entry.suffix.lower() in (
                        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv",
                        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"):
                    media.append({
                        "name": entry.name,
                        "path": str(entry),
                        "size": entry.stat().st_size,
                    })
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, "没有权限读取该目录")

    parent = str(target.parent) if target.parent != target and is_admin else None
    return {"path": str(target), "parent": parent, "dirs": dirs, "media": media,
            "admin": is_admin}


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    # SSE 由浏览器的 EventSource 发起，同样会带上 Cookie。
    user = optional_user(request)
    if not user or job.owner_id != user["id"]:
        raise HTTPException(404, "任务不存在")

    async def gen():
        # Replay the last known progress so a late subscriber is not stuck.
        yield f"data: {json.dumps({'type': 'progress', 'frac': job._last[0], 'msg': job._last[1]})}\n\n"
        if job.status == "done" and job.result:
            yield f"data: {json.dumps({'type': 'done', 'result': job.result})}\n\n"
            return
        if job.status == "error":
            yield f"data: {json.dumps({'type': 'error', 'message': job.error})}\n\n"
            return
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, job.queue.get)
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("type") in ("done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, user: dict = Depends(current_user)) -> dict:
    job = JOBS.get(job_id)
    if not job or job.owner_id != user["id"]:
        raise HTTPException(404, "任务不存在")
    return {
        "id": job.id, "status": job.status, "error": job.error,
        "result": job.result, "label": job.label,
        "elapsed": round(time.time() - job.started, 1),
    }


@app.post("/api/build/url")
def build_url(body: BuildUrlBody, user: dict = Depends(current_user)) -> dict:
    url = (body.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "请输入以 http(s):// 开头的链接")
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, f"链接导入 {url[:60]}")
    job.owner_id = user["id"]
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(
        target=_run_job, args=(job, lambda emit: Builder(progress=emit)
                               .from_url(url, keep_video=body.keepVideo)),
        daemon=True,
    ).start()
    return {"jobId": job_id}


@app.post("/api/build/local")
def build_local(body: BuildLocalBody, user: dict = Depends(current_user)) -> dict:
    path = Path(body.path or "")
    if not path.exists():
        raise HTTPException(400, f"文件不存在：{path}")
    if path.suffix.lower() not in (
            ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".mp3",
            ".wav", ".m4a", ".aac", ".flac", ".ogg"):
        raise HTTPException(400, "不支持的文件类型")

    # 普通用户只能导入共享素材夹里的文件，避免读取服务器上任意路径。
    if (user.get("role") or "user") != "admin":
        shared = (C.WORKSPACE / "我的素材").resolve()
        try:
            path.resolve().relative_to(shared)
        except (ValueError, OSError):
            raise HTTPException(403, "普通用户只能导入「我的素材」文件夹里的文件")

    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, f"本地导入 {path.name}")
    job.owner_id = user["id"]
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(
        target=_run_job, args=(job, lambda emit: Builder(progress=emit)
                               .from_local(path, title=body.title)),
        daemon=True,
    ).start()
    return {"jobId": job_id}


@app.get("/api/tts")
def api_tts(text: str, voice: str = "en-US-AriaNeural",
            engine: str = "offline",
            user: dict = Depends(current_user)):
    """Read one sentence aloud.

    Defaults to the offline Piper engine, which works reliably.  `engine=online`
    opts into edge-tts (better sounding, but its unofficial endpoint currently
    answers 403 from this network) and silently falls back to offline if it
    fails, so the button never dead-ends.
    """
    try:
        path, used = tts.synthesize(text, voice, prefer=engine)
    except tts.TTSError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"朗读失败：{type(e).__name__}: {e}") from e

    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"X-TTS-Engine": used},
    )


@app.get("/api/tts/status")
def api_tts_status() -> dict:
    return tts.status()


# ---------------------------------------------------------------------------
# static media + UI
# ---------------------------------------------------------------------------

@app.get("/media/{lesson_id}/{name}")
def media_file(lesson_id: str, name: str, request: Request):
    # 浏览器请求音视频时会自动带上 Cookie，所以这里也能做归属校验。
    user = optional_user(request)
    if not user:
        raise HTTPException(401, "请先登录")
    owner = store.lesson_owner(lesson_id)
    if owner is not None and owner != user["id"]:
        raise HTTPException(404, "文件不存在")

    p = (C.MEDIA_DIR / lesson_id / name).resolve()
    if not str(p).startswith(str(C.MEDIA_DIR.resolve())) or not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p)


@app.get("/clips/{lesson_id}/{name}")
def clip_file(lesson_id: str, name: str, request: Request):
    user = optional_user(request)
    if not user:
        raise HTTPException(401, "请先登录")
    owner = store.lesson_owner(lesson_id)
    if owner is not None and owner != user["id"]:
        raise HTTPException(404, "文件不存在")

    p = (C.CLIP_DIR / lesson_id / name).resolve()
    if not str(p).startswith(str(C.CLIP_DIR.resolve())) or not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, media_type="audio/mpeg")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


class NoCacheStaticFiles(StaticFiles):
    """Serve the UI files with revalidation forced.

    Without this the browser caches style.css / app.js aggressively, so after
    the app is updated the user keeps seeing the old interface - and the only
    fix is "clear your cache", which is a terrible thing to ask.  `no-cache`
    still lets the browser keep a copy; it just has to check with us first,
    which is instant on a local server.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response


if WEB_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR)),
              name="static")


# ---------------------------------------------------------------------------
# LAN info helper
# ---------------------------------------------------------------------------

def lan_urls(port: int) -> list[str]:
    import socket

    urls = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in [u.split("//")[1].split(":")[0] for u in urls]:
                urls.append(f"http://{ip}:{port}")
    except OSError:
        pass
    return urls
