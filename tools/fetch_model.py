"""Download a faster-whisper (CTranslate2) model via the HF mirror.

huggingface.co is blocked from this network; hf-mirror.com works, so HF_ENDPOINT
is pointed there.  Cache goes inside the workspace so the sandbox can write it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "models")
os.makedirs(CACHE, exist_ok=True)

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = CACHE
os.environ["HF_HUB_CACHE"] = os.path.join(CACHE, "hub")
# The sandbox denies the symlink/rename step hf_hub uses to publish a snapshot,
# so make it copy real files instead of linking into blobs.
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
# hf-mirror.com does not serve the Xet CAS protocol, and hf_hub tries it first
# (chunked, via cas-server.xethub.hf.co) which fails 401.  Force plain HTTP.
os.environ["HF_HUB_DISABLE_XET"] = "1"

sys.path.insert(0, os.path.join(HERE, "pydeps"))

REPO = os.environ.get("FW_REPO", "Systran/faster-whisper-small.en")


def main():
    from huggingface_hub import snapshot_download

    print(f"endpoint: {os.environ['HF_ENDPOINT']}", flush=True)
    print(f"repo:     {REPO}", flush=True)
    print(f"cache:    {os.environ['HF_HUB_CACHE']}", flush=True)
    try:
        path = snapshot_download(
            repo_id=REPO,
            cache_dir=os.environ["HF_HUB_CACHE"],
            allow_patterns=["*.bin", "*.json", "*.txt", "*.md"],
            max_workers=4,
        )
    except Exception as e:  # noqa: BLE001
        print(f"DOWNLOAD FAILED: {type(e).__name__}: {e}", flush=True)
        return 1

    print(f"\nmodel at: {path}", flush=True)
    total = 0
    for root, _dirs, files in os.walk(path):
        for n in files:
            p = os.path.join(root, n)
            total += os.path.getsize(p)
            print(f"  {n:32} {os.path.getsize(p) / 1048576:8.1f} MB", flush=True)
    print(f"total: {total / 1048576:.1f} MB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
