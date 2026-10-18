"""Download cloudflared.exe with resume + multiple mirrors.

The GitHub direct download is blocked from this network, and the mirrors are
slow and occasionally stall, so this resumes a partial file and rotates through
mirrors until the size looks complete.
"""
from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "bin", "cloudflared.exe")
EXPECT_MIN = 40 * 1048576          # the binary is ~53 MB

REL = ("cloudflare/cloudflared/releases/latest/download/"
       "cloudflared-windows-amd64.exe")

MIRRORS = [
    f"https://ghfast.top/https://github.com/{REL}",
    f"https://ghproxy.net/https://github.com/{REL}",
    f"https://gh-proxy.com/https://github.com/{REL}",
    f"https://github.moeyy.xyz/https://github.com/{REL}",
]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))


def fetch(url: str) -> bool:
    have = os.path.getsize(DEST) if os.path.exists(DEST) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if have:
        headers["Range"] = f"bytes={have}-"

    try:
        resp = OPENER.open(urllib.request.Request(url, headers=headers),
                           timeout=45)
    except Exception as e:  # noqa: BLE001
        print(f"    connect fail: {type(e).__name__}: {e}", flush=True)
        return False

    with resp:
        code = getattr(resp, "status", None) or resp.getcode()
        # Only append on a real 206; a 200 means the mirror ignored Range and
        # sending the whole body would corrupt the file.
        append = have > 0 and code == 206
        if have and not append:
            print(f"    mirror ignored Range (HTTP {code}), restarting", flush=True)
            have = 0
        elif append:
            print(f"    resuming from {have / 1048576:.1f} MB", flush=True)

        mode = "ab" if append else "wb"
        last_report = time.time()
        try:
            with open(DEST, mode) as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    if time.time() - last_report > 5:
                        print(f"      {os.path.getsize(DEST) / 1048576:.1f} MB",
                              flush=True)
                        last_report = time.time()
        except Exception as e:  # noqa: BLE001
            print(f"    interrupted at {os.path.getsize(DEST) / 1048576:.1f} MB: "
                  f"{type(e).__name__}", flush=True)
            return False

    size = os.path.getsize(DEST)
    if size < EXPECT_MIN:
        print(f"    incomplete {size / 1048576:.1f} MB", flush=True)
        return False
    return True


def main() -> int:
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    print(f"目标: {DEST}")
    if os.path.exists(DEST) and os.path.getsize(DEST) >= EXPECT_MIN:
        print(f"已存在且完整 ({os.path.getsize(DEST) / 1048576:.1f} MB)，跳过")
        return 0

    for round_no in range(1, 7):
        for mirror in MIRRORS:
            print(f"  [round {round_no}] {mirror.split('/')[2]}", flush=True)
            if fetch(mirror):
                print(f"\n完成: {os.path.getsize(DEST) / 1048576:.1f} MB")
                return 0
        print(f"  round {round_no} exhausted, retrying in 5s", flush=True)
        time.sleep(5)

    print("!! 所有镜像都失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
