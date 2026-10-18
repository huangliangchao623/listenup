"""Integration test: exercise the whole pipeline without needing real speech.

Real ASR output is replaced by injected word timings, so the parts that the unit
tests cannot reach are still verified end to end:

  synthetic audio -> ffmpeg extract -> (injected ASR words) -> sentence split
  -> per-sentence ffmpeg clips -> manifest + progress rows

The audio is a sequence of distinct tones, so a mis-aligned cut is audibly and
numerically detectable (each clip's duration must match its sentence).
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

WORK = C.TOOLS / "_integration"

# Three spoken "sentences", each preceded by silence, so the splitter sees
# clear pauses.  Timings are the ground truth the clips must match.
SCRIPT = [
    ("Hello and welcome to my channel.", 1.0),
    ("Today we talk about listening practice.", 1.0),
    ("Click any sentence to hear it again.", 1.0),
]
TONE_SECONDS = 2.0
SILENCE_SECONDS = 0.9


def build_audio(path: Path) -> list[Word]:
    """Render tones separated by silence and return the matching word timings.

    Each script sentence occupies one TONE block; its words are spread evenly
    across that block so the expected clip boundaries are known exactly.
    """
    parts, filters = [], []
    words: list[Word] = []
    t = 0.0
    for i, (text, _pad) in enumerate(SCRIPT):
        # silence
        parts.append("anullsrc=r=16000:cl=mono:d=%.3f" % SILENCE_SECONDS)
        filters.append(f"[{len(parts) - 1}:a]")
        t += SILENCE_SECONDS

        # tone block = the "sentence"
        parts.append("sine=frequency=%d:duration=%.3f" % (300 + i * 200, TONE_SECONDS))
        filters.append(f"[{len(parts) - 1}:a]")

        toks = text.split()
        step = TONE_SECONDS / len(toks)
        for k, tok in enumerate(toks):
            words.append(Word(
                text=("" if k == 0 else " ") + tok,
                start=t + k * step,
                end=t + (k + 1) * step,
                prob=0.95,
            ))
        t += TONE_SECONDS

    cmd = [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y"]
    for p in parts:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex",
            "".join(filters) + f"concat=n={len(parts)}:v=0:a=1[out]",
            "-map", "[out]", "-ac", "1", "-ar", "16000", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-500:]}")
    return words


class TestPipelineIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        C.apply_env()
        WORK.mkdir(parents=True, exist_ok=True)
        # Use a throwaway database.  Writing these throwaway lessons into the
        # real library would leave rows whose media is deleted moments later.
        cls._orig_db = C.DB_PATH
        C.DB_PATH = WORK / "test_library.sqlite3"
        store.init()

        cls.src = WORK / "synthetic_speech.wav"
        cls.words = build_audio(cls.src)
        # Inject the words instead of running the model: this test is about the
        # pipeline, not about ASR accuracy.
        cls._orig = asr.transcribe_words
        asr.transcribe_words = staticmethod(
            lambda audio_path, language="en", model_name=None, progress=None:
            (cls.words, {"model": "injected", "words": len(cls.words)})
        )
        cls.lesson = P.Builder(progress=lambda f, m: None).from_local(
            cls.src, title="集成测试")

    @classmethod
    def tearDownClass(cls):
        asr.transcribe_words = cls._orig
        C.DB_PATH = cls._orig_db
        shutil.rmtree(C.MEDIA_DIR / cls.lesson.lesson_id, ignore_errors=True)
        shutil.rmtree(C.CLIP_DIR / cls.lesson.lesson_id, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)

    def test_sentence_count_matches_script(self):
        self.assertEqual(len(self.lesson.sentences), len(SCRIPT))

    def test_sentence_text_is_reconstructed(self):
        for sent, (expected, _pad) in zip(self.lesson.sentences, SCRIPT):
            self.assertEqual(sent.text, expected)

    def test_sentences_are_ordered_and_non_overlapping(self):
        for a, b in zip(self.lesson.sentences, self.lesson.sentences[1:]):
            self.assertLess(a.start, a.end)
            self.assertLessEqual(a.end, b.start)

    def test_every_sentence_has_a_clip(self):
        for s in self.lesson.sentences:
            self.assertIsNotNone(s.audio, f"句子 {s.index} 没有音频文件")
            clip = C.CLIP_DIR / self.lesson.lesson_id / s.audio
            self.assertTrue(clip.exists(), f"缺失 {clip}")
            self.assertGreater(clip.stat().st_size, 1000)

    def test_clip_durations_match_sentence_spans(self):
        """The real point of the pipeline: clip length == sentence length."""
        for s in self.lesson.sentences:
            clip = C.CLIP_DIR / self.lesson.lesson_id / s.audio
            info = ffmpeg_io.probe(clip)
            # mp3 frame padding at 24 kHz allows roughly 30 ms of slack.
            self.assertAlmostEqual(info["duration"], s.duration, delta=0.15,
                                   msg=f"句 {s.index} 时长不符")

    def test_manifest_written_and_consistent(self):
        manifest = C.MEDIA_DIR / self.lesson.lesson_id / "lesson.json"
        self.assertTrue(manifest.exists())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(data["sentences"]), len(SCRIPT))
        for s in data["sentences"]:
            self.assertIsNotNone(s["audio"])
            self.assertGreater(s["end"], s["start"])

    def test_srt_written(self):
        srt = C.MEDIA_DIR / self.lesson.lesson_id / "sentences.srt"
        self.assertTrue(srt.exists())
        text = srt.read_text(encoding="utf-8")
        self.assertIn("-->", text)
        self.assertIn("Hello and welcome", text)

    def test_store_records_lesson(self):
        store.upsert_lesson(self.lesson)
        found = [l for l in store.list_lessons() if l["id"] == self.lesson.lesson_id]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["sentenceCount"], len(SCRIPT))

    def test_lesson_json_roundtrip_via_store(self):
        store.upsert_lesson(self.lesson)
        data = store.get_lesson(self.lesson.lesson_id)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["sentences"]), len(SCRIPT))
        # get_lesson must attach playable URLs
        for s in data["sentences"]:
            self.assertTrue(s["audioUrl"].startswith("/clips/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
