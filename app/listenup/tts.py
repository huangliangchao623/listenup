"""Read-aloud (TTS) with a reliable offline path.

Two engines:

  * **Piper** (offline, MIT-licensed voice models) is the primary path.  It was
    verified working on this machine: ~0.16 s to synthesize 2.6 s of speech.
  * **edge-tts** is a nicer-sounding online voice, but it drives an unofficial
    Microsoft endpoint that currently answers 403 from this network, so it is
    only attempted when explicitly requested and always falls back.

The public entry point is `synthesize(text, voice)` which returns the path to a
cached mp3/wav.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import wave
from pathlib import Path

from . import config as C

VOICE_DIR = C.MODELS / "voices"
CACHE_DIR = C.DATA_DIR / "tts"

# Piper voices we ship / can fetch.  Keys are the UI-facing names so the
# settings page does not have to know about engines.
PIPER_VOICES = {
    "en_US-lessac-medium": "美音 · 女声 Lessac",
}

# Which Piper voice backs each UI voice choice.  edge-tts names are kept as the
# keys because the settings UI already uses them.
UI_TO_PIPER = {
    "en-US-AriaNeural": "en_US-lessac-medium",
    "en-US-GuyNeural": "en_US-lessac-medium",
    "en-GB-SoniaNeural": "en_US-lessac-medium",
    "en-GB-RyanNeural": "en_US-lessac-medium",
}

_lock = threading.Lock()
_voice = None
_voice_name: str | None = None


class TTSError(RuntimeError):
    pass


def piper_available() -> bool:
    if not (VOICE_DIR / f"{_default_voice()}.onnx").exists():
        return False
    try:
        import piper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _default_voice() -> str:
    return "en_US-lessac-medium"


def _load_piper(name: str):
    """Load (and memoize) a Piper voice.  Loading takes ~2 s, hence the cache."""
    global _voice, _voice_name
    with _lock:
        if _voice is not None and _voice_name == name:
            return _voice
        model = VOICE_DIR / f"{name}.onnx"
        if not model.exists():
            raise TTSError(f"离线语音模型缺失：{model.name}")
        from piper import PiperVoice

        _voice = PiperVoice.load(str(model))
        _voice_name = name
        return _voice


def _cache_path(text: str, voice: str, ext: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.{ext}"


def piper_synthesize(text: str, voice: str | None = None) -> Path:
    """Synthesize to an mp3 in the cache, returning its path."""
    name = UI_TO_PIPER.get(voice or "", _default_voice())
    final = _cache_path(text, f"piper:{name}", "mp3")
    if final.exists() and final.stat().st_size > 0:
        return final

    v = _load_piper(name)
    raw = final.with_suffix(".raw.wav")

    with wave.open(str(raw), "wb") as w:
        v.synthesize_wav(text, w)

    # Re-encode to mp3 so the browser gets a small, universally supported file.
    r = subprocess.run(
        [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(raw), "-ac", "1", "-ar", "24000",
         "-c:a", "libmp3lame", "-q:a", "5", str(final)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    raw.unlink(missing_ok=True)
    if r.returncode != 0 or not final.exists():
        raise TTSError(f"音频转码失败：{(r.stderr or '')[-300:]}")
    return final


def edge_synthesize(text: str, voice: str) -> Path:
    """Online path.  Kept optional because the endpoint is unreliable."""
    final = _cache_path(text, f"edge:{voice}", "mp3")
    if final.exists() and final.stat().st_size > 0:
        return final

    import asyncio

    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice).save(str(final))

    asyncio.run(go())
    if not final.exists() or final.stat().st_size == 0:
        raise TTSError("在线朗读没有返回音频")
    return final


def synthesize(text: str, voice: str | None = None,
               prefer: str = "offline") -> tuple[Path, str]:
    """Synthesize `text`; returns (path, engine_used).

    `prefer="offline"` (default) goes straight to Piper.  `prefer="online"`
    tries edge-tts first and falls back to Piper, so the read-aloud button keeps
    working even when Microsoft's endpoint rejects us - which it currently does.
    """
    text = (text or "").strip()
    if not text:
        raise TTSError("缺少文本")
    if len(text) > 600:
        text = text[:600]

    errors: list[str] = []

    if prefer == "online":
        try:
            return edge_synthesize(text, voice or "en-US-AriaNeural"), "edge-tts"
        except Exception as e:  # noqa: BLE001
            errors.append(f"edge-tts: {type(e).__name__}")

    try:
        return piper_synthesize(text, voice), "piper"
    except Exception as e:  # noqa: BLE001
        errors.append(f"piper: {type(e).__name__}: {e}")

    raise TTSError("朗读失败（" + "；".join(errors) + "）")


def status() -> dict:
    """What the settings page shows about read-aloud availability."""
    have_piper_model = (VOICE_DIR / f"{_default_voice()}.onnx").exists()
    return {
        "offline": {
            "engine": "piper",
            "available": piper_available(),
            "model": _default_voice(),
            "modelPresent": have_piper_model,
            "license": "MIT",
        },
        "online": {
            "engine": "edge-tts",
            "available": _edge_importable(),
            "note": "免费接口不稳定（实测返回 403），仅作可选增强",
        },
        "cacheDir": str(CACHE_DIR),
    }


def _edge_importable() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False
