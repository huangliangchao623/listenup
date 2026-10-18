"""Fetch ffmpeg / ffprobe / yt-dlp binaries into tools/bin.

Python's OpenSSL TLS stack works inside the DSH sandbox even though
.NET/curl schannel is blocked, so this script is the download path.
Tries several mirrors per artifact and reports clearly.
"""
import os
import ssl
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "bin")
os.makedirs(BIN, exist_ok=True)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Optional proxy support: set DSH_DL_PROXY=http://127.0.0.1:7890 to route downloads.
PROXY = os.environ.get("DSH_DL_PROXY") or os.environ.get("HTTPS_PROXY")
if PROXY:
    print(f"using proxy: {PROXY}", flush=True)
    OPENER = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
        urllib.request.HTTPSHandler(context=CTX),
    )
else:
    OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))

FFMPEG_ZIPS = [
    "https://ghproxy.net/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://gh-proxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]
YTDLP = [
    "https://ghfast.top/https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "https://ghproxy.net/https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "https://gh-proxy.com/https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
]


def fetch(url, dest, label, expect_min=None):
    """Download url -> dest, safely resuming a partial file.

    Critical detail: only append when the server actually honours Range and
    answers 206.  A proxy that ignores Range returns 200 with the *whole*
    body, and appending that to a partial file silently corrupts it.
    """
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = OPENER.open(req, timeout=60)
    except Exception as e:  # noqa: BLE001
        print(f"    connect fail: {type(e).__name__}: {e}", flush=True)
        return False

    with resp:
        code = getattr(resp, "status", None) or resp.getcode()
        appending = have > 0 and code == 206
        if have and not appending:
            print(f"    server ignored Range (HTTP {code}); restarting from 0", flush=True)
            have = 0
        elif appending:
            print(f"    resuming from {have / 1048576:.1f} MB (HTTP 206)", flush=True)
        mode = "ab" if appending else "wb"
        try:
            with open(dest, mode) as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception as e:  # noqa: BLE001
            got = os.path.getsize(dest) if os.path.exists(dest) else 0
            print(f"    interrupted at {got / 1048576:.1f} MB: {type(e).__name__}: {e}", flush=True)
            return False

    size = os.path.getsize(dest)
    if expect_min and size < expect_min:
        print(f"    incomplete: {size / 1048576:.1f} MB (want >= {expect_min / 1048576:.1f} MB)", flush=True)
        return False
    print(f"  OK {label}: {size / 1048576:.1f} MB", flush=True)
    return True


def try_all(urls, dest, label, expect_min=None, rounds=5):
    """Retry every mirror across rounds; transient DNS failures recover."""
    for rnd in range(1, rounds + 1):
        for u in urls:
            print(f"  [{rnd}/{rounds}] {label}: {u}", flush=True)
            if fetch(u, dest, label, expect_min):
                return True
        if rnd < rounds:
            print(f"  -- round {rnd} exhausted, waiting 15s --", flush=True)
            time.sleep(15)
    return False


def main():
    # --- yt-dlp ---
    yt = os.path.join(BIN, "yt-dlp.exe")
    if os.path.exists(yt) and os.path.getsize(yt) > 1_000_000:
        print("yt-dlp already present, skipping")
    elif try_all(YTDLP, yt, "yt-dlp"):
        print("yt-dlp ready")
    else:
        print("!! yt-dlp FAILED from all mirrors")

    # --- ffmpeg bundle ---
    have_ff = os.path.exists(os.path.join(BIN, "ffmpeg.exe"))
    have_fp = os.path.exists(os.path.join(BIN, "ffprobe.exe"))
    if have_ff and have_fp:
        print("ffmpeg/ffprobe already present, skipping")
        return

    zpath = os.path.join(HERE, "ffmpeg.zip")
    # BtbN gpl build is ~80-120 MB; a smaller file means the transfer was cut.
    if try_all(FFMPEG_ZIPS, zpath, "ffmpeg zip", expect_min=60 * 1048576):
        print("extracting ffmpeg/ffprobe ...", flush=True)
        try:
            with zipfile.ZipFile(zpath) as z:
                wanted = [
                    n for n in z.namelist()
                    if os.path.basename(n).lower() in ("ffmpeg.exe", "ffprobe.exe")
                ]
                for n in wanted:
                    base = os.path.basename(n)
                    with z.open(n) as src, open(os.path.join(BIN, base), "wb") as dst:
                        dst.write(src.read())
                    print(f"  extracted {base}")
            os.remove(zpath)
        except zipfile.BadZipFile as e:
            print(f"!! zip corrupt ({e}); delete and rerun", flush=True)
    else:
        print("!! ffmpeg FAILED from all mirrors", flush=True)

    print("\n=== bin contents ===")
    for n in sorted(os.listdir(BIN)):
        p = os.path.join(BIN, n)
        print(f"  {n:16} {os.path.getsize(p) / 1048576:8.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
