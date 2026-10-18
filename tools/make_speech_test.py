"""Generate real English speech with Piper, then run the full pipeline on it.

This is the strongest available end-to-end check: the exact text is known, so
ASR accuracy, sentence splitting and clip alignment can all be judged against
ground truth - without depending on any external media source.
"""
from __future__ import annotations

import os
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../tools
WS = HERE.parent                                # workspace root
APP = WS / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE / "pydeps"))

from listenup import config as C  # noqa: E402

VOICE_DIR = C.MODELS / "voices"
VOICE = "en_US-lessac-medium"
OUT_DIR = C.TOOLS / "_speech"

# Deliberately conversational, with clear sentence boundaries and a couple of
# tricky bits (abbreviation, question mark, contraction).
SCRIPT = [
    "Hello and welcome back to the channel.",
    "Today we are going to practise English listening.",
    "Dr. Smith says you should listen every single day.",
    "Do you understand the sentence you just heard?",
    "If not, simply click it again and listen once more.",
    "Practice a little every day, and your English will improve quickly.",
]


def synth(text: str, out_wav: Path) -> bool:
    """Synthesize one line to wav.

    Invoked as `python -m piper` rather than the console script: the latter is a
    uv trampoline that cannot canonicalize its target when installed with
    --target into a directory that is only on PYTHONPATH.
    """
    model = VOICE_DIR / f"{VOICE}.onnx"
    if not model.exists():
        print(f"!! voice model missing: {model}")
        return False

    r = subprocess.run(
        [sys.executable, "-m", "piper", "-m", str(model), "-f", str(out_wav)],
        input=text, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONPATH": str(C.PYDEPS)},
    )
    if r.returncode != 0:
        print(f"   piper failed ({r.returncode}): {(r.stderr or '')[-500:]}")
        return False
    return out_wav.exists() and out_wav.stat().st_size > 1000


def wav_duration(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    silence = OUT_DIR / "sil.wav"

    # Build a 0.8 s silence once with ffmpeg.
    subprocess.run(
        [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono:d=0.8",
         "-c:a", "pcm_s16le", str(silence)],
        check=True,
    )

    parts: list[Path] = []
    for i, line in enumerate(SCRIPT):
        wav = OUT_DIR / f"line{i:02d}.wav"
        if not synth(line, wav):
            return 1
        print(f"   line {i}: {wav_duration(wav):5.2f}s  {line}")
        parts += [wav, silence]

    # Concatenate with ffmpeg's concat demuxer.
    listfile = OUT_DIR / "concat.txt"
    listfile.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = OUT_DIR / "speech_test.wav"
    subprocess.run(
        [str(C.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(final)],
        check=True,
    )
    print(f"\n   combined: {wav_duration(final):.2f}s -> {final}")

    (OUT_DIR / "ground_truth.txt").write_text("\n".join(SCRIPT), encoding="utf-8")
    print("\n=== running the full pipeline on this audio ===")
    r = subprocess.run(
        [sys.executable, "-m", "listenup.cli", "build-local", str(final),
         "--title", "Piper 语音测试"],
        cwd=str(WS),
        env={**os.environ,
             "PYTHONPATH": str(C.PYDEPS) + os.pathsep + str(APP),
             "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(r.stdout[-4000:])
    if r.returncode != 0:
        print("--- stderr ---")
        print(r.stderr[-2000:])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
