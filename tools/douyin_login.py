"""抖音登录设置向导（中文界面）。

和 B 站不同的是：抖音要整套 cookie，不是一个值。
所以让用户从 F12 里把 Cookie 那一栏整段复制过来。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / "data" / "douyin_cookie.txt"
sys.path.insert(0, str(ROOT / "app"))

BANNER = """
============================================================
              设置抖音登录（Cookies）
============================================================

  没有它：抖音链接完全下不了（平台拒绝匿名请求）
  有了它：可以像 B 站一样粘贴链接导入

------------------------------------------------------------
  获取方法（大约 1 分钟）
------------------------------------------------------------

  1. 用 Edge 打开 www.douyin.com，确认已经登录
  2. 按 F12 打开开发者工具
  3. 顶部切到「网络 / Network」标签
  4. 刷新页面（F5），在左侧列表里随便点一个 douyin.com 的请求
  5. 右侧找到「请求标头 / Request Headers」
  6. 找到 Cookie 那一行，把冒号后面**整段**复制
     （形如：ttwid=xxx; sessionid=yyy; msToken=zzz; ...）

------------------------------------------------------------
  怎么保存
------------------------------------------------------------

  接下来会打开记事本，把整段内容粘到最后一行，保存关闭。

  说明：抖音的 cookie 有时效（几天到几周），过期了重新复制一次即可。

"""


def read_current() -> str:
    if not COOKIE_FILE.exists():
        return ""
    for line in COOKIE_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            return s
    return ""


def main() -> int:
    print(BANNER)

    cur = read_current()
    if cur:
        n = cur.count("=")
        print(f"  当前状态：已配置（约 {n} 个 cookie）\n")
    else:
        print("  当前状态：未配置\n")

    if not COOKIE_FILE.exists():
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(
            "# 抖音 cookie\n"
            "# 把 F12 里 Cookie 那一整段粘在下面（形如 ttwid=xxx; sessionid=yyy）。\n"
            "# 程序会忽略所有以 # 开头的行。\n\n",
            encoding="utf-8")

    print("  正在打开记事本…\n")
    try:
        subprocess.run(["notepad.exe", str(COOKIE_FILE)])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开记事本：{e}")
        print(f"  请手动编辑：{COOKIE_FILE}")
        return 1

    now = read_current()
    print()
    if now:
        print(f"  ✓ 已检测到 cookie（约 {now.count('=')} 个）")
        print()
        print("  下一步：双击「停止服务.bat」，再双击「开机启动.bat」")
        print("  然后就能粘贴抖音链接导入了。")
    else:
        print("  没有读到内容（文件里还是只有注释）。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
