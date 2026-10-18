"""The ingestion pipeline: media -> sentence-level study units.

Caption strategy (in priority order, always degrading gracefully):

  1. A sidecar .srt/.vtt next to a local file, or subtitles embedded in the
     container.
  2. Bilibili's caption API for an English (or English AI) track - only when the
     user supplied their own session.
  3. yt-dlp download of uploader subtitles, then auto-generated ones.
  4. Local ASR with faster-whisper, which always works and needs no account.

Tiers 1-3 still run ASR to sanity-check coverage, because a caption track that
covers only 20% of the audio is worse than no captions at all.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import asr, captions, config as C, ffmpeg_io, sources
from .sentences import Sentence, merge_cues, split_words, to_srt, to_vtt

Progress = Callable[[float, str], None]

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa", ".json"}


@dataclass
class Lesson:
    """A finished lesson: media + sentence units, ready to study."""
    lesson_id: str
    title: str
    duration: float
    media_path: Path
    has_video: bool
    sentences: list[Sentence]
    meta: dict

    def to_dict(self) -> dict:
        return {
            "id": self.lesson_id,
            "title": self.title,
            "duration": round(self.duration, 3),
            "hasVideo": self.has_video,
            "media": f"/media/{self.lesson_id}/{self.media_path.name}",
            "sentences": [s.to_dict() for s in self.sentences],
            "meta": self.meta,
        }


def _sid(path: Path) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in path.stem)
    return f"{safe[:48]}_{int(time.time()):x}"


def _find_sidecar(media: Path) -> list[Path]:
    """Subtitle files sitting next to a local video, in any common language tag."""
    out = []
    stem = media.stem
    for p in media.parent.iterdir() if media.parent.exists() else []:
        if p.suffix.lower() in SUB_EXT and p.stem.startswith(stem):
            out.append(p)
    # Prefer English-tagged files.
    out.sort(key=lambda p: (0 if ".en" in p.name.lower() else 1, p.name))
    return out


def _spoken_chars(sentences: list[Sentence]) -> int:
    return sum(len(s.text) for s in sentences)


class Builder:
    """Builds one lesson, reporting progress through a callback."""

    def __init__(self, progress: Progress | None = None):
        self.progress = progress or (lambda f, m: None)
        self._p = 0.0

    def step(self, frac: float, msg: str) -> None:
        self._p = max(self._p, frac)
        self.progress(self._p, msg)

    # -- entry points ------------------------------------------------------

    def from_local(self, media_path: str | Path, lesson_id: str | None = None,
                   title: str | None = None) -> Lesson:
        media_path = Path(media_path).resolve()
        if not media_path.exists():
            raise FileNotFoundError(media_path)

        lid = lesson_id or _sid(media_path)
        work = C.MEDIA_DIR / lid
        work.mkdir(parents=True, exist_ok=True)

        self.step(0.02, "读取媒体信息…")
        info = ffmpeg_io.probe(media_path)
        if not info["has_audio"]:
            raise ValueError("这个文件里没有音轨，无法用于听力训练")

        # Copy the media in so the library is self-contained.
        local_media = work / media_path.name
        if media_path != local_media and not local_media.exists():
            self.step(0.05, "复制媒体文件…")
            try:
                shutil.copy2(media_path, local_media)
            except OSError:
                local_media = media_path      # fall back to referencing in place

        sidecars = _find_sidecar(media_path)
        self.step(0.10, "提取音轨…")
        audio16k = ffmpeg_io.extract_audio(local_media, work / "audio16k.wav")

        return self._finish(
            lid=lid,
            title=title or media_path.stem,
            media=local_media,
            audio16k=audio16k,
            duration=info["duration"],
            has_video=info["has_video"],
            caption_files=sidecars,
            meta={"source": "local", "origin": str(media_path)},
        )

    def from_url(self, url: str, lesson_id: str | None = None,
                 keep_video: bool = True) -> Lesson:
        platform = sources.detect_platform(url)
        self.step(0.02, "解析链接…")

        try:
            meta_probe = sources.list_subtitles(url)
        except Exception as e:  # noqa: BLE001
            raise sources.SourceError(f"链接解析失败：{e}") from e

        title = meta_probe.get("title") or url
        lid = lesson_id or _sid(Path(meta_probe.get("id") or "video"))
        work = C.MEDIA_DIR / lid
        work.mkdir(parents=True, exist_ok=True)

        # A listening trainer wants audio; keep video only when the platform
        # cannot give us a clean audio-only stream or the user asked for it.
        self.step(0.05, "下载音频…")
        media = sources.download_media(
            url, work / "dl", audio_only=not keep_video,
            progress=lambda f, m: self.step(0.05 + f * 0.35, m),
        )

        self.step(0.42, "整理媒体文件…")
        local_media = work / media.name
        if media != local_media:
            try:
                shutil.move(str(media), str(local_media))
            except OSError:
                local_media = media

        info = ffmpeg_io.probe(local_media)
        self.step(0.48, "提取音轨…")
        audio16k = ffmpeg_io.extract_audio(local_media, work / "audio16k.wav")

        # Captions: Bilibili API first (best language metadata), then yt-dlp.
        caption_files: list[Path] = []
        if platform == "bilibili":
            self.step(0.52, "尝试获取 B 站字幕…")
            tracks = sources.bilibili_subtitles(url)
            track = sources.pick_english_track(tracks)
            if track:
                try:
                    dst = C.SUB_DIR / f"{lid}.bili.json"
                    sources.download_bilibili_subtitle(track, dst)
                    caption_files.append(dst)
                except Exception:
                    pass

        if not caption_files and platform not in sources.NO_CAPTION_PLATFORMS:
            self.step(0.54, "尝试下载平台字幕…")
            try:
                caption_files = sources.download_subtitles(url, work / "subs")
            except Exception:
                caption_files = []

        if platform in sources.NO_CAPTION_PLATFORMS and not caption_files:
            self.step(0.55, "该平台没有字幕接口，改用本地语音识别…")

        return self._finish(
            lid=lid,
            title=title,
            media=local_media,
            audio16k=audio16k,
            duration=info["duration"] or float(meta_probe.get("duration") or 0.0),
            has_video=info["has_video"],
            caption_files=caption_files,
            meta={
                "source": "url",
                "platform": platform,
                "url": url,
                "uploader": meta_probe.get("uploader") or "",
            },
            caption_kind_hint=("manual" if meta_probe.get("manual") else None),
        )

    # -- shared tail -------------------------------------------------------

    def _finish(self, *, lid: str, title: str, media: Path, audio16k: Path,
                duration: float, has_video: bool, caption_files: list[Path],
                meta: dict, caption_kind_hint: str | None = None) -> Lesson:
        work = media.parent
        words = []
        asr_meta: dict = {}
        caption_sentences: list[Sentence] = []
        caption_kind = None

        # Parse whatever captions we got.
        for cf in caption_files:
            try:
                cues = captions.parse_any(cf)
            except Exception:
                continue
            if not cues:
                continue
            s = merge_cues(cues)
            if _spoken_chars(s) > _spoken_chars(caption_sentences):
                caption_sentences = s
        if caption_sentences:
            caption_kind = caption_kind_hint or "manual"

        # Always run ASR: it is the ground truth for coverage, and its word
        # timings give us much tighter clip boundaries than caption cues.
        self.step(0.58, "语音识别（本地模型，首次加载较慢）…")
        try:
            words, asr_meta = asr.transcribe_words(
                audio16k,
                progress=lambda f, m: self.step(0.58 + f * 0.30, m),
            )
        except Exception as e:  # noqa: BLE001
            asr_meta = {"error": f"{type(e).__name__}: {e}"}

        asr_sentences = split_words(words) if words else []

        # Decide the caption tier by coverage: a caption track covering less
        # than ~55% of the spoken characters is not worth trusting.
        chosen: list[Sentence]
        chosen_kind: str
        if caption_sentences and asr_sentences:
            ratio = _spoken_chars(caption_sentences) / max(1, _spoken_chars(asr_sentences))
            if ratio >= 0.55:
                chosen, chosen_kind = caption_sentences, f"caption:{caption_kind}"
            else:
                chosen, chosen_kind = asr_sentences, "asr:captions_incomplete"
        elif caption_sentences:
            chosen, chosen_kind = caption_sentences, f"caption:{caption_kind}"
        elif asr_sentences:
            chosen, chosen_kind = asr_sentences, "asr"
        else:
            raise RuntimeError("既没有字幕也无法识别出语音内容")

        # Cut one audio clip per sentence.
        self.step(0.90, f"切分 {len(chosen)} 个句子音频…")
        clips_dir = C.CLIP_DIR / lid
        if clips_dir.exists():
            shutil.rmtree(clips_dir, ignore_errors=True)
        clips_dir.mkdir(parents=True, exist_ok=True)
        spans = [(s.start, s.end, s.text) for s in chosen]
        made = ffmpeg_io.cut_clips(audio16k, clips_dir, spans)
        for s, p in zip(chosen, made):
            s.audio = f"seg_{s.index:04d}.mp3"

        meta = {
            **meta,
            "sentenceSource": chosen_kind,
            "asr": asr_meta,
            "captionsUsed": bool(caption_sentences),
            "sentenceCount": len(chosen),
            "builtAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        self.step(0.97, "写入字幕文件…")
        (work / "sentences.srt").write_text(to_srt(chosen), encoding="utf-8")
        (work / "sentences.vtt").write_text(to_vtt(chosen), encoding="utf-8")

        lesson = Lesson(
            lesson_id=lid, title=title, duration=duration, media_path=media,
            has_video=has_video, sentences=chosen, meta=meta,
        )
        manifest = work / "lesson.json"
        manifest.write_text(
            json.dumps(lesson.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.step(1.0, "完成")
        return lesson
