"""Sentence segmentation - the heart of the product.

Two inputs are supported:

  1. Word-level tokens from ASR (faster-whisper with word_timestamps=True).
  2. Caption cues (SRT/VTT/AI-subtitle lines) which are line-level, not
     sentence-level, and must be merged before they can be split.

The rules below are deliberately conservative: merging two sentences into one
clickable unit is a mild annoyance, but splitting one sentence in half makes the
feature useless ("click a sentence" then hearing half of it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import config as C

# --- sentence-final punctuation -------------------------------------------
TERMINAL = ".!?…。！？"
# Weaker break points.  Used only when a span must be cut for length: breaking
# after a comma keeps the two halves grammatical, unlike cutting mid-clause.
SECONDARY = ",;:—–，、；："
# A period ending one of these is not a sentence boundary.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "no", "fig", "approx", "inc",
    "ltd", "co", "dept", "est", "min", "max", "sec", "hr", "yr", "vol",
}
# Words that strongly suggest the *next* line continues the current sentence.
# Only used when the previous line did NOT end with sentence-final punctuation:
# "and" / "so" / "but" can open a new sentence, so they must never veto a real
# sentence boundary.
MERGE_CONJUNCTIONS = {
    "and", "or", "but", "because", "so", "that", "which", "who", "whom",
    "whose", "where", "when", "while", "if", "unless", "although", "though",
    "to", "of", "in", "on", "at", "for", "with", "as", "than", "then",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "the", "a", "an", "my", "your", "his", "her", "its", "our", "their",
    "this", "these", "those",
}

# Non-dialogue caption noise that must never become a clickable unit.
_NOISE_PATTERNS = [
    re.compile(r"^\s*\[.*\]\s*$"),        # [Music]  [Applause]
    re.compile(r"^\s*\(.*\)\s*$"),        # (laughs)  (sighs)
    re.compile(r"^\s*[♪♫♬#*\-–—\s]*\s*$"),  # music notes / separators only
    re.compile(r"^\s*[\d\s:.,%-]+\s*$"),  # pure numbers, timestamps
]


def is_noise(text: str) -> bool:
    """True for caption lines that carry no speakable content."""
    t = (text or "").strip()
    if not t:
        return True
    if t.strip("♪♫♬ ").strip() == "":
        return True
    return any(p.match(t) for p in _NOISE_PATTERNS)


def _ends_sentence(text: str) -> bool:
    """Does this text end a sentence, accounting for abbreviations?"""
    t = (text or "").rstrip()
    if not t:
        return False
    if t[-1] in TERMINAL:
        if t[-1] == ".":
            # Look at the trailing word; "Dr." / "U.S." are not boundaries.
            tail = re.split(r"[\s(\[\"']", t.rstrip("."))[-1].lower()
            if tail in ABBREVIATIONS:
                return False
            # Single capital letter + period = initial ("J. K. Rowling").
            if len(tail) == 1 and tail.isalpha():
                return False
        return True
    return False


def _first_word(text: str) -> str:
    m = re.search(r"[A-Za-z']+", text or "")
    return m.group(0).lower() if m else ""


def _ends_secondary(text: str) -> bool:
    """Does this text end on a comma/semicolon/dash - a safe place to cut?"""
    t = (text or "").rstrip()
    return bool(t) and t[-1] in SECONDARY


@dataclass
class Word:
    """One ASR word with its own timing."""
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Sentence:
    """One clickable study unit."""
    index: int
    start: float
    end: float
    text: str
    words: list[dict] = field(default_factory=list)
    audio: str | None = None
    source: str = "asr"          # asr | caption | caption_aligned
    quality: float = 1.0         # 0..1, used to flag suspect units
    # True when this unit ended on an explicit signal (sentence-final
    # punctuation, or a clear pause).  Such a unit must not be merged into a
    # neighbour, even if it is short.
    natural_break: bool = False
    # True when the unit had to be cut because it grew too long.  A forced cut
    # is a compromise, so a short fragment produced by one is allowed to merge
    # back even though it may carry a period from the original sentence.
    forced_cut: bool = False

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": self.duration,
            "text": self.text,
            "words": self.words,
            "audio": self.audio,
            "source": self.source,
            "quality": self.quality,
        }


def _sentence_from_span(words: Sequence[Word], start_i: int, end_i: int,
                        source: str = "asr",
                        natural_break: bool = False,
                        forced_cut: bool = False) -> Sentence:
    """Build a Sentence from words[start_i:end_i] (exclusive end)."""
    span = words[start_i:end_i]
    text = "".join(w.text for w in span).strip()
    text = re.sub(r"\s+", " ", text)
    probs = [w.prob for w in span if w.prob is not None]
    quality = sum(probs) / len(probs) if probs else 1.0
    return Sentence(
        index=0,
        start=span[0].start,
        end=span[-1].end,
        text=text,
        words=[{"w": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}
               for w in span],
        source=source,
        quality=round(quality, 3),
        natural_break=natural_break,
        forced_cut=forced_cut,
    )


def _merge_short(sentences: list[Sentence]) -> list[Sentence]:
    """Absorb fragments too small to be worth studying on their own.

    A fragment is absorbed when it looks genuinely unfinished: it did not end on
    a natural break, or it was produced by a forced length cut (in which case a
    trailing period is the *original* sentence's, not evidence that this piece
    stands alone), and it holds very few words.

    A short but complete unit that ended on a real pause or punctuation ("Yes."
    / "I am fine.") stays its own study unit, because merging it would make
    "click a sentence" play two sentences.
    """
    if not sentences:
        return []
    out: list[Sentence] = []
    for s in sentences:
        word_count = len(s.words) if s.words else len(s.text.split())
        unfinished = (
            not s.natural_break
            and (s.forced_cut or not _ends_sentence(s.text))
        )
        too_short = (
            s.duration < C.SENT_MIN_DURATION or word_count <= 4
            or len(s.text) < C.SENT_MIN_CHARS
        )
        if out and unfinished and too_short:
            prev = out[-1]
            prev.end = s.end
            prev.text = (prev.text + " " + s.text).strip()
            prev.words.extend(s.words)
            prev.quality = round((prev.quality + s.quality) / 2, 3)
            prev.forced_cut = s.forced_cut
            prev.natural_break = s.natural_break
        else:
            out.append(s)
    return out


def split_words(words: Iterable[Word], source: str = "asr",
                max_gap: float | None = None,
                punct_gap: float | None = None,
                max_duration: float | None = None,
                max_chars: float | None = None) -> list[Sentence]:
    """Group ASR words into sentences.

    A break happens when any of these is true:
      * the word ends with sentence-final punctuation **and** there is a pause
      * the silence gap exceeds `max_gap` (a genuine utterance boundary)
      * the run exceeds `max_duration` / `max_chars`

    Why punctuation alone is not enough: Whisper's punctuation is a guess, and
    on conversational material it is unreliable.  Requiring a pause alongside it
    stops the splitter from breaking in the middle of a clause whenever the
    transcriber happens to emit a period.

    Why the gap threshold is high: in podcasts and vlogs people pause constantly
    mid-sentence - to breathe, to think, for emphasis.  A naive 0.75 s threshold
    cut almost every sentence in half on real material.

    The thresholds are parameters so they can be tuned against real audio
    (see tools/tune_split.py) instead of being guessed.
    """
    words = list(words)
    if not words:
        return []

    max_gap = C.SENT_MAX_GAP if max_gap is None else max_gap
    punct_gap = C.SENT_PUNCT_GAP if punct_gap is None else punct_gap
    max_duration = C.SENT_MAX_DURATION if max_duration is None else max_duration
    max_chars = C.SENT_MAX_CHARS if max_chars is None else max_chars

    # Counts characters per span, trimming the outer whitespace that Whisper
    # attaches to each token.
    def span_chars(a: int, b: int) -> int:
        return len("".join(x.text for x in words[a:b]).strip())

    sentences: list[Sentence] = []
    start_i = 0
    i = 0
    n_words = len(words)

    while i < n_words:
        w = words[i]
        nxt = words[i + 1] if i + 1 < n_words else None
        gap = (nxt.start - w.end) if nxt else 1e9
        run_dur = w.end - words[start_i].start
        run_chars = span_chars(start_i, i + 1)

        broke = False
        natural = False
        forced = False
        cut_at = i + 1

        if _ends_sentence(w.text) and gap >= punct_gap:
            # Punctuation corroborated by a pause: a real sentence end.
            broke, natural = True, True
        elif gap > max_gap:
            # A long silence is an utterance boundary on its own.
            broke, natural = True, True
        elif run_dur > max_duration or run_chars > max_chars:
            broke, natural, forced = True, False, True
        elif nxt is None:
            broke, natural, forced = True, False, True

        if not broke:
            i += 1
            continue

        if forced and cut_at - start_i > 2:
            # Prefer the latest secondary punctuation in the back half of the
            # span, so the pieces stay balanced rather than one huge + one tiny.
            floor_i = start_i + max(1, (cut_at - start_i) // 2)
            for j in range(cut_at - 1, floor_i - 1, -1):
                if _ends_secondary(words[j].text):
                    look_gap = words[j + 1].start - words[j].end
                    if look_gap > 0.02:
                        cut_at = j + 1
                    break

        s = _sentence_from_span(words, start_i, cut_at, source, natural, forced)
        if s.text and not is_noise(s.text):
            sentences.append(s)

        # Words from cut_at onward start the next span.  When a secondary cut
        # moved the boundary backwards, `i` must follow it so those words are
        # not skipped.
        start_i = cut_at
        i = cut_at

    return _reindex(_merge_short(sentences))


def _reindex(sentences: list[Sentence]) -> list[Sentence]:
    for n, s in enumerate(sentences, 1):
        s.index = n
    return sentences


# ---------------------------------------------------------------------------
# Caption (cue) path
# ---------------------------------------------------------------------------

@dataclass
class Cue:
    """One caption line with line-level timing (milliseconds or seconds)."""
    start: float
    end: float
    text: str


def merge_cues(cues: Iterable[Cue], gap_break: float = 0.8) -> list[Sentence]:
    """Merge caption lines into sentences.

    Caption cues break at arbitrary points ("...I want to tell you" / "about my
    day."), so they cannot be used as sentence units directly.
    """
    cues = [c for c in cues if not is_noise(c.text)]
    if not cues:
        return []

    groups: list[tuple[list[Cue], bool]] = []
    cur: list[Cue] = [cues[0]]

    for prev, cue in zip(cues, cues[1:]):
        gap = cue.start - prev.end
        prev_text = prev.text.strip()
        nxt_word = _first_word(cue.text)
        merged_chars = len(" ".join(c.text for c in cur)) + len(cue.text)
        merged_dur = cue.end - cur[0].start

        # Dominant signals: these break regardless of continuation heuristics.
        # A long silence or an over-long unit is unambiguous.
        dominant_break = (
            gap >= gap_break
            or merged_chars > C.SENT_MAX_CHARS
            or merged_dur > C.SENT_MAX_DURATION
        )
        # Decide whether the next cue continues the current sentence.
        #  * A lowercase start is the strongest continuation signal.
        #  * Otherwise only an explicitly unfinished previous line may be
        #    continued by a conjunction, and only across a negligible pause.
        #    This keeps "Hello, I wanted to say" + "and then I left" together
        #    while still breaking "This is the first sentence." + "And this is
        #    the second one.".
        starts_lower = cue.text[:1].islower()
        continues = starts_lower or (
            not _ends_sentence(prev_text)
            and nxt_word in MERGE_CONJUNCTIONS
            and gap < 0.3
        )
        punctuation_break = _ends_sentence(prev_text) and not continues

        if dominant_break or punctuation_break:
            groups.append((cur, dominant_break or punctuation_break))
            cur = [cue]
        else:
            cur.append(cue)

    groups.append((cur, False))

    sentences = []
    for g, natural in groups:
        text = " ".join(c.text.strip() for c in g)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or is_noise(text):
            continue
        sentences.append(Sentence(
            index=0, start=g[0].start, end=g[-1].end, text=text,
            source="caption", natural_break=natural,
        ))
    return _reindex(_merge_short(sentences))


# ---------------------------------------------------------------------------
# Sentence-level .srt / .vtt output (for reference and for other tools)
# ---------------------------------------------------------------------------

def _fmt_ts(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(sentences: Sequence[Sentence]) -> str:
    blocks = []
    for n, s in enumerate(sentences, 1):
        blocks.append(
            f"{n}\n{_fmt_ts(s.start)} --> {_fmt_ts(s.end)}\n{s.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(sentences: Sequence[Sentence]) -> str:
    blocks = ["WEBVTT\n"]
    for n, s in enumerate(sentences, 1):
        blocks.append(
            f"{n}\n{_fmt_ts(s.start, '.')} --> {_fmt_ts(s.end, '.')}\n{s.text}\n"
        )
    return "\n".join(blocks)
