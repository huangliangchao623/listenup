"""B 站登录设置向导（中文界面）。

## 为什么用 Python 而不是批处理

批处理里写中文有两个坑，都踩过：

1. **换行必须是 CRLF** —— 写成 LF 会让 cmd 把整个文件当成一行执行，
   满屏 "不是内部或外部命令"
2. **GBK 双字节的尾字节可能是 0x7C（`|`）** —— 某些汉字会被 cmd
   当成管道符，把后面的 `echo` 吃掉，表现为莫名报错

所以 .bat 里只写纯英文的启动命令，所有中文提示交给 Python 打印。
Python 输出 UTF-8，配合 `chcp 65001` 不会乱码。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESS_FILE = ROOT / "data" / "bili_sessdata.txt"
sys.path.insert(0, str(ROOT / "app"))

BANNER = """
============================================================
             设置 B 站登录态（SESSDATA）
============================================================

  没有它：导入 B 站视频只能靠语音识别，没有中文翻译
  有了它：自动带出 B 站的中文字幕

------------------------------------------------------------
  获取方法（大约 30 秒）
------------------------------------------------------------

  1. 用 Edge 打开 bilibili.com，确认已经登录
  2. 按 F12 打开开发者工具
  3. 顶部切到「应用程序」标签（英文叫 Application）
  4. 左侧依次展开：存储 → Cookie → https://www.bilibili.com
  5. 在下拉列表里找到名字是 SESSDATA 的那一行
  6. 双击它「值」那一列，全选（Ctrl+A）、复制（Ctrl+C）

------------------------------------------------------------
  怎么保存
------------------------------------------------------------

  接下来会打开一个记事本，把复制的内容粘到最后一行，保存关闭。
  保存后重启服务即可生效。

"""


def read_current() -> str:
    if not SESS_FILE.exists():
        return ""
    for line in SESS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def main() -> int:
    print(BANNER)

    cur = read_current()
    if cur:
        masked = cur[:6] + "…" + cur[-4:] if len(cur) > 12 else "（已设置）"
        print(f"  当前状态：已配置  {masked}\n")
    else:
        print("  当前状态：未配置\n")

    if not SESS_FILE.exists():
        SESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESS_FILE.write_text(
            "# B 站登录态（SESSDATA）\n"
            "# 把 SESSDATA 的值粘在下面这一行，保存即可。\n"
            "# 这一行是说明，程序会忽略所有以 # 开头的行。\n\n",
            encoding="utf-8")

    print("  正在打开记事本…\n")
    try:
        subprocess.run(["notepad.exe", str(SESS_FILE)])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开记事本：{e}")
        print(f"  请手动编辑：{SESS_FILE}")
        return 1

    # 记事本关闭后再读一次，给用户即时反馈
    now = read_current()
    print()
    if now:
        masked = now[:6] + "…" + now[-4:] if len(now) > 12 else "（已设置）"
        print(f"  ✓ 已检测到 SESSDATA：{masked}")
        print()
        print("  下一步：双击「停止服务.bat」，再双击「开机启动.bat」")
        print("  然后重新导入 B 站视频，就会自动带中文字幕了。")
    else:
        print("  没有读到值（文件里还是只有注释）。")
        print("  如果确实粘了，检查是不是粘到了 # 开头的行里。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
