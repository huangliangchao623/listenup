"""内网穿透守护进程。

## 为什么需要它

只靠"开机启动一次"是不够的：natapp 客户端会因为网络抖动、服务端踢线、
或者被别的程序误杀而退出，退出之后就再也没人管它了——表现就是
"电脑明明开着，手机却突然访问不了"。

这个进程做三件事，一直循环：

  1. **端口转发**：natapp 隧道指向的是本机某个端口（常见是 80，因为
     网站后台建隧道时默认填 80）。如果它不是 8765，就在这里开一个
     TCP 中继，把它转到 8765，省得用户去后台改配置。
  2. **看住 natapp**：没在跑就拉起来；跑着但退出了就重启，带退避，
     避免疯狂重启把服务端打到限流。
  3. **留证据**：每次重启都写进 data\\logs\\tunnel-watchdog.log，
     出问题能查到是哪一刻断的。

用 pythonw 启动（无窗口），由「开机启动.bat」在开机时拉起。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# 让子进程（tasklist / natapp / ffmpeg）不弹控制台窗口
import sys as _sys

_sys.path.insert(0, str(ROOT / "app"))
from listenup import noflash as _noflash  # noqa: E402

_noflash.install()

DATA = ROOT / "data"
LOGS = DATA / "logs"
TOKEN_FILE = DATA / "natapp_token.txt"
NATAPP = TOOLS / "bin" / "natapp.exe"
OUT_LOG = LOGS / "natapp.out"
WD_LOG = LOGS / "tunnel-watchdog.log"

SERVICE_PORT = 8765
CHECK_EVERY = 15          # 秒
MAX_BACKOFF = 120         # 连续失败时最长等这么久再重启

_forwarder_done: set[int] = set()


def log(msg: str) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with WD_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def read_token() -> str | None:
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        return t or None
    except Exception:  # noqa: BLE001
        return None


def natapp_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq natapp.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15).stdout
        return "natapp.exe" in out.lower()
    except Exception:  # noqa: BLE001
        return False


def tunnel_local_port() -> int | None:
    """从 natapp 日志里读隧道转发到的本地端口（local=127.0.0.1:80）。"""
    if not OUT_LOG.exists():
        return None
    try:
        text = OUT_LOG.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    import re
    found = re.findall(r"local=(?:[\d.]+|localhost):(\d+)", text)
    return int(found[-1]) if found else None


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def start_forwarder(listen_port: int) -> bool:
    if listen_port in _forwarder_done:
        return True
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", listen_port))
        srv.listen(64)
    except OSError as e:
        log(f"转发器绑定 {listen_port} 失败：{e}")
        return False

    def accept_loop() -> None:
        while True:
            try:
                client, _ = srv.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(
                    ("127.0.0.1", SERVICE_PORT), timeout=10)
            except OSError:
                client.close()
                continue
            threading.Thread(target=_pipe, args=(client, upstream),
                             daemon=True).start()
            threading.Thread(target=_pipe, args=(upstream, client),
                             daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    _forwarder_done.add(listen_port)
    log(f"已在本机 {listen_port} 端口开转发 → {SERVICE_PORT}")
    return True


def start_natapp(token: str) -> bool:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(NATAPP), f"-authtoken={token}", f"-log={OUT_LOG}", "-loglevel=INFO"],
            cwd=str(TOOLS / "bin"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log("已启动 natapp 客户端")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"启动 natapp 失败：{e}")
        return False


def main() -> int:
    # 单实例保护：开机启动.bat 可能被重复触发，两个守护进程会各自去拉
    # natapp，互相打架。所以先看 PID 文件里那个进程还活着不。
    pid_file = LOGS / "tunnel-watchdog.pid"
    LOGS.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        try:
            old = int(pid_file.read_text().strip())
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
            if str(old) in out and "python" in out.lower():
                log(f"已有守护进程在跑（PID {old}），本次退出")
                return 0
        except Exception:  # noqa: BLE001
            pass
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    log(f"=== 守护进程启动，PID {os.getpid()} ===")
    token = read_token()
    if not token:
        log("没有 natapp_token.txt，守护线程退出")
        return 0

    fails = 0
    while True:
        try:
            # 1) 先保证 natapp 在跑
            if not natapp_running():
                if fails:
                    wait = min(MAX_BACKOFF, 5 * (2 ** min(fails, 5)))
                    log(f"natapp 不在（连续失败 {fails} 次），等 {wait}s 再拉起")
                    time.sleep(wait)
                if start_natapp(token):
                    fails = 0
                else:
                    fails += 1
                time.sleep(6)      # 给客户端一点时间建立隧道

            # 2) 读取隧道实际的本地端口，必要时开转发
            port = tunnel_local_port()
            if port and port != SERVICE_PORT:
                start_forwarder(port)

            # 3) 检查本地服务是否活着（服务挂了隧道转发过去也是 502）
            try:
                with socket.create_connection(("127.0.0.1", SERVICE_PORT),
                                              timeout=3):
                    pass
            except OSError:
                log(f"本地服务 {SERVICE_PORT} 端口没有响应")

        except Exception as e:  # noqa: BLE001
            log(f"守护循环异常：{type(e).__name__}: {e}")
            fails += 1

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    sys.exit(main())
