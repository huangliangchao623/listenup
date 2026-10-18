"""Unit tests for the sentence splitter.

These run offline with synthetic word timings, so the most important logic in
the product is verifiable without audio or a model.

Run:  python -m unittest discover -s app/tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from listenup.sentences import (  # noqa: E402
    Cue, Word, is_noise, merge_cues, split_words, to_srt, to_vtt,
)


def words_from(text: str, gap: float = 0.10, wdur: float = 0.25,
               start: float = 0.0, sent_gap: float = 0.30):
    """Build synthetic word timings from a plain string.

    Whisper emits each token with its leading whitespace already attached
    ("Hello", " there", "."), and `split_words` concatenates tokens verbatim.
    The helper reproduces that shape exactly, so the tests exercise the real
    input format rather than a cleaned-up one.

    Timings mirror natural speech: words run into each other (`gap`), while a
    sentence end is followed by a real pause (`sent_gap`).  Fixtures using tiny
    gaps everywhere would be unrealistic - measured on real podcast audio, a
    sentence boundary is ~0.3-0.7 s of silence, and the splitter requires a
    pause to corroborate sentence-final punctuation.
    """
    toks = text.split()
    out, t = [], start
    for i, tok in enumerate(toks):
        out.append(Word(text=("" if i == 0 else " ") + tok,
                        start=t, end=t + wdur, prob=0.9))
        t += wdur
        # A longer pause follows sentence-final punctuation.
        punct = tok.rstrip()[-1:] in ".!?…。！？"
        t += sent_gap if punct else gap
    return out


class TestSplitWords(unittest.TestCase):

    def test_splits_on_sentence_final_punctuation(self):
        ws = words_from("Hello there. How are you? I am fine!")
        s = split_words(ws)
        self.assertEqual(len(s), 3)
        self.assertEqual(s[0].text, "Hello there.")
        self.assertEqual(s[1].text, "How are you?")
        self.assertEqual(s[2].text, "I am fine!")

    def test_splits_on_long_pause_without_punctuation(self):
        # A long silence (> SENT_MAX_GAP) is an utterance boundary even with no
        # punctuation at all.  2.5 s is a real pause, unlike the short breaths
        # speakers take mid-sentence.
        a = words_from("we are going to talk", gap=0.05)
        b = words_from("about listening practice", gap=0.05,
                       start=a[-1].end + 2.5)
        s = split_words(a + b)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0].text, "we are going to talk")
        self.assertEqual(s[1].text, "about listening practice")

    def test_short_pause_does_not_split_without_punctuation(self):
        # A 0.8 s breath in the middle of a clause must NOT break the sentence.
        # Real podcast audio showed mid-sentence pauses of 0.7 s, so treating
        # those as sentence ends slices speech into unlearnable fragments.
        a = words_from("we are going to talk", gap=0.05)
        b = words_from("about listening practice", gap=0.05,
                       start=a[-1].end + 0.8)
        s = split_words(a + b)
        self.assertEqual(len(s), 1,
                         "句中短暂停顿不应断句")

    def test_does_not_split_on_abbreviation(self):
        # "Dr." must not end a sentence.
        ws = words_from("I saw Dr. Smith yesterday. He was kind.")
        s = split_words(ws)
        self.assertEqual(len(s), 2)
        self.assertIn("Dr. Smith yesterday.", s[0].text)

    def test_does_not_split_on_initial(self):
        ws = words_from("Her name is J. K. Rowling. She writes books.")
        s = split_words(ws)
        self.assertEqual(len(s), 2)
        self.assertIn("J. K. Rowling.", s[0].text)

    def test_merges_fragment_into_previous(self):
        # A 1-word fragment with no punctuation should not stand alone.
        ws = words_from("I really like it. Yes. And that is all.")
        s = split_words(ws)
        joined = " ".join(x.text for x in s)
        self.assertIn("Yes.", joined)

    def test_force_break_on_long_run(self):
        # 40 short words with no punctuation: must break up rather than
        # produce one unusable 10-second-plus unit.
        ws = words_from(" ".join(["word"] * 40), gap=0.05)
        s = split_words(ws)
        self.assertGreater(len(s), 1)
        for x in s:
            self.assertLessEqual(len(x.text), 120)

    def test_timings_are_monotonic_and_non_overlapping(self):
        ws = words_from("One thing. Then another thing. And a third one here.")
        s = split_words(ws)
        for a, b in zip(s, s[1:]):
            self.assertLessEqual(a.end, b.start)
            self.assertLess(a.start, a.end)

    def test_empty_input(self):
        self.assertEqual(split_words([]), [])

    def test_indexes_are_sequential(self):
        ws = words_from("Aaa bbb. Ccc ddd. Eee fff. Ggg hhh.")
        s = split_words(ws)
        self.assertEqual([x.index for x in s], list(range(1, len(s) + 1)))


class TestForcedCutQuality(unittest.TestCase):
    """A length-forced cut must not slice an English clause in half.

    Real podcast material exposed this: a 110-character limit cut mid-clause and
    left a fragment like "to start with." stranded as its own clickable unit.
    """

    def test_forced_cut_prefers_secondary_punctuation(self):
        # Long enough to exceed SENT_MAX_CHARS, with a comma roughly two thirds
        # in and no full stop.  The break should land on the comma, not on the
        # character limit.
        text = ("I started learning that language when I was twenty two years old, "
                "so I have been studying it on and off for quite a long time now "
                "and I still find new things to learn every single week")
        ws = words_from(text, gap=0.05)
        s = split_words(ws)
        self.assertGreater(len(s), 1, "过长的句子必须被切开")
        self.assertTrue(s[0].text.endswith(","),
                        f"应切在逗号处，实际切在：{s[0].text!r}")
        joined = " ".join(x.text for x in s)
        self.assertIn("twenty two years old", joined)
        self.assertIn("every single week", joined)

    def test_forced_cut_does_not_strand_a_tiny_fragment(self):
        text = ("This is a fairly long stretch of speech without any full stop "
                "in it at all which means the splitter has to cut somewhere "
                "and it should not leave a two word orphan behind")
        ws = words_from(text, gap=0.05)
        s = split_words(ws)
        for x in s[:-1]:
            self.assertGreater(len(x.text.split()), 2,
                               f"产生了碎片句：{x.text!r}")

    def test_forced_cut_fragment_merges_back(self):
        """A short piece produced by a forced cut may carry a period from the
        original sentence, but it is still a fragment and must merge back."""
        from listenup.sentences import Sentence, _merge_short

        a = Sentence(index=1, start=0.0, end=9.0,
                     text="Something long enough to be its own unit here",
                     words=[{"w": "x"}] * 8, natural_break=False, forced_cut=True)
        b = Sentence(index=2, start=9.1, end=9.5, text="you don't care.",
                     words=[{"w": "x"}] * 3, natural_break=False, forced_cut=True)
        merged = _merge_short([a, b])
        self.assertEqual(len(merged), 1, "强制切出的碎片应并回上一句")
        self.assertIn("you don't care.", merged[0].text)

    def test_natural_short_sentence_still_stands_alone(self):
        """A genuinely complete short sentence must NOT be merged away."""
        from listenup.sentences import Sentence, _merge_short

        a = Sentence(index=1, start=0.0, end=6.0,
                     text="I really like listening to podcasts every day",
                     words=[{"w": "x"}] * 8, natural_break=True, forced_cut=False)
        b = Sentence(index=2, start=7.5, end=8.2, text="Yes.",
                     words=[{"w": "x"}] * 1, natural_break=True, forced_cut=False)
        merged = _merge_short([a, b])
        self.assertEqual(len(merged), 2, "完整的短句不应被合并")

    def test_output_stays_within_limits(self):
        text = " ".join(["word"] * 60)
        ws = words_from(text, gap=0.05)
        s = split_words(ws)
        for x in s:
            self.assertLessEqual(len(x.text), 130, f"仍有过长片段：{x.text[:40]}")


class TestNoise(unittest.TestCase):

    def test_detects_caption_noise(self):
        for t in ["[Music]", "(laughs)", "♪♪♪", "...", "", "   ", "1:23"]:
            self.assertTrue(is_noise(t), msg=f"should be noise: {t!r}")

    def test_real_speech_is_not_noise(self):
        for t in ["Hello world.", "I am here", "Yeah"]:
            self.assertFalse(is_noise(t), msg=f"should not be noise: {t!r}")

    def test_noise_cues_are_dropped(self):
        cues = [
            Cue(0.0, 1.0, "[Music]"),
            Cue(1.0, 3.0, "Hello and welcome to my vlog."),
            Cue(3.0, 4.0, "(laughs)"),
        ]
        s = merge_cues(cues)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0].text, "Hello and welcome to my vlog.")


class TestMergeCues(unittest.TestCase):
    """Captions break mid-sentence; merging must reconstruct real sentences."""

    def test_merges_lowercase_continuation(self):
        cues = [
            Cue(0.0, 2.0, "I want to tell you"),
            Cue(2.0, 4.5, "about my day."),
        ]
        s = merge_cues(cues)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0].text, "I want to tell you about my day.")

    def test_breaks_on_terminal_punctuation(self):
        cues = [
            Cue(0.0, 2.0, "This is the first sentence."),
            Cue(2.0, 4.0, "And this is the second one."),
        ]
        s = merge_cues(cues)
        self.assertEqual(len(s), 2)

    def test_breaks_on_large_gap(self):
        cues = [
            Cue(0.0, 2.0, "Something without punctuation"),
            Cue(4.0, 6.0, "and then it continues here"),
        ]
        s = merge_cues(cues, gap_break=0.8)
        self.assertEqual(len(s), 2)

    def test_time_span_covers_all_cues(self):
        cues = [
            Cue(0.0, 2.0, "I want to tell you"),
            Cue(2.0, 4.5, "about my day."),
        ]
        s = merge_cues(cues)
        self.assertEqual(s[0].start, 0.0)
        self.assertEqual(s[0].end, 4.5)


class TestSubtitleOutput(unittest.TestCase):

    def test_srt_format(self):
        ws = words_from("Hello there. How are you?")
        s = split_words(ws)
        out = to_srt(s)
        self.assertIn("-->", out)
        self.assertIn("00:00:00,000", out)
        self.assertTrue(out.strip().endswith("How are you?"))

    def test_vtt_format(self):
        ws = words_from("Hello there. How are you?")
        s = split_words(ws)
        out = to_vtt(s)
        self.assertTrue(out.startswith("WEBVTT"))
        self.assertIn("-->", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
