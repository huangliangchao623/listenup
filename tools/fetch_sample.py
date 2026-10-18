"""Try to fetch a real English speech sample (public domain) for testing.

Wikimedia and archive.org direct downloads have been unreliable from this
network, so several candidates are tried and the first success wins.
"""
import json
import os
import socket
import ssl
import sys
import urllib.request

socket.setdefaulttimeout(30)
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_e2e")
os.makedirs(OUT, exist_ok=True)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with OPENER.open(req, timeout=timeout) as r:
        return r.read()


# --- Option 1: LibriVox API (public domain audiobooks, English) ------------
print("[1] LibriVox API ...")
try:
    data = json.loads(get(
        "https://librivox.org/api/feed/audiobooks/?format=json&limit=3&extended=1"))
    books = data.get("books") or []
    print(f"    got {len(books)} books")
    for b in books:
        sections = b.get("sections") or []
        print(f"    {b.get('title')!r}: {len(sections)} sections")
        for s in sections[:2]:
            listen = s.get("listen_url")
            print(f"      listen_url={listen}")
            if not listen:
                continue
            try:
                raw = get(listen, timeout=120)
            except Exception as e:  # noqa: BLE001
                print(f"      download FAIL {type(e).__name__}: {str(e)[:90]}")
                continue
            dst = os.path.join(OUT, "sample.mp3")
            with open(dst, "wb") as f:
                f.write(raw)
            print(f"      OK saved {len(raw)} bytes -> {dst}")
            sys.exit(0)
except Exception as e:  # noqa: BLE001
    print(f"    FAIL {type(e).__name__}: {str(e)[:100]}")

# --- Option 2: archive.org LibriVox item ----------------------------------
print("[2] archive.org ...")
CANDIDATES = [
    "https://archive.org/download/librivoxaudio/",
    "https://ia800207.us.archive.org/7/items/test_recordings_201307/",
]
for url in CANDIDATES:
    try:
        raw = get(url, timeout=45)
        print(f"    OK {url} -> {len(raw)} bytes")
    except Exception as e:  # noqa: BLE001
        print(f"    FAIL {url}: {type(e).__name__}: {str(e)[:80]}")

print("\nno sample obtained")
sys.exit(1)
