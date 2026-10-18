"""通过 Chrome DevTools Protocol 读取页面真实布局数值。

截图只能看出"偏了"，量不出"偏多少、为什么偏"。这个脚本启动一个带远程调试
端口的 Edge 应用窗口，然后用 CDP 直接在页面里跑 JS，拿到视口宽度、
body 宽度、登录卡片的实际矩形——这样就不用再猜了。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 9222
APP_URL = "http://127.0.0.1:8765/"
PROFILE = ROOT / "data" / "webview-profile"

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

JS = """
(function () {
  var o = document.getElementById('auth-overlay');
  var c = document.querySelector('.auth-card');
  var b = document.body;
  var h = document.documentElement;
  var cr = c ? c.getBoundingClientRect() : null;
  var br = b.getBoundingClientRect();
  return JSON.stringify({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    dpr: window.devicePixelRatio,
    docScrollW: h.scrollWidth,
    docClientW: h.clientWidth,
    bodyW: Math.round(br.width),
    bodyL: Math.round(br.left),
    overlayW: o ? o.offsetWidth : null,
    cardLeft: cr ? Math.round(cr.left) : null,
    cardWidth: cr ? Math.round(cr.width) : null,
    scrollX: window.scrollX
  });
})()
"""


def launch() -> subprocess.Popen:
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        str(EDGE),
        f"--app={APP_URL}",
        f"--user-data-dir={PROFILE}",
        "--window-size=1180,820",
        "--window-position=120,60",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        # 新配置目录会弹账号同步提示，这些开关把它压掉
        "--disable-sync",
        "--disable-features=msEdgeSyncPromo,Translate",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def targets() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        return json.loads(r.read())


async def measure(ws_url: str) -> dict:
    import websockets

    async with websockets.connect(ws_url, max_size=2**22) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": JS, "returnByValue": True},
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("id") == 1:
                return json.loads(msg["result"]["result"]["value"])


def main() -> int:
    for p in subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process msedge -ErrorAction SilentlyContinue | "
             "Where-Object { $_.MainWindowTitle -like '*听力*' } | "
             "ForEach-Object { $_.Id }"],
            capture_output=True, text=True).stdout.split():
        subprocess.run(["taskkill", "/PID", p, "/F"], capture_output=True)

    proc = launch()
    print(f"已启动 Edge 应用窗口 (PID {proc.pid})，等待加载…")

    ws_url = None
    for _ in range(30):
        time.sleep(1)
        try:
            for t in targets():
                if t.get("type") == "page" and "8765" in t.get("url", ""):
                    ws_url = t["webSocketDebuggerUrl"]
                    break
        except Exception:  # noqa: BLE001
            pass
        if ws_url:
            break

    if not ws_url:
        print("没拿到调试目标")
        return 1

    time.sleep(2)
    data = asyncio.run(measure(ws_url))
    print("\n=== 页面真实布局 ===")
    for k, v in data.items():
        print(f"  {k:14} = {v}")

    print("\n=== 分析 ===")
    iw = data["innerWidth"]
    cw = data["cardWidth"]
    cl = data["cardLeft"]
    if cw and cl is not None:
        expect = (iw - cw) / 2
        print(f"  卡片宽 {cw}，视口宽 {iw} → 居中应在 left={expect:.0f}，实际 {cl}")
        if abs(cl - expect) > 4:
            print(f"  ✗ 偏移 {cl - expect:.0f}px")
        else:
            print("  ✓ 居中正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
