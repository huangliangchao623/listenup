"""消灭控制台闪窗。

## 问题

这个程序的后台进程是用 `pythonw.exe` 启动的——**没有控制台**。

但它在干活时调用的这些工具**全是控制台程序**：

```
tasklist   查进程是否在跑（守护进程每 15 秒一次）
netstat    查端口占用（控制台每次刷新）
taskkill   停止服务
ffmpeg     切音频、转码（每导入一个视频要跑几十次）
ffprobe    读媒体信息
yt-dlp     下载平台视频
```

Windows 的规则是：**父进程没有控制台时，启动控制台子进程会新开一个控制台窗口**。
所以用户会看到黑框不停地闪——守护进程每 15 秒闪一次，导入视频时连续闪几十次。

要压住它，必须给每次 `subprocess` 调用加 `creationflags=CREATE_NO_WINDOW`。

## 为什么在入口处统一打补丁，而不是逐个改调用点

因为 `yt-dlp` 是第三方库，它内部自己调 ffmpeg/ffprobe，我们改不到它的源码。
在进程入口把 `subprocess` 的默认行为改掉，才能盖住所有路径。

用的是 `setdefault`：**调用方自己指定的 creationflags 优先**，
所以「用新窗口打开隧道」这种刻意行为不受影响。
"""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000       # Windows: 进程不创建控制台窗口
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010

_installed = False


def no_window_flags() -> int:
    """子进程不弹窗的 creationflags。非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return CREATE_NO_WINDOW


def install() -> None:
    """把 subprocess 的 creationflags 默认设成「不弹窗」。可重复调用。

    ## 只改 Popen.__init__，不能把 Popen 本身换掉

    第一版我把 `subprocess.Popen` 整个替换成了一个函数，结果服务起不来：

        File "asyncio/windows_utils.py", line 125
          class Popen(subprocess.Popen):
        TypeError: function() argument 'code' must be code, not str

    因为 `subprocess.Popen` 是个**类**，asyncio 在 Windows 上会**继承它**
    （`class Popen(subprocess.Popen)`）。换成一个函数之后，继承就崩了。

    所以正确做法是只包一层 `Popen.__init__`——类的身份不变，别人照样能继承。
    而且 `subprocess.run` / `call` / `check_output` 内部都是调 Popen，
    改这一处就全覆盖了。
    """
    global _installed
    if _installed or sys.platform != "win32":
        return
    _installed = True

    flag = CREATE_NO_WINDOW
    orig_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        # 调用方自己传了 creationflags 就用它的（例如「用新窗口打开」）
        kwargs.setdefault("creationflags", flag)
        orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init
