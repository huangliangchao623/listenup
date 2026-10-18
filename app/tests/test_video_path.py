"""End-to-end check of the local-video path, including the video track.

Everything tested so far used audio-only input.  The study page has a distinct
code path for lessons that carry video (`hasVideo`), and that path also drives
the `<video>` element's range requests, so it needs its own verification.

Builds a small real mp4 (tone audio + a colour video track), runs the pipeline
on it, and asserts the lesson is marked as having video and that ffprobe can
still see the video stream in the stored media.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP.parent / "tools" / "pydeps"))

from listenup import asr, config as C, ffmpeg_io, store  # noqa: E402
from listenup import pipeline as P  # noqa: E402
from listenup.sentences import Word  # noqa: E402

WORK = C.TOOLS / "_video_test"

LINES = [
    "This is the first line of the video.",
    "And here is a second line for testing.",
]
TONE = 1.8
SIL = 0.9


def build_video(path: Path) -> list[Word]:
    """Render a real mp4 with a video track plus tone audio, and known words.

    Input 0 is the video; the lavfi audio sources are inputs 1..N, so the
    concat labels must be offset by one.
    """
    words: list[Word] = []
    lavfi: list[str] = []
    labels: list[str] = []
    t = 0.0
    for i, line in enumerate(LINES):
        lavfi.append(f"anullsrc=r=16000:cl=mono:d={SIL}")
        labels.append(f"[{len(lavfi)}:a]")          # +1 for the video input
        t += SIL
        lavfi.append(f"sine=frequency={320 + i * 180}:duration={TONE}")
        labels.append(f"[{len(lavfi)}:a]")
        toks = line.split()
        step = TONE / len(toks)
        for k, tok in enumerate(toks):
            words.append(Word(text=("" if k == 0 else " ") + tok,
                              start=t + k * step, end=t + (k + 1) * step,
                              prob=0.9))
        t += TONE

    cmd = [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=navy:s=320x180:d={t:.3f}"]
    for p in lavfi:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex",
            "".join(labels) + f"concat=n={len(lavfi)}:v=0:a=1[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "1", "-ar", "16000", "-shortest",
            str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return words


class TestLocalVideoPath(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        C.apply_env()
        WORK.mkdir(parents=True, exist_ok=True)
        cls._orig_db = C.DB_PATH
        C.DB_PATH = WORK / "video_test.sqlite3"
        store.init()

        cls.src = WORK / "clip.mp4"
        cls.words = build_video(cls.src)

        cls._orig_asr = asr.transcribe_words
        asr.transcribe_words = staticmethod(
            lambda audio_path, language="en", model_name=None, progress=None:
            (cls.words, {"model": "injected"})
        )
        cls.lesson = P.Builder(progress=lambda f, m: None).from_local(
            cls.src, title="视频路径测试")

    @classmethod
    def tearDownClass(cls):
        asr.transcribe_words = cls._orig_asr
        C.DB_PATH = cls._orig_db
        shutil.rmtree(C.MEDIA_DIR / cls.lesson.lesson_id, ignore_errors=True)
        shutil.rmtree(C.CLIP_DIR / cls.lesson.lesson_id, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)

    def test_source_file_really_has_a_video_stream(self):
        info = ffmpeg_io.probe(self.src)
        self.assertTrue(info["has_video"], "测试素材本身必须有视频轨")
        self.assertTrue(info["has_audio"])

    def test_lesson_is_flagged_as_video(self):
        self.assertTrue(self.lesson.has_video,
                        "带视频的素材必须标记 hasVideo，学习页才会渲染播放器")

    def test_stored_media_keeps_the_video_stream(self):
        """The study page plays S.lesson.media, so the copy must keep video."""
        info = ffmpeg_io.probe(self.lesson.media_path)
        self.assertTrue(info["has_video"])
        self.assertTrue(info["has_audio"])

    def test_video_dimensions_preserved(self):
        info = ffmpeg_io.probe(self.lesson.media_path)
        self.assertEqual(info["width"], 320)
        self.assertEqual(info["height"], 180)

    def test_sentences_and_clips_still_produced(self):
        self.assertEqual(len(self.lesson.sentences), len(LINES))
        for s in self.lesson.sentences:
            clip = C.CLIP_DIR / self.lesson.lesson_id / s.audio
            self.assertTrue(clip.exists(), f"缺少片段 {clip}")
            info = ffmpeg_io.probe(clip)
            self.assertAlmostEqual(info["duration"], s.duration, delta=0.15)

    def test_duration_matches_video(self):
        info = ffmpeg_io.probe(self.lesson.media_path)
        self.assertAlmostEqual(self.lesson.duration, info["duration"], delta=0.3)

    def test_payload_marks_has_video_for_the_ui(self):
        store.upsert_lesson(self.lesson)
        data = store.get_lesson(self.lesson.lesson_id)
        self.assertTrue(data["hasVideo"])
        self.assertTrue(data["media"].startswith("/media/"))
        self.assertEqual(len(data["sentences"]), len(LINES))

    def test_listed_lesson_reports_video(self):
        store.upsert_lesson(self.lesson)
        rows = [l for l in store.list_lessons() if l["id"] == self.lesson.lesson_id]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["hasVideo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
