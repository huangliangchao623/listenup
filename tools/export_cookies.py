"""Export browser cookies to data/cookies.txt (Netscape format).

Why this exists
---------------
Douyin and TikTok reject anonymous requests.  Reading cookies straight from a
running Chrome/Edge fails because the browser keeps its cookie database locked
("Could not copy Chrome cookie database"), and asking you to close the browser
every single time is miserable.

Run this once: it reads the browser's cookies, writes them to a plain file, and
from then on the app picks that file up automatically.

Privacy
-------
The file stays on this machine at data/cookies.txt and is only handed to
yt-dlp/ffmpeg when fetching from a site that needs it.  Nothing is uploaded.
Treat it like a password: anyone with this file can act as your logged-in
session, so do not share it.

Usage
-----
    python tools\\export_cookies.py --browser edge
    python tools\\export_cookies.py --browser firefox
    python tools\\export_cookies.py --browser edge --out D:\\my\\cookies.txt
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
PYDEPS = HERE / "pydeps"
DEFAULT_OUT = WS / "data" / "cookies.txt"

sys.path.insert(0, str(PYDEPS))

BROWSER_PROCESS = {
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    "vivaldi": "vivaldi.exe",
}

# Firefox stores cookies in an unlocked SQLite file, so it works while running.
UNLOCKED_BROWSERS = {"firefox"}

# A page that requires no login.  yt-dlp loads the browser's cookie store on
# startup regardless of which site is requested, so this is enough to make it
# write the whole jar out.
PROBE_URL = "https://example.com/"


def browser_running(browser: str) -> bool:
    exe = BROWSER_PROCESS.get(browser)
    if not exe:
        return False
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return exe.lower() in (r.stdout or "").lower()


def main() -> int:
    ap = argparse.ArgumentParser(description="导出浏览器 cookies 供下载使用")
    ap.add_argument("--browser", default="edge",
                    choices=sorted(BROWSER_PROCESS),
                    help="从哪个浏览器读取（默认 edge）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"输出文件路径（默认 {DEFAULT_OUT}）")
    ap.add_argument("--force", action="store_true",
                    help="即使浏览器在运行也强行尝试")
    args = ap.parse_args()

    browser = args.browser.lower()
    out = Path(args.out)

    print("=" * 62)
    print("  导出浏览器 cookies")
    print("=" * 62)
    print(f"  来源浏览器 : {browser}")
    print(f"  输出文件   : {out}")

    if browser_running(browser) and browser not in UNLOCKED_BROWSERS \
            and not args.force:
        print()
        print(f"  ✗ {browser} 正在运行，它的 cookie 数据库被锁住了。")
        print()
        print("  请先完全退出它：")
        print(f"    - 关闭所有 {browser} 窗口")
        print(f"    - 打开任务管理器，确认没有 {BROWSER_PROCESS[browser]} 进程")
        print("      （Edge 关了窗口后常常还有后台进程，必须手动结束）")
        print()
        print("  然后重新运行本命令。")
        print()
        print("  或者换 Firefox —— 它的 cookies 不受运行状态影响：")
        print("    python tools\\export_cookies.py --browser firefox")
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--cookies-from-browser", browser,
        "--cookies", str(out),
        "--skip-download", "--no-warnings",
        PROBE_URL,
    ]
    print(f"\n  正在读取 {browser} 的 cookies …")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYDEPS) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("TEMP", str(HERE / "tmp"))
    env.setdefault("TMP", str(HERE / "tmp"))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)

    if not out.exists() or out.stat().st_size == 0:
        err = (r.stderr or "") + (r.stdout or "")
        print("\n  ✗ 导出失败。yt-dlp 的输出：")
        tail = err.strip().splitlines()[-8:]
        for line in tail:
            print("   ", line)
        print()

        low = err.lower()
        if "could not copy" in low and "winerror 5" in low:
            print("  两个可能的原因，按顺序排查：")
            print("  ① 浏览器没关干净 —— 最常见")
            print(f"     到任务管理器结束所有 {BROWSER_PROCESS[browser]} 进程")
            print("  ② 当前终端权限不足，无法在临时目录建文件")
            print("     换个普通（非沙箱）终端重试")
        elif "could not copy" in low:
            print("  原因：浏览器没关干净。")
            print(f"  请到任务管理器结束所有 {BROWSER_PROCESS[browser]} 进程后重试。")
        elif "no such file" in low or "not found" in low:
            print(f"  原因：没找到 {browser} 的 cookie 数据库。")
            print(f"  请确认 {browser} 已安装，并且至少启动过一次。")
        return 1

    # Report what we actually captured, without printing the secret values.
    lines = [l for l in out.read_text(encoding="utf-8", errors="replace").splitlines()
             if l and not l.startswith("#")]
    domains: dict[str, int] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 1:
            d = parts[0].lstrip(".")
            domains[d] = domains.get(d, 0) + 1

    print(f"\n  ✓ 成功导出 {len(lines)} 条 cookies 到：")
    print(f"     {out}")
    if domains:
        print("\n  涉及的主要域名：")
        for d, n in sorted(domains.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {d:34} {n} 条")

    if any("douyin" in d for d in domains):
        print("\n  ✓ 包含抖音 cookies，抖音链接现在应该能解析了。")
    else:
        print("\n  ⚠ 没有找到 douyin.com 的 cookies。")
        print("    请先在浏览器里打开并登录 www.douyin.com，再重新导出。")

    print("\n  ⚠ 这个文件等同于你的登录凭证，不要分享给任何人。")
    print("    不需要时直接删除即可，程序会自动回到「无 cookies」状态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
