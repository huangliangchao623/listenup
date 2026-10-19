"""给已有课程补上中文字幕（不用重新导入）。

## 为什么需要

翻译功能是后加的。之前导入的课程，lesson.json 里没有 translation 字段。
重新导入要重新下载 + 重新识别，20 多分钟的视频很费时间。

这个脚本只做一件事：
    对每门 B 站课程 → 取中文字幕 → 按时间重叠对齐 → 写回 lesson.json

媒体文件、句子切分、音频片段全部不动。

## 安全性

每门课先备份成 lesson.json.bak，失败可还原。
"""
from __future__ import annotations

import json
import shutil
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "pydeps"))
sys.path.insert(0, str(ROOT / "app"))

from listenup import config as C  # noqa: E402

C.apply_env()

from listenup import captions, sources  # noqa: E402
from listenup.sentences import Sentence, attach_translations  # noqa: E402

MEDIA = ROOT / "data" / "media"
CACHE = ROOT / "data" / "subs"


def bvid_of(lesson_dir: Path) -> str | None:
    """课程目录名形如 BV1oXN76tETL_p1_6ad5d987，取前面的 BV 号。"""
    name = lesson_dir.name
    if name.startswith("BV") and "_" in name:
        return name.split("_")[0]
    return None


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    done = 0
    skipped = 0

    for d in sorted(MEDIA.iterdir()):
        if not d.is_dir():
            continue
        mp = d / "lesson.json"
        if not mp.exists():
            continue

        bvid = bvid_of(d)
        if not bvid:
            print(f"  {d.name[:32]:34} 不是 B 站课程，跳过")
            skipped += 1
            continue

        data = json.loads(mp.read_text(encoding="utf-8"))
        sents_raw = data.get("sentences") or []
        if not sents_raw:
            print(f"  {d.name[:32]:34} 没有句子，跳过")
            skipped += 1
            continue

        already = sum(1 for s in sents_raw if (s.get("translation") or "").strip())
        if already == len(sents_raw):
            print(f"  {d.name[:32]:34} 已有完整译文，跳过")
            skipped += 1
            continue

        # 抓中文字幕。
        #
        # B 站 AI 字幕接口有个坑：每次请求返回的 body 长度不一样
        # （实测 11KB / 59KB / 133KB 都出现过），像是分片返回或者
        # 边生成边吐。所以要下几次，保留「时间轴覆盖最广」的那份。
        url = f"https://www.bilibili.com/video/{bvid}"
        want_end = float(data.get("duration") or 0) or 1e9

        best_cues: list = []
        best_span = 0.0
        got_track = None
        for attempt in range(6):
            try:
                tracks = sources.bilibili_subtitles(url)
            except Exception:  # noqa: BLE001
                tracks = []
            zh = sources.pick_chinese_track(tracks)
            if not zh:
                time.sleep(1.5)
                continue
            got_track = zh
            tmp = CACHE / f"{d.name}.try{attempt}.json"
            try:
                sources.download_bilibili_subtitle(zh, tmp)
                cues_try = captions.parse_any(tmp)
            except Exception:  # noqa: BLE001
                cues_try = []
            if cues_try:
                span = cues_try[-1].end
                if span > best_span:
                    best_span = span
                    best_cues = cues_try
            if best_span >= want_end * 0.92:
                break                      # 已经基本完整，不用再试
            time.sleep(1.5)

        # 清掉临时尝试文件
        for p in CACHE.glob(f"{d.name}.try*.json"):
            try: p.unlink()
            except Exception: pass

        if not got_track:
            print(f"  {d.name[:32]:34} 该视频没有中文字幕")
            skipped += 1
            continue

        print(f"  {d.name[:32]:34} 字幕覆盖 {best_span:.0f}s / {want_end:.0f}s")
        cues = best_cues

        if not cues:
            print(f"  {d.name[:32]:34} 字幕为空")
            skipped += 1
            continue

        # 对齐
        sents = [Sentence(index=int(s["index"]), start=float(s["start"]),
                          end=float(s["end"]), text=s["text"])
                 for s in sents_raw]
        n = attach_translations(sents, cues)
        if not n:
            print(f"  {d.name[:32]:34} 时间轴对不上，跳过")
            skipped += 1
            continue

        # 备份后写回（只改 translation 字段，其它原样保留）
        backup = mp.with_suffix(".json.bak")
        if not backup.exists():
            shutil.copy2(mp, backup)
        for raw, s in zip(sents_raw, sents):
            raw["translation"] = s.translation
        mp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"  {d.name[:32]:34} ✓ {n}/{len(sents)} 句已补译文 "
              f"({len(cues)} 条中文字幕)")
        done += 1

    print()
    print(f"  完成 {done} 门课程，跳过 {skipped} 门")
    return 0


if __name__ == "__main__":
    sys.exit(main())
