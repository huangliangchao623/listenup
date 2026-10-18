"""Local speech recognition via faster-whisper.

Design notes:

* `word_timestamps=True` is non-negotiable - it is what makes sentence-level
  splitting possible.
* `vad_filter=True` plus `condition_on_previous_text=False` prevents the
  notorious long-form repetition loop where Whisper starts repeating a phrase
  for minutes on quiet or musical stretches.
* Model cache lives in the workspace; HF access goes through hf-mirror.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from . import config as C
from .sentences import Word

_model = None
_model_lock = threading.Lock()
_model_name: str | None = None


def load_model(name: str | None = None):
    """Load (and memoize) the CTranslate2 Whisper model."""
    global _model, _model_name
    name = name or C.ASR_MODEL
    with _model_lock:
        if _model is not None and _model_name == name:
            return _model
        C.apply_env()
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            name,
            device="cpu",
            compute_type=C.ASR_COMPUTE,
            cpu_threads=C.ASR_CPU_THREADS,
            num_workers=C.ASR_NUM_WORKERS,
            download_root=str(C.MODELS / "hub"),
        )
        _model_name = name
        return _model


def transcribe_words(audio_path: str | Path, language: str = "en",
                     model_name: str | None = None,
                     progress=None) -> tuple[list[Word], dict]:
    """Transcribe audio and return (words, info).

    Runs with the VAD filter first, which is right for speech.  Music, singing
    and heavily-processed audio can make the VAD discard everything, so an empty
    result triggers one retry without it rather than failing the import.

    `progress` is an optional callable(fraction, message).

    Word timings are cached per audio file.  Transcription is the slowest step
    (~100 s for a 16-minute podcast) and tuning the sentence splitter means
    re-running the *split* many times, so the cache makes that instant.
    """
    cache = _cache_path(audio_path, language, model_name)
    if cache is not None and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            cached = [Word(text=w["text"], start=w["start"], end=w["end"],
                           prob=w.get("prob", 1.0)) for w in data["words"]]
            if cached:
                if progress:
                    progress(0.99, "使用已缓存的识别结果…")
                meta = dict(data.get("meta", {}))
                meta["cached"] = True
                return cached, meta
        except (json.JSONDecodeError, KeyError, OSError):
            pass          # a corrupt cache must never block a real run

    words, meta = _transcribe_once(audio_path, language, model_name, progress,
                                   vad=True)
    if not words:
        if progress:
            progress(0.05, "未检测到清晰语音，改为全量识别…")
        words, meta2 = _transcribe_once(audio_path, language, model_name,
                                       progress, vad=False)
        meta2["retriedWithoutVad"] = True
        meta = meta2

    if cache is not None and words:
        _write_cache(cache, words, meta)
    return words, meta


def _cache_path(audio_path: str | Path, language: str,
                model_name: str | None) -> Path | None:
    """Cache file for this audio's word timings, or None if it cannot be keyed."""
    try:
        p = Path(audio_path)
        stat = p.stat()
    except OSError:
        return None
    key = hashlib.sha1(
        f"{p.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
        f"|{language}|{model_name or C.ASR_MODEL}".encode("utf-8")
    ).hexdigest()[:16]
    cache_dir = C.DATA_DIR / "asr_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.json"


def _write_cache(cache: Path, words: list[Word], meta: dict) -> None:
    payload = {
        "meta": meta,
        "words": [{"text": w.text, "start": w.start, "end": w.end,
                   "prob": w.prob} for w in words],
    }
    tmp = cache.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        tmp.unlink(missing_ok=True)


def _transcribe_once(audio_path: str | Path, language: str,
                     model_name: str | None, progress,
                     vad: bool) -> tuple[list[Word], dict]:
    model = load_model(model_name)
    if progress:
        progress(0.05, "识别中…")

    t0 = time.time()
    kwargs = dict(
        language=language,
        beam_size=5,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    if vad:
        kwargs["vad_filter"] = True
        kwargs["vad_parameters"] = dict(min_silence_duration_ms=400)

    segments, info = model.transcribe(str(audio_path), **kwargs)

    words: list[Word] = []
    total = float(getattr(info, "duration", 0.0) or 0.0)
    for seg in segments:
        for w in (seg.words or []):
            text = w.word
            if text is None or not text.strip():
                continue
            words.append(Word(
                text=text,
                start=float(w.start or 0.0),
                end=float(w.end or 0.0),
                prob=float(w.probability) if w.probability is not None else 1.0,
            ))
        if progress and total > 0:
            done = min(1.0, float(getattr(seg, "end", 0.0) or 0.0) / total)
            progress(0.05 + done * 0.9, f"识别中… {int(done * 100)}%")

    elapsed = time.time() - t0
    meta = {
        "model": _model_name or C.ASR_MODEL,
        "language": getattr(info, "language", language),
        "language_probability": round(
            float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "duration": round(total, 3),
        "elapsed": round(elapsed, 1),
        "rtf": round(elapsed / total, 3) if total > 1 else None,
        "words": len(words),
        "vad": vad,
    }
    if progress:
        progress(0.99, "切句中…")
    return words, meta
