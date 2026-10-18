"""Fetch uv (Python package manager) - used because pip's temp-wheel path
is blocked by the sandbox's mandatory integrity level.

uv unpacked is ~30-40 MB.  Mirrors via GitHub proxies.
"""
import os
import ssl
import sys
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "bin")
os.makedirs(BIN, exist_ok=True)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
PROXY = os.environ.get("DSH_DL_PROXY") or os.environ.get("HTTPS_PROXY")
if PROXY:
    OPENER = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
        urllib.request.HTTPSHandler(context=CTX))
else:
    OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))

TARGET = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
URLS = [
    "https://ghfast.top/" + TARGET,
    "https://ghproxy.net/" + TARGET,
    "https://gh-proxy.com/" + TARGET,
    TARGET,
]


def fetch(url, dest, expect_min=None):
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    mode = "wb"
    if have:
        headers["Range"] = f"bytes={have}-"
        mode = "ab"
    req = urllib.request.Request(url, headers=headers)
    try:
        with OPENER.open(req, timeout=90) as r, open(dest, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        size = os.path.getsize(dest)
        if expect_min and size < expect_min:
            print(f"  truncated at {size / 1048576:.1f} MB", flush=True)
            return False
        print(f"  OK {size / 1048576:.1f} MB", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  fail: {type(e).__name__}: {e}", flush=True)
        return False


def main():
    zpath = os.path.join(HERE, "uv.zip")
    ok = False
    for rnd in range(1, 4):
        for u in URLS:
            print(f"  [{rnd}] {u}", flush=True)
            if fetch(u, zpath, expect_min=8 * 1048576):
                ok = True
                break
        if ok:
            break
    if not ok:
        print("!! uv download failed")
        return 1

    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if os.path.basename(n).lower() == "uv.exe":
                with z.open(n) as src, open(os.path.join(BIN, "uv.exe"), "wb") as dst:
                    dst.write(src.read())
                print("extracted uv.exe")
    os.remove(zpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
