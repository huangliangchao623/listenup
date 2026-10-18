"""英语听力训练 · 桌面客户端

把本地服务包装成一个独立的桌面窗口。

## 为什么不用 pywebview / Electron

一开始用 pywebview，但这条路在这台机器上走不通，原因很具体：

  · pywebview 在 Windows 上靠 pythonnet 调 .NET WinForms
  · pythonnet 默认走 .NET Framework 4.8 —— 实测报
    "Failed to resolve Python.Runtime.Loader.Initialize"
  · 强制 PYTHONNET_RUNTIME=coreclr 走 .NET 8 —— 又报
    "Could not load System.Windows.Forms"，因为 .NET Core **不含**
    WinForms，那在单独的 Microsoft.WindowsDesktop.App 运行时里

而 Electron 要额外 150 MB 运行时和 npm 构建链。

## 最终方案：浏览器的「应用模式」

Edge / Chrome 都支持 `--app=URL`：开一个**没有地址栏、没有标签页**的独立窗口。
它就是一个正常的桌面窗口（有标题栏、能最小化、任务栏有独立条目），
用户看到的和"软件"没有区别，而代价是零。

这也顺带解决了登录态持久化：给一个专用的 user-data-dir，
cookie 和 localStorage 落在那里面，下次打开还是登录状态。

## 关掉窗口 ≠ 停服务

服务是给手机端共用的，所以只关窗口，后台服务继续跑。
需要停止时用「停止服务.bat」或桌面控制台。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from listenup import noflash as _noflash  # noqa: E402

_noflash.install()


# 打包成 exe 之后，__file__ 指向 PyInstaller 的运行时目录，用它算程序目录会全错。
# 运行时以 exe 所在位置为起点，向上找"真正的程序根目录"（同时有 app\run.py
# 和 tools\pydeps 的那一层）。这样 exe 放在根目录还是子目录都能工作。
def _find_root(start: Path) -> Path:
    p = start.resolve()
    for _ in range(8):
        if (p / "app" / "run.py").exists() and (p / "tools" / "pydeps").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve()


if getattr(sys, "frozen", False):
    ROOT = _find_root(Path(sys.executable).parent)
    APP_DIR = ROOT / "app"
    _FROZEN = True
else:
    APP_DIR = Path(__file__).resolve().parent
    ROOT = APP_DIR.parent
    _FROZEN = False

TOOLS = ROOT / "tools"
PORT = int(os.environ.get("LISTENUP_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}/"
PROFILE = ROOT / "data" / "webview-profile"

BROWSER_CANDIDATES = [
    (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", "Edge"),
    (r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe", "Edge"),
    (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", "Chrome"),
    (r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe", "Chrome"),
    (r"%LocalAppData%\Google\Chrome\Application\chrome.exe", "Chrome"),
]


def log(msg: str) -> None:
    """写一行启动日志。

    打包成 --windowed 之后控制台输出全部丢失，出问题时只能靠这个文件。
    """
    try:
        import datetime

        d = ROOT / "data" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "desktop.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def prepare_env() -> None:
    os.environ.setdefault("PYTHONPATH", f"{TOOLS / 'pydeps'};{APP_DIR}")
    os.environ.setdefault("TEMP", str(TOOLS / "tmp"))
    os.environ.setdefault("TMP", str(TOOLS / "tmp"))
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", str(TOOLS / "models"))
    os.environ.setdefault("HF_HUB_CACHE", str(TOOLS / "models" / "hub"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "6")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


# ---------------------------------------------------------------------------
#  找浏览器
# ---------------------------------------------------------------------------

def expand(p: str) -> Path:
    return Path(os.path.expandvars(p))


def find_browser() -> tuple[Path, str] | None:
    for raw, name in BROWSER_CANDIDATES:
        p = expand(raw)
        if p.exists():
            return p, name
    # 兜底：注册表里登记的默认浏览器
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            r"Microsoft\Windows\Shell\Associations\UrlAssociations\http"
            r"\UserChoice")
        prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        cmd_key = winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command")
        cmd = winreg.QueryValueEx(cmd_key, "")[0]
        if cmd.startswith('"'):
            exe = cmd.split('"')[1]
        else:
            exe = cmd.split(" ")[0]
        p = Path(exe)
        if p.exists():
            return p, p.stem
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
#  服务控制
# ---------------------------------------------------------------------------

def healthy(timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(f"{URL}api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def port_busy() -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def pythonw() -> str:
    """找到用来跑后端服务的 Python 解释器。

    优先用随程序附带的 tools\\python：系统里的 python 可能是 Windows 商店版
    的「应用执行别名」，商店包一旦损坏就报 "Python was not found"，
    整个软件都起不来。自带解释器完全不受系统环境影响。
    """
    import shutil

    for name in ("pythonw.exe", "python.exe"):
        p = TOOLS / "python" / name
        if p.exists():
            return str(p)

    if not _FROZEN:
        exe = Path(sys.executable)
        for cand in ("pythonw.exe", "pythonw3.12.exe"):
            p = exe.with_name(cand)
            if p.exists():
                return str(p)

    for name in ("pythonw.exe", "python.exe"):
        found = shutil.which(name)
        if found:
            return found
    return str(Path(sys.executable))


def ensure_server(announce=print) -> bool:
    """确保服务在跑；没跑就拉起来。返回最终是否可用。"""
    if healthy():
        announce("  服务已在运行")
        return True

    if port_busy():
        announce(f"  端口 {PORT} 已被占用，等待响应…")
    else:
        announce("  正在启动本地服务…")
        log_dir = ROOT / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                [pythonw(), str(APP_DIR / "run.py"),
                 "--port", str(PORT), "--log", "--quiet"],
                cwd=str(ROOT), env=dict(os.environ),
                # stdin 也必须重定向：打包成 --windowed 后没有控制台，
                # 标准句柄是无效的，不显式给 DEVNULL 会抛 WinError 6
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except Exception as e:  # noqa: BLE001
            announce(f"  启动失败：{e}")
            return False

    for _ in range(60):            # 最多 30 秒
        time.sleep(0.5)
        if healthy():
            announce("  服务已就绪")
            return True
    announce("  服务 30 秒内没有响应，请查看 data\\logs\\server.log")
    return False


# ---------------------------------------------------------------------------
#  打开应用窗口
# ---------------------------------------------------------------------------

def open_app_window(browser: Path, announce=print) -> bool:
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        str(browser),
        f"--app={URL}",
        f"--user-data-dir={PROFILE}",
        "--window-size=1180,820",
        "--window-position=120,60",
        "--no-first-run",
        "--no-default-browser-check",
        # 全新的配置目录会触发 Edge 的账号同步／新手引导弹窗，
        # 那个窗口会盖住应用界面，所以把这些都关掉。
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-features=msEdgeSyncPromo,Translate,msImplicitSignin",
        "--no-service-autorun",
    ]
    try:
        subprocess.Popen(args, cwd=str(ROOT),
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        announce("  已打开应用窗口")
        return True
    except Exception as e:  # noqa: BLE001
        announce(f"  打开窗口失败：{e}")
        return False


def main() -> int:
    prepare_env()
    log("main() 开始")

    print("=" * 58)
    print("  英语听力训练 · 桌面客户端")
    print("=" * 58)

    if not ensure_server(announce=lambda m: (print(m), log(m))):
        print()
        print("  无法启动服务，这里有几个排查方向：")
        print(f"    · 端口 {PORT} 是否被别的程序占用")
        print("    · 看 data\\logs\\server.log 的最后几行")
        print("    · 改用「启动听力训练.bat」，能看到完整报错")
        # 打包成窗口程序后没有标准输入，input() 会直接抛异常
        if not _FROZEN:
            try:
                input("\n  按回车关闭…")
            except (EOFError, RuntimeError):
                pass
        return 1

    found = find_browser()
    log(f"find_browser -> {found}")
    if not found:
        print("  没找到 Edge 或 Chrome，改用系统默认浏览器打开")
        try:
            os.startfile(URL)     # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            print(f"  打开浏览器失败：{e}")
            return 1
        return 0

    browser, name = found
    print(f"  使用 {name} 的应用模式")
    ok = open_app_window(browser)
    log(f"open_app_window -> {ok}")
    if not ok:
        os.startfile(URL)         # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    # 打包用的是 --console 构建（--windowed 在这台机器上启动不稳定，
    # 实测有时起来有时直接退出，而且看不到任何报错）。
    # 所以改用可靠的控制台构建，启动后立刻把控制台窗口藏掉。
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
        except Exception:  # noqa: BLE001
            pass

    # 打包成无窗口程序后异常会静默消失，这里兜底写一份崩溃日志。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        import traceback

        try:
            logdir = ROOT / "data" / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            (logdir / "desktop-error.log").write_text(
                traceback.format_exc(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, "启动失败，详情见 data\\logs\\desktop-error.log",
                "听力训练", 0x10)
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
