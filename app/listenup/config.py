"""Central paths and runtime settings for the listening trainer.

Everything the app needs at runtime (ffmpeg, model cache, Python deps) lives
under tools/, which was provisioned during setup.  Keeping these in one module
means the sandbox-specific workarounds are documented in exactly one place.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- repo layout -----------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent          # .../app/listenup
PROJECT_DIR = APP_DIR.parent                       # .../app
WORKSPACE = PROJECT_DIR.parent                     # workspace root
TOOLS = WORKSPACE / "tools"

BIN_DIR = TOOLS / "bin"
PYDEPS = TOOLS / "pydeps"
MODELS = TOOLS / "models"

DATA_DIR = WORKSPACE / "data"
MEDIA_DIR = DATA_DIR / "media"        # downloaded/imported source media
CLIP_DIR = DATA_DIR / "clips"         # per-sentence audio clips
SUB_DIR = DATA_DIR / "subs"           # normalized caption json
DB_PATH = DATA_DIR / "library.sqlite3"

FFMPEG = BIN_DIR / "ffmpeg.exe"
FFPROBE = BIN_DIR / "ffprobe.exe"

# --- environment the third-party libs read ---------------------------------
# The sandbox blocks %TEMP% and the default HF cache location, so every
# temp/cache directory is redirected inside the workspace.
_TMP = TOOLS / "tmp"
for _d in (DATA_DIR, MEDIA_DIR, CLIP_DIR, SUB_DIR, _TMP,
           MODELS / "hub", TOOLS / "cache", TOOLS / "appdata"):
    _d.mkdir(parents=True, exist_ok=True)


def apply_env() -> None:
    """Export the env vars third-party libraries need.  Call once at startup."""
    os.environ.setdefault("TEMP", str(_TMP))
    os.environ.setdefault("TMP", str(_TMP))
    # huggingface.co is unreachable from this network; the mirror works.
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", str(MODELS))
    os.environ.setdefault("HF_HUB_CACHE", str(MODELS / "hub"))
    # hf_hub publishes snapshots with symlinks/renames the sandbox denies,
    # and the Xet CAS protocol is unsupported by the mirror.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("XDG_CACHE_HOME", str(TOOLS / "cache"))
    os.environ.setdefault("APPDATA", str(TOOLS / "appdata"))
    # Keep native libs from oversubscribing this 18-core box.
    os.environ.setdefault("OMP_NUM_THREADS", "6")


# --- ASR settings ----------------------------------------------------------
# NOTE: never use distil-*/turbo models here.  They have a 2-layer decoder and
# their word-level timestamps are unreliable, which is fatal for sentence
# splitting (this is documented in the tech report).
ASR_MODEL = os.environ.get("LISTENUP_ASR_MODEL", "small.en")
ASR_COMPUTE = "int8"
ASR_CPU_THREADS = 6
ASR_NUM_WORKERS = 2

# --- sentence splitting defaults -------------------------------------------
# Tuned for English conversation / vlogs.
#
# SENT_MAX_GAP is deliberately generous.  A naive 0.75 s threshold sounds
# reasonable but fails badly on real podcasts and vlogs: speakers pause
# constantly mid-sentence to breathe or think, so a low threshold slices almost
# every sentence in half.  Only a long silence is a real utterance boundary.
SENT_MAX_GAP = 1.8
# Punctuation emitted by the transcriber must be corroborated by a pause before
# it is trusted; Whisper's punctuation is a guess and is often wrong on
# conversational speech.
#
# Tuned on a real 16-minute English podcast (tools/tune_split.py).  Measured:
# the gap between "life." and the next sentence (0.72 s) is almost identical to
# the gap in the middle of "Podcast Episode 1." (0.70 s), so pause length alone
# cannot separate sentence ends from mid-sentence breathing.  Requiring both a
# mark and a pause took the sentence count from 157 to 130 with far fewer
# unlearnable fragments.
SENT_PUNCT_GAP = 0.15
SENT_MAX_DURATION = 14.0     # force a break on a very long run
SENT_MAX_CHARS = 110         # force a break on a very long line
SENT_MIN_DURATION = 0.9      # merge fragments shorter than this
SENT_MIN_CHARS = 3

# --- audio ----------------------------------------------------------------
CLIP_SAMPLE_RATE = 24000
CLIP_BITRATE_Q = "4"
CLIP_FADE = 0.012            # 12 ms fades kill the click at both cut edges
