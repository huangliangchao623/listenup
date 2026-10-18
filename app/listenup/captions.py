"""Caption parsing: SRT / VTT via pysubs2, plus Bilibili's subtitle JSON.

Bilibili subtitles are a JSON body list with `from`/`to` in seconds and
`content` holding the text.  Both AI subtitles (`lan` like `ai-zh`) and
uploader (CC) subtitles use the same shape.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .sentences import Cue

# Basic HTML-ish markup that shows up inside caption payloads.
_TAG = re.compile(r"<[^>]+>")
# Bilibili position tags like {\an8} or {\pos(1,2)}
_POS_TAG = re.compile(r"\{\\[^}]*\}")


def _clean(text: str) -> str:
    text = _POS_TAG.sub("", text or "")
    text = _TAG.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def parse_subs(path: str | Path) -> list[Cue]:
    """Read an .srt / .vtt / .ass file into cues (seconds)."""
    import pysubs2

    subs = pysubs2.load(str(path))
    cues: list[Cue] = []
    for ev in subs:
        if ev.is_comment or ev.type != "Dialogue":
            continue
        text = _clean(ev.plaintext)
        if not text:
            continue
        cues.append(Cue(start=ev.start / 1000.0, end=ev.end / 1000.0, text=text))
    cues.sort(key=lambda c: c.start)
    return cues


def parse_bilibili_json(path: str | Path) -> list[Cue]:
    """Read a Bilibili `subtitle_url` payload into cues."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    body = raw.get("body") if isinstance(raw, dict) else raw
    if not isinstance(body, list):
        return []
    cues: list[Cue] = []
    for item in body:
        try:
            start = float(item.get("from", 0.0))
            end = float(item.get("to", start))
        except (TypeError, ValueError):
            continue
        text = _clean(str(item.get("content", "")))
        if not text or end <= start:
            continue
        cues.append(Cue(start=start, end=end, text=text))
    cues.sort(key=lambda c: c.start)
    return cues


def parse_any(path: str | Path) -> list[Cue]:
    """Auto-detect the caption format and parse it."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        return parse_bilibili_json(p)
    return parse_subs(p)
