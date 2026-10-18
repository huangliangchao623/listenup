"""Launcher: start the listening trainer and print every address it serves.

Used instead of `uvicorn app.listenup.server:app` so that the bundled Python
dependencies (tools/pydeps) and the sandbox-specific environment are set up
before uvicorn imports anything.

    python app/run.py [--port 8765] [--host 0.0.0.0] [--reload]
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WS = APP_DIR.parent
TOOLS = WS / "tools"

# Make the vendored dependencies importable and redirect every cache the
# sandbox would otherwise deny.
sys.path.insert(0, str(TOOLS / "pydeps"))
sys.path.insert(0, str(APP_DIR))

# 让所有子进程（ffmpeg / yt-dlp / netstat…）都不弹控制台窗口
from listenup import noflash  # noqa: E402

noflash.install()


os.environ.setdefault("TEMP", str(TOOLS / "tmp"))
os.environ.setdefault("TMP", str(TOOLS / "tmp"))
os.environ.setdefault("OMP_NUM_THREADS", "6")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def local_ip() -> str | None:
    """Best-effort LAN address (the one a phone on the same Wi-Fi can reach)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent; this just picks the outbound interface.
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="英语听力训练 · 启动服务")
    ap.add_argument("--host", default="0.0.0.0",
                    help="默认 0.0.0.0，手机才能从局域网访问")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    ap.add_argument("--log", action="store_true",
                    help="把日志写到 data\\logs\\server.log（后台运行时用）")
    ap.add_argument("--quiet", action="store_true",
                    help="不打印启动横幅（无控制台时用）")
    args = ap.parse_args()

    import uvicorn

    from listenup import mdns

    # Publish <hostname>.local via mDNS so phones can reach the service by name
    # instead of by an IP that changes whenever the hotspot is re-created.
    mdns_ok = mdns.start(args.port)
    urls = mdns.access_urls(args.port)

    if not args.quiet:
        print("=" * 62)
        print("  英语听力训练 · 本地服务")
        print("=" * 62)
        print(f"  电脑访问 : {urls['local']}")
        print()
        print("  手机访问（任选一个能打开的）：")
        for key, label in (("mdns", "推荐·名字访问"), ("ip", "当前 IP"),
                           ("hostname", "电脑名")):
            if key in urls:
                print(f"    {label:10} {urls[key]}")
        if not mdns_ok:
            print("    （名字访问未启用：zeroconf 不可用，只能用 IP）")
        print()
        print(f"  数据目录 : {WS / 'data'}")
        print("=" * 62)
        print("  手机端提示：浏览器里选「添加到主屏幕」，可当 App 用")
        print("  按 Ctrl+C 停止\n")

    log_config = None
    if args.log:
        # A background service started with pythonw has no console, so stdout
        # and stderr go nowhere.  Logging to a file is the only way to find out
        # after the fact why it failed to start.
        log_dir = WS / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)-7s %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": str(log_dir / "server.log"),
                    "encoding": "utf-8",
                    "formatter": "plain",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["file"], "level": "INFO",
                            "propagate": False},
                "uvicorn.error": {"handlers": ["file"], "level": "INFO",
                                  "propagate": False},
                "uvicorn.access": {"handlers": ["file"], "level": "WARNING",
                                   "propagate": False},
            },
        }
        try:
            with open(log_dir / "server.log", "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 58}\n服务启动 "
                        f"{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}"
                        f"  pid={__import__('os').getpid()}\n")
        except OSError:
            pass

    try:
        uvicorn.run(
            "listenup.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
            log_config=log_config,
        )
    finally:
        # Withdraw the mDNS advertisement so a stale name does not linger on
        # the network after the service stops.
        mdns.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
