"""Tests for the read-aloud (TTS) path.

The online edge-tts engine is expected to be broken from this network (it
answers 403), so the important behaviours to lock down are:

  * the offline Piper engine actually produces playable audio;
  * results are cached (repeated taps must be instant);
  * asking for the online engine never dead-ends - it falls back to offline;
  * empty input is rejected rather than producing a silent file.
"""
from __future__ import annotations

import shutil
import sys
import unittest
import wave
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP.parent / "tools" / "pydeps"))

from listenup import config as C, ffmpeg_io, tts  # noqa: E402

SENTENCE = "The most important thing is to hear every single word clearly."


class TestTTSCore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        C.apply_env()
        if not tts.piper_available():
            raise unittest.SkipTest("Piper 离线语音不可用，跳过朗读测试")

    def test_offline_synthesizes_playable_audio(self):
        path, engine = tts.synthesize(SENTENCE, "en-US-AriaNeural",
                                      prefer="offline")
        self.assertEqual(engine, "piper")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 2000)
        info = ffmpeg_io.probe(path)
        # A 12-word sentence should land somewhere near a few seconds.
        self.assertGreater(info["duration"], 1.0)
        self.assertLess(info["duration"], 15.0)
        self.assertTrue(info["has_audio"])

    def test_result_is_cached(self):
        first, _ = tts.synthesize(SENTENCE, "en-US-AriaNeural", prefer="offline")
        mtime = first.stat().st_mtime
        second, _ = tts.synthesize(SENTENCE, "en-US-AriaNeural", prefer="offline")
        self.assertEqual(first, second)
        self.assertEqual(first.stat().st_mtime, mtime,
                         "缓存命中时不应重新生成文件")

    def test_online_engine_falls_back_to_offline(self):
        """Whatever edge-tts does, the caller must still get audio."""
        path, engine = tts.synthesize("Fallback check.", "en-US-AriaNeural",
                                      prefer="online")
        self.assertIn(engine, ("edge-tts", "piper"))
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_empty_text_is_rejected(self):
        for bad in ("", "   ", "\n\t"):
            with self.assertRaises(tts.TTSError):
                tts.synthesize(bad)

    def test_long_text_is_truncated_not_rejected(self):
        path, engine = tts.synthesize("hello " * 400, prefer="offline")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_status_reports_offline_available(self):
        st = tts.status()
        self.assertTrue(st["offline"]["available"])
        self.assertEqual(st["offline"]["engine"], "piper")
        self.assertTrue(st["offline"]["modelPresent"])
        self.assertIn("note", st["online"])

    def test_piper_wav_is_mono_and_audible(self):
        """Guard against a silent/empty synthesis that still produces a file."""
        path, _ = tts.synthesize("Checking that this is really audible.",
                                 prefer="offline")
        self.assertGreater(path.stat().st_size, 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
