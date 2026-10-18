"""Compare sentence splitting before/after the forced-cut fix, on real material.

Read-only: it re-splits the already-transcribed audio in memory and reports the
difference.  It never rewrites the lesson, so study progress is untouched.

Run:  python tools/bench_split.py <lesson_id>
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
sys.path.insert(0, str(HERE / "pydeps"))
sys.path.insert(0, str(WS / "app"))

from listenup import asr, config as C  # noqa: E402
from listenup.sentences import split_words  # noqa: E402


def _dur(s):
    """Duration of a sentence, whether it is a Sentence object or a plain dict."""
    if isinstance(s, dict):
        return float(s.get("duration", s["end"] - s["start"]))
    return s.duration


def _text(s):
    return s["text"] if isinstance(s, dict) else s.text


def stats(sentences, label):
    durs = [_dur(s) for s in sentences]
    short = [s for s in sentences if _dur(s) < 1.0]
    long_ = [s for s in sentences if _dur(s) > 12]
    # A unit starting lowercase is almost certainly a mid-sentence offcut.
    orphan_start = [s for s in sentences[1:] if _text(s)[:1].islower()]
    print(f"\n  【{label}】")
    print(f"    句子数        : {len(sentences)}")
    if durs:
        print(f"    时长中位数    : {statistics.median(durs):.2f}s")
        print(f"    时长平均      : {statistics.mean(durs):.2f}s")
    print(f"    过短 (<1s)    : {len(short)}")
    print(f"    过长 (>12s)   : {len(long_)}")
    print(f"    小写开头的残句: {len(orphan_start)}")
    return {
        "n": len(sentences),
        "median": statistics.median(durs) if durs else 0.0,
        "short": len(short), "long": len(long_), "orphan": len(orphan_start),
    }


def _index(s):
    return s["index"] if isinstance(s, dict) else s.index


def main() -> int:
    C.apply_env()
    lesson_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not lesson_id:
        dirs = [d for d in C.MEDIA_DIR.iterdir()
                if (d / "audio16k.wav").exists()
                and (d / "audio16k.wav").stat().st_size > 5_000_000]
        if not dirs:
            print("找不到足够大的音频素材")
            return 1
        lesson_id = dirs[0].name

    work = C.MEDIA_DIR / lesson_id
    audio = work / "audio16k.wav"
    print("=" * 66)
    print(f"  切句效果对比 · {lesson_id}")
    print("=" * 66)
    print(f"  音频: {audio.name}  ({audio.stat().st_size / 1048576:.1f} MB)")

    print("\n  [1/3] 识别（首次较慢，之后走缓存）…")
    words, meta = asr.transcribe_words(
        audio, progress=lambda f, m: print(f"        {f * 100:5.1f}% {m}", end="\r"))
    print(f"\n        词数 {len(words)}  用时 {meta.get('elapsed', '?')}s  "
          f"缓存命中={meta.get('cached', False)}")

    print("\n  [2/3] 读取当前课程里的切句结果…")
    manifest = work / "lesson.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    current = [s for s in data["sentences"]]
    print(f"        课程中共 {len(current)} 句（切句来源 "
          f"{data['meta'].get('sentenceSource')}）")

    print("\n  [3/3] 用新规则重新切句…")
    fresh = split_words(words)
    fresh = [s for s in fresh]

    old = stats(current, "修改前（你正在学的版本）")
    new = stats(fresh, "修改后（新规则）")

    print("\n" + "=" * 66)
    print("  对比结论")
    print("=" * 66)
    print(f"  指标           修改前    修改后    变化")
    rows = [
        ("句子数", "n"), ("时长中位数(s)", "median"),
        ("过短句 (<1s)", "short"), ("过长句 (>12s)", "long"),
        ("小写开头残句", "orphan"),
    ]
    for label, key in rows:
        a, b = old[key], new[key]
        if isinstance(a, float):
            print(f"  {label:16} {a:8.2f} {b:8.2f}   {b - a:+.2f}")
        else:
            print(f"  {label:16} {a:8d} {b:8d}   {b - a:+d}")

    print("\n  修改后前 10 句预览：")
    for s in fresh[:10]:
        print(f"    {s.index:3d}. [{s.start:7.2f}-{s.end:7.2f}] "
              f"({s.duration:5.2f}s) {s.text[:66]}")

    print("\n  修改后仍以逗号/连词开头的前 6 句（潜在残句）：")
    suspects = [s for s in fresh[1:]
                if s.text[:1].islower() or s.text.startswith(",")]
    for s in suspects[:6]:
        print(f"    {s.index:3d}. ({s.duration:5.2f}s) {s.text[:70]}")
    if not suspects:
        print("    （无）")

    print("\n  注：本次对比只读，未改动你的课程和学习进度。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
