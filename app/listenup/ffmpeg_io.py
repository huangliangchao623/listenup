"""ffmpeg / ffprobe wrappers.

Two things here matter a lot for perceived quality:

  * Sentence clips are cut with `atrim` (sample-accurate) and get a 12 ms fade
    at both edges.  Cutting with `-c copy` snaps to frame boundaries (up to
    ~26 ms off for mp3) and leaves a residual frame that clicks.
  * `afade=t=out:st=` must be computed from the clip duration, otherwise the
    fade either does nothing or silences the whole clip.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config as C


class FFmpegError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    r = subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=timeout
    )
    if r.returncode != 0:
        tail = (r.stderr or "")[-800:]
        raise FFmpegError(f"{Path(args[0]).name} failed ({r.returncode}): {tail}")
    return r


def probe(path: str | Path) -> dict:
    """Return {duration, has_video, has_audio, width, height} for a media file."""
    r = _run([
        str(C.FFPROBE), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = 0.0
    try:
        duration = float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and a:
        try:
            duration = float(a.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
    return {
        "duration": round(duration, 3),
        "has_video": v is not None,
        "has_audio": a is not None,
        "width": (v or {}).get("width"),
        "height": (v or {}).get("height"),
    }


def extract_audio(src: str | Path, dst: str | Path,
                  sample_rate: int = 16000) -> Path:
    """Pull a mono 16 kHz wav out of any media file (what ASR wants)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(dst),
    ])
    return dst


def cut_clip(src: str | Path, dst: str | Path, start: float, end: float,
             sample_rate: int = C.CLIP_SAMPLE_RATE,
             fade: float = C.CLIP_FADE) -> Path:
    """Cut one sentence clip, sample-accurately, with click-free edges."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    f = min(fade, duration / 3.0)
    af = (
        f"atrim=start={start:.3f}:end={end:.3f},"
        f"asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={f:.3f},"
        f"afade=t=out:st={duration - f:.3f}:d={f:.3f}"
    )
    _run([
        str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-af", af,
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "libmp3lame", "-q:a", C.CLIP_BITRATE_Q, str(dst),
    ])
    return dst


def cut_clips(src: str | Path, out_dir: str | Path, spans,
              prefix: str = "seg") -> list[Path]:
    """Cut many clips.  `spans` is an iterable of (start, end, label)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for i, (start, end, _label) in enumerate(spans, 1):
        dst = out_dir / f"{prefix}_{i:04d}.mp3"
        cut_clip(src, dst, start, end)
        made.append(dst)
    return made
