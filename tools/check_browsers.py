"""Check which browsers can supply cookies for Douyin/TikTok.

Chrome and Edge lock their cookie database while running, so reading from them
is unreliable.  Firefox keeps its cookies in an unlocked SQLite file, so it is
by far the simplest path on Windows.  This script reports what is actually
available on this machine instead of guessing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
ROAM = Path(os.environ.get("APPDATA", ""))
HOME = Path.home()

BROWSERS = {
    "Firefox": [
        ROAM / "Mozilla" / "Firefox" / "Profiles",
    ],
    "Edge": [
        LOCAL / "Microsoft" / "Edge" / "User Data",
    ],
    "Chrome": [
        LOCAL / "Google" / "Chrome" / "User Data",
    ],
    "Brave": [
        LOCAL / "BraveSoftware" / "Brave-Browser" / "User Data",
    ],
    "Opera": [
        ROAM / "Opera Software" / "Opera Stable",
    ],
    "Vivaldi": [
        LOCAL / "Vivaldi" / "User Data",
    ],
}

PROCESS_NAMES = {
    "Firefox": "firefox",
    "Edge": "msedge",
    "Chrome": "chrome",
    "Brave": "brave",
    "Opera": "opera",
    "Vivaldi": "vivaldi",
}


def running(name: str) -> int:
    r = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {PROCESS_NAMES[name]}.exe", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (r.stdout or "").lower().count(f"{PROCESS_NAMES[name]}.exe")


def main() -> int:
    print("=" * 66)
    print("  浏览器 cookies 可用性检查")
    print("=" * 66)

    installed: list[str] = []
    for name, dirs in BROWSERS.items():
        hit = next((d for d in dirs if d.exists()), None)
        if not hit:
            print(f"  {name:9} 未安装")
            continue
        installed.append(name)
        n = running(name)
        lock = f"运行中（{n} 个进程）→ cookie 数据库被锁" if n else "未运行 → 可以读取"
        print(f"  {name:9} 已安装   {lock}")

    print()
    if not installed:
        print("  没有检测到任何浏览器。")
        return 1

    # Firefox keeps cookies in an unlocked file, so it always works.
    if "Firefox" in installed:
        print("  ★ 推荐：用 Firefox")
        print("    原因：Firefox 的 cookie 存在独立的 SQLite 文件里，")
        print("         浏览器开着也能读，不需要关浏览器。")
        print()
        print("    步骤：")
        print("      1) 在 Firefox 里打开 www.douyin.com 并登录")
        print("      2) 设置环境变量后重启服务：")
        print('         $env:LISTENUP_COOKIES_FROM_BROWSER = "firefox"')
        print("         python app\\run.py")
    else:
        print("  ★ 你没有 FireFox，用 Edge 也可以，但必须先完全关掉它。")
        print()
        print("    步骤：")
        print("      1) 在 Edge 里登录 www.douyin.com（确认能看视频）")
        print("      2) 完全退出 Edge：任务管理器里确认没有 msedge.exe")
        print("      3) 设置环境变量后重启服务：")
        print('         $env:LISTENUP_COOKIES_FROM_BROWSER = "edge"')
        print("         python app\\run.py")

    print()
    print("  ── 更省事的替代方案 ──────────────────────────────")
    print("  只关一次浏览器，把 cookies 导出成文件，以后永久可用：")
    print("    1) 关掉 Edge，运行：")
    print("       python tools\\export_cookies.py --browser edge")
    print("    2) 之后启动服务不用再设任何环境变量，")
    print("       程序会自动使用 data\\cookies.txt")
    print()
    print("  （cookies 只保存在你自己的电脑上，不会上传到任何地方）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
