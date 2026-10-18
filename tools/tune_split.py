"""Tune the sentence splitter against real audio.

Re-splitting from cached ASR words is instant, so we can sweep parameter
combinations and score each one instead of guessing thresholds.

Scoring uses measurable symptoms of a bad cut:
  * a unit starting lowercase   -> almost certainly a mid-sentence offcut
  * a unit starting with ","    -> the previous cut was too early
  * a unit ending with ","      -> the cut happened after a comma, so the
                                   sentence continues; mildly bad
  * too many units under 1 s    -> fragments the user has to tap twice
  * units over 12 s             -> too long to study comfortably

Run:  python tools/tune_split.py [lesson_id]
"""
from __future__ import annotations

import itertools
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


def score(sentences) -> dict:
    """Lower is better.  Each symptom contributes its count."""
    n = len(sentences)
    if not n:
        return {"score": 1e9, "n": 0}

    orphan = sum(1 for s in sentences[1:] if s.text[:1].islower())
    comma_start = sum(1 for s in sentences[1:] if s.text.lstrip().startswith(","))
    comma_end = sum(1 for s in sentences[:-1] if s.text.rstrip().endswith(","))
    tiny = sum(1 for s in sentences if s.duration < 1.0)
    huge = sum(1 for s in sentences if s.duration > 12.0)

    # Weighting: a mid-sentence cut is the worst outcome because it makes the
    # unit unlearnable, so orphan/comma symptoms weigh heaviest.
    total = orphan * 3 + comma_start * 3 + comma_end * 2 + tiny * 2 + huge * 2

    durs = [s.duration for s in sentences]
    return {
        "score": total,
        "n": n,
        "orphan": orphan,
        "commaStart": comma_start,
        "commaEnd": comma_end,
        "tiny": tiny,
        "huge": huge,
        "median": round(statistics.median(durs), 2),
        "mean": round(statistics.mean(durs), 2),
        "maxDur": round(max(durs), 2),
    }


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

    audio = C.MEDIA_DIR / lesson_id / "audio16k.wav"
    print("=" * 78)
    print(f"  切句参数调优 · {lesson_id}")
    print("=" * 78)

    words, meta = asr.transcribe_words(
        audio, progress=lambda f, m: None)
    print(f"  词数 {len(words)}   缓存命中={meta.get('cached', False)}   "
          f"音频 {meta.get('duration', 0):.0f}s")

    # The current shipping configuration, as a baseline.
    base = split_words(words)
    b = score(base)
    print(f"\n  当前默认参数 (max_gap={C.SENT_MAX_GAP}, punct_gap={C.SENT_PUNCT_GAP}):")
    print(f"    得分 {b['score']:5d}  句数 {b['n']:4d}  残句 {b['orphan']:3d}  "
          f"逗号结尾 {b['commaEnd']:3d}  过短 {b['tiny']:2d}  中位 {b['median']}s")

    grid_gap = [0.9, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0]
    grid_punct = [0.15, 0.25, 0.35, 0.5, 0.8]
    grid_chars = [90, 110, 140]

    print(f"\n  扫描 {len(grid_gap) * len(grid_punct) * len(grid_chars)} 种组合 …")
    results = []
    for g, p, ch in itertools.product(grid_gap, grid_punct, grid_chars):
        s = split_words(words, max_gap=g, punct_gap=p, max_chars=ch)
        sc = score(s)
        results.append((sc["score"], g, p, ch, sc))

    results.sort(key=lambda r: r[0])

    print("\n  得分最低的 12 组：")
    print("    得分  句数  残句  逗尾  过短  过长  中位s  max_gap  punct_gap  字符上限")
    for sc_val, g, p, ch, sc in results[:12]:
        print(f"    {sc_val:5d} {sc['n']:5d} {sc['orphan']:5d} {sc['commaEnd']:5d} "
              f"{sc['tiny']:5d} {sc['huge']:5d} {sc['median']:6.2f} "
              f"{g:8.1f} {p:10.2f} {ch:9d}")

    best_val, bg, bp, bch, bsc = results[0]
    print("\n" + "=" * 78)
    print("  推荐配置")
    print("=" * 78)
    print(f"    SENT_MAX_GAP     = {bg}")
    print(f"    SENT_PUNCT_GAP   = {bp}")
    print(f"    SENT_MAX_CHARS   = {bch}")
    print(f"\n    相对默认参数：得分 {b['score']} -> {best_val} "
          f"（下降 {100 * (b['score'] - best_val) / max(1, b['score']):.0f}%）")
    print(f"    残句 {b['orphan']} -> {bsc['orphan']}，"
          f"逗号结尾 {b['commaEnd']} -> {bsc['commaEnd']}，"
          f"过短 {b['tiny']} -> {bsc['tiny']}")

    best = split_words(words, max_gap=bg, punct_gap=bp, max_chars=bch)
    print("\n  采用推荐配置后的前 14 句：")
    for s in best[:14]:
        print(f"    {s.index:3d}. [{s.start:7.2f}-{s.end:7.2f}] "
              f"({s.duration:5.2f}s) {s.text[:62]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
