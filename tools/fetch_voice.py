"""Download a Piper voice (offline neural TTS) via the HF mirror.

Serves two purposes:
  * gives the app a working offline read-aloud engine, since edge-tts's
    unofficial endpoint returns 403 from this network;
  * gives us real English speech to exercise the ASR + sentence-splitting
    pipeline end to end without depending on external media.

Piper voices are MIT/CC licensed; en_US-lessac-medium is a standard test voice.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "models")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = CACHE
os.environ["HF_HUB_CACHE"] = os.path.join(CACHE, "hub")
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "1"

sys.path.insert(0, os.path.join(HERE, "pydeps"))

REPO = os.environ.get("PIPER_REPO", "rhasspy/piper-voices")
VOICE = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")
# piper-voices is a very large monorepo; listing its tree through the mirror
# times out, so fetch the two files we need by explicit path instead.
FILES = [
    f"en/en_US/lessac/medium/{VOICE}.onnx",
    f"en/en_US/lessac/medium/{VOICE}.onnx.json",
]


def main():
    from huggingface_hub import hf_hub_download

    out = os.path.join(CACHE, "voices")
    os.makedirs(out, exist_ok=True)
    print(f"endpoint: {os.environ['HF_ENDPOINT']}")
    print(f"repo    : {REPO}")

    found = 0
    for rel in FILES:
        try:
            p = hf_hub_download(
                repo_id=REPO,
                filename=rel,
                cache_dir=os.environ["HF_HUB_CACHE"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {rel}: {type(e).__name__}: {str(e)[:110]}")
            continue
        dst = os.path.join(out, os.path.basename(rel))
        with open(p, "rb") as f, open(dst, "wb") as g:
            g.write(f.read())
        print(f"  OK   {os.path.basename(rel):40} {os.path.getsize(dst) / 1048576:7.1f} MB")
        found += 1

    print(f"\n{found}/{len(FILES)} files -> {out}")
    return 0 if found == len(FILES) else 1


if __name__ == "__main__":
    sys.exit(main())
