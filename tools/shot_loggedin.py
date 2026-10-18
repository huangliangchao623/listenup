"""截取「登录后的真实界面」。

之前截图只能用模拟页（因为登录需要 cookie）。这个脚本换了个思路：
用 Chrome DevTools Protocol 把登录 token 直接写进浏览器的 cookie，再刷新，
截到的就是真实登录后的界面，不是假数据。

同时用 CDP 的 deviceMetricsOverride 精确控制视口尺寸——这解决了之前
"无头浏览器窗口尺寸 ≠ 布局视口"导致截图裁切的问题。
"""
from __future__ import annotations

import asyncio
import base64
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = "http://127.0.0.1:8765/"
PORT = 9333
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
SHOTS = ROOT / "data" / "shots"
PROFILE = ROOT / "tools" / "cdp-profile"

USER = ("测试用户", "test123456")


def login() -> tuple[str, str]:
    """走 HTTP 登录，拿回 cookie 名和值。"""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        APP + "api/auth/login",
        data=json.dumps({"username": USER[0], "password": USER[1]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    op.open(req, timeout=20)
    for c in jar:
        if c.name == "listenup_session":
            return c.name, c.value
    raise RuntimeError("登录后没拿到 session cookie")


async def shots(token_name: str, token: str) -> None:
    import websockets

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        targets = json.loads(r.read())
    page = next(t for t in targets if t.get("type") == "page")
    ws_url = page["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=2 ** 26) as ws:
        seq = 0

        async def call(method: str, params: dict | None = None):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps(
                {"id": seq, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
                if msg.get("id") == seq:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        await call("Page.enable")
        await call("Network.enable")

        # 注入登录 cookie
        await call("Network.setCookie", {
            "name": token_name, "value": token,
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": True,
        })

        async def capture(w: int, h: int, dpr: float, mobile: bool,
                          name: str, scroll: int = 0):
            await call("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": h,
                "deviceScaleFactor": dpr, "mobile": mobile,
            })
            await call("Page.navigate", {"url": APP})
            await asyncio.sleep(3.5)
            if scroll:
                await call("Runtime.evaluate", {
                    "expression": f"window.scrollTo(0,{scroll})"})
                await asyncio.sleep(0.8)
            r = await call("Page.captureScreenshot", {"format": "png"})
            out = SHOTS / name
            out.write_bytes(base64.b64decode(r["data"]))
            print(f"  ✓ {name}  ({w}x{h} @{dpr}x)  {out.stat().st_size:,} B")

        await capture(414, 900, 2, True, "real-mobile.png")
        await capture(1280, 900, 1.5, False, "real-desktop.png")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    name, value = login()
    print(f"  已登录，拿到 cookie {name}")

    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--user-data-dir={PROFILE}",
         f"--remote-debugging-port={PORT}", "--no-first-run", APP],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(25):
            time.sleep(1)
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                continue
        asyncio.run(shots(name, value))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
