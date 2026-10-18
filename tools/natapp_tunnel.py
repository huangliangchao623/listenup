"""国内内网穿透（natapp）集成。

## 为什么换成 natapp

原来用 Cloudflare 隧道，但它在国内有两个硬伤：
  · 出口在美国节点，延迟 1.5–2.5 秒
  · 要用固定地址必须自己有域名 + Cloudflare 账号

natapp 是国内服务，机房在阿里云 BGP 多线，延迟低得多，也不用域名。

## 你需要做的（一次性）

1. 注册 https://natapp.cn/register （要实名认证）
2. 在「购买隧道」里选**免费隧道**，本地端口填 **8765**，协议选 Web
3. 在「隧道列表」里复制 **authtoken**
4. 把 token 粘给这个脚本（会自动存到 data/natapp_token.txt）

## 免费版的真实限制（重要）

  · 1M 带宽（约 128 KB/s）
  · 流量 2G/月
  · **随机域名，且"不定时强制更换域名/端口"**

最后一条意味着免费版地址会突然失效，只适合自己测试。
想稳定给几个人用，需要 ¥9/月 的 VIP_1（固定域名）。

## 用法

    python tools/natapp_tunnel.py                 # 用已保存的 token 启动
    python tools/natapp_tunnel.py <authtoken>     # 保存新 token 并启动
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 让子进程（tasklist / natapp / ffmpeg）不弹控制台窗口
import sys as _sys

_sys.path.insert(0, str(ROOT / "app"))
from listenup import noflash as _noflash  # noqa: E402

_noflash.install()

BIN = ROOT / "tools" / "bin"
EXE = BIN / "natapp.exe"
TOKEN_FILE = ROOT / "data" / "natapp_token.txt"
LOCAL_PORT = 8765


def read_token() -> str | None:
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    return None


def save_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    print(f"  ✓ token 已保存到 {TOKEN_FILE}")


def ensure_client(token: str) -> bool:
    """用官方一键脚本下载客户端（它会同时生成 run_natapp.bat）。"""
    if EXE.exists():
        print(f"  ✓ 客户端已存在：{EXE}")
        return True

    print("  客户端不存在，正在用官方脚本下载…")
    BIN.mkdir(parents=True, exist_ok=True)
    ps = (
        f"$ErrorActionPreference='Stop';"
        f"Set-Location -LiteralPath '{BIN}';"
        f"irm 'https://natapp.cn/get.ps1?authtoken={token}' | iex"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    if EXE.exists():
        print(f"  ✓ 客户端已下载：{EXE} ({EXE.stat().st_size:,} B)")
        return True

    print("  ✗ 下载失败。可以手动下载：")
    print("     https://natapp.cn/download  （登录后才有下载链接）")
    print(f"     把 natapp.exe 放到 {BIN}")
    if r.stdout.strip():
        print("  脚本输出：", r.stdout.strip()[:400])
    return False


def main() -> int:
    print("=" * 62)
    print("  英语听力训练 · 国内穿透（natapp）")
    print("=" * 62)

    token = sys.argv[1].strip() if len(sys.argv) > 1 else None
    if token:
        save_token(token)
    else:
        token = read_token()

    if not token:
        print()
        print("  还没配置 authtoken。步骤：")
        print("    1. 注册 https://natapp.cn/register （需实名）")
        print("    2. 购买隧道 → 选免费隧道")
        print("       本地端口填 8765，协议选 Web")
        print("    3. 隧道列表里复制 authtoken")
        print("    4. 再次运行：python tools/natapp_tunnel.py <你的authtoken>")
        print()
        return 1

    print(f"  本地服务：127.0.0.1:{LOCAL_PORT}")
    print(f"  token：{token[:8]}…{token[-4:]}")
    print()

    if not ensure_client(token):
        return 1

    log = ROOT / "data" / "logs" / "natapp.out"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()

    print("  正在启动隧道…")
    print("  " + "-" * 58)

    # 注意：natapp 在管道里会缓冲输出，读 proc.stdout 可能一直空。
    # 所以让它直接写日志文件，我们再轮询文件——这样状态一定看得到。
    proc = subprocess.Popen(
        [str(EXE), f"-authtoken={token}", f"-log={log}", "-loglevel=INFO"],
        cwd=str(BIN),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    url_re = re.compile(r"https?://[a-zA-Z0-9._-]*natapp[a-zA-Z0-9._-]*")
    local_re = re.compile(r"local=([^\s]+)")
    seen: set[str] = set()
    public_url: str | None = None
    local_addr: str | None = None

    try:
        for _ in range(120):                    # 最多等 60 秒
            time.sleep(0.5)
            if not log.exists():
                continue
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if line in seen:
                    continue
                seen.add(line)
                print("  " + line)

                m = url_re.search(line)
                if m:
                    public_url = m.group(0)
                m2 = local_re.search(line)
                if m2:
                    local_addr = m2.group(1)

            if public_url and local_addr:
                break
    except KeyboardInterrupt:
        print("\n  已中断")

    print()
    if not public_url:
        print("  ✗ 没有从日志里读到公网地址。")
        print(f"    日志：{log}")
        print("    常见原因：authtoken 不对，或隧道还没在后台创建好。")
        _stop(proc)
        return 1

    print("  " + "=" * 58)
    print(f"  公网地址：{public_url}")
    print("  " + "=" * 58)

    # 关键检查：隧道的本地端口是不是我们服务的端口
    expected = {f"127.0.0.1:{LOCAL_PORT}", f"localhost:{LOCAL_PORT}"}
    if local_addr and local_addr not in expected:
        print()
        print(f"  隧道指向 {local_addr}，但服务在 127.0.0.1:{LOCAL_PORT}")
        print("  → 尝试在本机做一次端口转发，这样就不用去改网站配置…")

        try:
            want = int(local_addr.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            want = 0

        if want and start_forwarder(want, LOCAL_PORT):
            time.sleep(0.6)
            print(f"  ✓ 已在 127.0.0.1:{want} 做转发 → {LOCAL_PORT}")
            print("    隧道现在应该可以正常访问了")
        else:
            print()
            print(f"  ⚠ 无法自动转发（{want} 端口被占用或没权限）")
            print("     现在访问会返回 502。请改隧道配置：")
            print("       1. 打开 https://natapp.cn/tunnel/lists")
            print("       2. 找到这条隧道，点「编辑」")
            print(f"       3. 把「本地端口」改成 {LOCAL_PORT}，保存")
            print("       4. 关掉这个窗口，重新双击 启动国内穿透.bat")
            print()
            _stop(proc)
            return 1

    print()
    print("  ✓ 隧道正常，手机在任何网络下都能打开上面的地址")
    print("  保持这个窗口开着；按 Ctrl+C 停止隧道")
    print()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        _stop(proc)
    return 0


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


def start_forwarder(listen_port: int, target_port: int) -> bool:
    """在 listen_port 上开一个 TCP 转发到 target_port。

    为什么需要：natapp 的隧道本地端口是在**网站后台**设置的，
    客户端没有参数能覆盖。如果用户建隧道时填了 80（默认值），
    隧道就永远指向 80，访问会 502。

    与其让用户去后台改，不如我们在他选的那个端口上开个转发——
    一个几十行的 TCP 中继就够了，HTTP 完全能过。
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", listen_port))
        srv.listen(64)
    except OSError as e:
        print(f"    （绑定 {listen_port} 失败：{e}）")
        return False

    def accept_loop() -> None:
        while True:
            try:
                client, _ = srv.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(
                    ("127.0.0.1", target_port), timeout=10)
            except OSError:
                client.close()
                continue
            threading.Thread(target=_pipe, args=(client, upstream),
                             daemon=True).start()
            threading.Thread(target=_pipe, args=(upstream, client),
                             daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return True


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
