"""Command-line entry point for the ingestion pipeline.

Examples
--------
    python -m listenup.cli build-local "D:\\video\\vlog.mp4"
    python -m listenup.cli build-url "https://www.bilibili.com/video/BVxxxx"
    python -m listenup.cli subs "https://www.bilibili.com/video/BVxxxx"
    python -m listenup.cli check

Run from the workspace root with PYTHONPATH including app/ and tools/pydeps/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config as C, ffmpeg_io, sources, store
from .pipeline import Builder


def _bar(frac: float, msg: str) -> None:
    width = 28
    filled = int(max(0.0, min(1.0, frac)) * width)
    bar = "█" * filled + "░" * (width - filled)
    sys.stderr.write(f"\r  [{bar}] {frac * 100:5.1f}%  {msg[:44]:<44}")
    sys.stderr.flush()


def cmd_check(_args) -> int:
    """Verify the toolchain is present and working."""
    print("=== toolchain check ===")
    ok = True
    for label, path in (("ffmpeg", C.FFMPEG), ("ffprobe", C.FFPROBE),
                        ("yt-dlp (module)", None)):
        if path is None:
            try:
                import yt_dlp  # noqa: F401
                print(f"  [ok]   {label}")
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {label}: {e}")
                ok = False
            continue
        if path.exists():
            print(f"  [ok]   {label:16} {path}")
        else:
            print(f"  [FAIL] {label:16} missing at {path}")
            ok = False

    try:
        import faster_whisper  # noqa: F401
        print("  [ok]   faster-whisper")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] faster-whisper: {e}")
        ok = False

    model_dir = C.MODELS / "hub" / f"models--Systran--faster-whisper-{C.ASR_MODEL}"
    if model_dir.exists():
        print(f"  [ok]   model {C.ASR_MODEL:16} {model_dir.name}")
    else:
        print(f"  [warn] model {C.ASR_MODEL} not cached (will download on first use)")

    print(f"\n  data dir : {C.DATA_DIR}")
    print(f"  db       : {C.DB_PATH}")
    return 0 if ok else 1


def cmd_subs(args) -> int:
    """List the subtitles a URL offers, without downloading media."""
    try:
        info = sources.list_subtitles(args.url)
    except Exception as e:  # noqa: BLE001
        print(f"failed: {e}", file=sys.stderr)
        return 1
    print(f"title    : {info['title']}")
    print(f"uploader : {info['uploader']}")
    print(f"duration : {info['duration']}s")
    print(f"manual   : {', '.join(info['manual']) or '(none)'}")
    print(f"auto     : {', '.join(info['auto']) or '(none)'}")
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _report(lesson, elapsed: float) -> None:
    print(f"\n=== 完成（{elapsed:.1f}s）===")
    print(f"  标题      : {lesson.title}")
    print(f"  时长      : {lesson.duration:.1f}s")
    print(f"  句子数    : {len(lesson.sentences)}")
    print(f"  切句来源  : {lesson.meta.get('sentenceSource')}")
    asr_meta = lesson.meta.get("asr") or {}
    if asr_meta.get("rtf"):
        print(f"  识别速度  : RTF {asr_meta['rtf']} "
              f"({asr_meta.get('elapsed')}s / {asr_meta.get('duration')}s 音频)")
    if asr_meta.get("error"):
        print(f"  ⚠ 识别错误 : {asr_meta['error']}")
    print(f"  媒体文件  : {lesson.media_path}")
    print(f"  单句音频  : {C.CLIP_DIR / lesson.lesson_id}")
    print("\n  前 10 句：")
    for s in lesson.sentences[:10]:
        print(f"    {s.index:3d}. [{s.start:7.2f}-{s.end:7.2f}] "
              f"({s.duration:5.2f}s) {s.text[:70]}")


def cmd_build_local(args) -> int:
    b = Builder(progress=_bar)
    t0 = time.time()
    lesson = b.from_local(args.path, title=args.title)
    sys.stderr.write("\n")
    _report(lesson, time.time() - t0)

    if args.out:
        Path(args.out).write_text(
            json.dumps(lesson.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n  manifest -> {args.out}")
    return 0


def cmd_build_url(args) -> int:
    b = Builder(progress=_bar)
    t0 = time.time()
    lesson = b.from_url(args.url, keep_video=args.keep_video)
    sys.stderr.write("\n")
    _report(lesson, time.time() - t0)

    if args.out:
        Path(args.out).write_text(
            json.dumps(lesson.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n  manifest -> {args.out}")
    return 0


def cmd_resplit(args) -> int:
    """Re-run only the sentence splitting and clip cutting for a lesson.

    Uses the cached ASR word timings, so this takes seconds instead of the
    minutes a full re-import would need.  Useful after tuning the splitter:
    the audio does not have to be transcribed again.
    """
    import json
    import shutil

    lesson_id = args.lesson_id
    work = C.MEDIA_DIR / lesson_id
    manifest = work / "lesson.json"
    if not manifest.exists():
        print(f"找不到课程：{lesson_id}", file=sys.stderr)
        return 1

    audio = work / "audio16k.wav"
    if not audio.exists():
        print(f"缺少音频文件：{audio}", file=sys.stderr)
        return 1

    data = json.loads(manifest.read_text(encoding="utf-8"))
    old_sentences = data["sentences"]
    print(f"课程：{data['title'][:56]}")
    print(f"原句数：{len(old_sentences)}   切句来源：{data['meta'].get('sentenceSource')}")

    from . import asr as asr_mod
    from .sentences import split_words, to_srt, to_vtt

    if args.max_gap is not None:
        C.SENT_MAX_GAP = args.max_gap
    if args.punct_gap is not None:
        C.SENT_PUNCT_GAP = args.punct_gap
    print(f"参数：max_gap={C.SENT_MAX_GAP}  punct_gap={C.SENT_PUNCT_GAP}  "
          f"max_chars={C.SENT_MAX_CHARS}")

    print("读取缓存的识别结果…")
    words, meta = asr_mod.transcribe_words(audio, progress=_bar)
    sys.stderr.write("\n")
    if meta.get("cached"):
        print(f"  ✓ 缓存命中，跳过识别（{len(words)} 个词）")
    else:
        print(f"  未命中缓存，本次重新识别了 {len(words)} 个词")

    sentences = split_words(words)
    print(f"新句数：{len(sentences)}")

    # Remap progress by timestamp so time spent studying is not thrown away.
    kept = 0
    if not args.drop_progress:
        with store.db() as conn:
            rows = conn.execute(
                "SELECT sentence_index, plays, loops, completed FROM progress "
                "WHERE lesson_id = ?", (lesson_id,)).fetchall()
            state = conn.execute(
                "SELECT last_sentence, position FROM lesson_state "
                "WHERE lesson_id = ?", (lesson_id,)).fetchone()
            conn.execute("DELETE FROM progress WHERE lesson_id = ?", (lesson_id,))
            conn.execute("DELETE FROM lesson_state WHERE lesson_id = ?", (lesson_id,))
            for r in rows:
                idx = r["sentence_index"]
                if not (1 <= idx <= len(old_sentences)):
                    continue
                mid = (old_sentences[idx - 1]["start"]
                       + old_sentences[idx - 1]["end"]) / 2
                new_i = min(range(len(sentences)),
                            key=lambda i: abs((sentences[i].start
                                               + sentences[i].end) / 2 - mid)) + 1
                conn.execute(
                    "INSERT INTO progress (lesson_id, sentence_index, plays, "
                    "loops, last_played, completed) VALUES (?,?,?,?,?,1) "
                    "ON CONFLICT(lesson_id, sentence_index) DO UPDATE SET "
                    "plays = plays + excluded.plays, loops = loops + excluded.loops",
                    (lesson_id, new_i, r["plays"], r["loops"], time.time()))
                kept += 1
            if state:
                conn.execute(
                    "INSERT INTO lesson_state (lesson_id, last_sentence, position, "
                    "updated_at) VALUES (?,?,?,?)",
                    (lesson_id, state["last_sentence"], state["position"],
                     time.time()))
        print(f"已迁移 {kept} 条学习进度记录（按时间戳对应到新句子）")

    print(f"切分 {len(sentences)} 个句子音频…")
    clips_dir = C.CLIP_DIR / lesson_id
    shutil.rmtree(clips_dir, ignore_errors=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    made = ffmpeg_io.cut_clips(
        audio, clips_dir, [(s.start, s.end, s.text) for s in sentences])
    for s, p in zip(sentences, made):
        s.audio = f"seg_{s.index:04d}.mp3"

    data["sentences"] = [s.to_dict() for s in sentences]
    data["meta"]["sentenceSource"] = data["meta"].get("sentenceSource", "asr") + "+resplit"
    data["meta"]["sentenceCount"] = len(sentences)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    (work / "sentences.srt").write_text(to_srt(sentences), encoding="utf-8")
    (work / "sentences.vtt").write_text(to_vtt(sentences), encoding="utf-8")

    # Keep the DB row in step with the rewritten manifest, otherwise the
    # library card keeps showing the old sentence count.
    store.sync_lesson_from_manifest(lesson_id)

    durs = [s.duration for s in sentences]
    import statistics
    print(f"\n完成：{len(sentences)} 句，{len(made)} 个音频片段")
    print(f"  时长中位数 {statistics.median(durs):.2f}s，"
          f"最短 {min(durs):.2f}s，最长 {max(durs):.2f}s")
    print(f"  字幕文件已更新：{work / 'sentences.srt'}")
    return 0


def cmd_make_test_audio(args) -> int:
    """Synthesize speech-like audio segments joined by silence.

    Used to exercise the full pipeline when no speech sample is available.
    Tones are not speech, so ASR will not produce words - this only proves the
    plumbing (extract -> slice) works.
    """
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for i in range(4):
        freq = 300 + i * 120
        parts.append(f"sine=frequency={freq}:duration=1.2")
        parts.append("anullsrc=r=16000:cl=mono:d=0.6")
    fc = "".join(f"[{i}:a]" for i in range(len(parts)))
    cmd = [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y"]
    for p in parts:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex", f"{fc}concat=n={len(parts)}:v=0:a=1[out]",
            "-map", "[out]", "-ac", "1", "-ar", "16000", str(out)]
    ffmpeg_io._run(cmd)
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


def cmd_list(_args) -> int:
    """List built lessons found on disk."""
    if not C.MEDIA_DIR.exists():
        print("(no lessons yet)")
        return 0
    found = 0
    for d in sorted(C.MEDIA_DIR.iterdir()):
        manifest = d / "lesson.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        clips = C.CLIP_DIR / data["id"]
        n_clips = len(list(clips.glob("*.mp3"))) if clips.exists() else 0
        print(f"  {data['id']}")
        print(f"    {data['title'][:60]}")
        print(f"    {len(data['sentences'])} 句 / {n_clips} 个音频片段 / "
              f"{data['duration']:.0f}s")
        found += 1
    if not found:
        print("(no lessons yet)")
    return 0


def main(argv=None) -> int:
    C.apply_env()
    p = argparse.ArgumentParser(
        prog="listenup", description="英语听力训练软件 · 导入管线")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="检查工具链")
    c.set_defaults(func=cmd_check)

    c = sub.add_parser("subs", help="列出链接可用的字幕")
    c.add_argument("url")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_subs)

    c = sub.add_parser("build-local", help="从本地视频/音频文件构建课程")
    c.add_argument("path")
    c.add_argument("--title", default=None)
    c.add_argument("--out", default=None, help="写出 manifest JSON")
    c.set_defaults(func=cmd_build_local)

    c = sub.add_parser("build-url", help="从视频链接构建课程")
    c.add_argument("url")
    c.add_argument("--keep-video", action="store_true",
                   help="保留下载视频轨（默认只取音频）")
    c.add_argument("--out", default=None)
    c.set_defaults(func=cmd_build_url)

    c = sub.add_parser("make-test-audio", help="生成测试用音频")
    c.add_argument("out")
    c.set_defaults(func=cmd_make_test_audio)

    c = sub.add_parser("resplit",
                       help="只重新切句（复用已缓存的识别结果，秒级完成）")
    c.add_argument("lesson_id")
    c.add_argument("--max-gap", type=float, default=None,
                   help="停顿超过多少秒算句子边界（默认 1.8）")
    c.add_argument("--punct-gap", type=float, default=None,
                   help="标点后至少要有多长停顿才可信（默认 0.15）")
    c.add_argument("--drop-progress", action="store_true",
                   help="不迁移学习进度（默认会按时间戳迁移）")
    c.set_defaults(func=cmd_resplit)

    c = sub.add_parser("list", help="列出已构建的课程")
    c.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
