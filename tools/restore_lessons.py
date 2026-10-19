"""把磁盘上存在但数据库里没登记的课程重新登记回去。

## 什么时候需要

导入任务其实跑完了（lesson.json、音频、句子片段都在），
但数据库里的行被删掉了——比如手工清理时误删。

这时界面上看不到这门课，但文件全在。这个脚本扫描 data/media/*/lesson.json，
把缺失的课程按原始 manifest 重新写回 lessons 表。

不重新识别、不重新下载，只补一行数据库记录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "pydeps"))
sys.path.insert(0, str(ROOT / "app"))

from listenup import config as C  # noqa: E402

C.apply_env()

from listenup import sentences as SM, store  # noqa: E402
from listenup.pipeline import Lesson  # noqa: E402


class _Sentence:
    """最小还原：manifest 里的字段直接塞进来。"""

    def __init__(self, d: dict):
        self.index = int(d.get("index") or 0)
        self.start = float(d.get("start") or 0.0)
        self.end = float(d.get("end") or 0.0)
        self.text = d.get("text") or ""
        self.translation = d.get("translation") or ""
        self.words = d.get("words") or []
        self.audio = d.get("audio")
        self.source = d.get("source") or "asr"
        self.quality = float(d.get("quality") or 1.0)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.end - self.start, 3),
            "text": self.text,
            "translation": self.translation,
            "words": self.words,
            "audio": self.audio,
            "source": self.source,
            "quality": self.quality,
        }


class _Lesson:
    def __init__(self, d: dict, media_path: Path):
        self.lesson_id = d["id"]
        self.title = d.get("title") or d["id"]
        self.duration = float(d.get("duration") or 0.0)
        self.media_path = media_path
        self.has_video = bool(d.get("hasVideo"))
        self.sentences = [_Sentence(s) for s in d.get("sentences", [])]
        self.meta = d.get("meta") or {}

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


def main() -> int:
    import sqlite3

    con = sqlite3.connect(str(C.DB_PATH), timeout=15)
    known = {r[0] for r in con.execute("SELECT id FROM lessons")}
    owner = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    owner_id = owner[0] if owner else None
    con.close()

    restored = 0
    skipped = 0
    for d in sorted(C.MEDIA_DIR.iterdir()) if C.MEDIA_DIR.exists() else []:
        if not d.is_dir():
            continue
        mp = d / "lesson.json"
        if not mp.exists():
            continue
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        lid = data.get("id") or d.name
        if lid in known:
            skipped += 1
            continue

        # 找一个存在的媒体文件（wav/mp4 等）
        media = None
        for cand in d.iterdir():
            if cand.is_file() and cand.suffix.lower() in (
                    ".wav", ".mp3", ".m4a", ".mp4", ".mkv", ".webm", ".flv"):
                media = cand
                break
        if media is None:
            print(f"  {lid[:34]:36} 找不到媒体文件，跳过")
            continue

        lesson = _Lesson(data, media)
        store.upsert_lesson(lesson, owner_id)
        n_zh = sum(1 for s in lesson.sentences if s.translation.strip())
        print(f"  ✓ {lid[:34]:36} {len(lesson.sentences):>3} 句  译文 {n_zh}")
        restored += 1

    print()
    print(f"  恢复 {restored} 门，跳过（已存在）{skipped} 门")
    return 0


if __name__ == "__main__":
    sys.exit(main())
