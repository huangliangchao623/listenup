"""英语听力训练 · 桌面控制台（详细版）

一个把"服务状态、访问地址、用户、素材、日志、诊断、设置"全部摊开的本地工具。

设计原则：
  · **只读优先**：查数据用 SQLite 的只读连接（`mode=ro`），绝不和正在运行的服务
    抢写锁。要写也走 API 或明确的操作按钮。
  · **不阻塞界面**：启动/停止服务这类可能耗时几秒的动作放线程里跑，主线程只管刷新。
  · **出错不崩**：每个数据源都单独 try，某一块坏了只显示"读取失败"，不影响其他块。
  · **所见即所得**：所有地址都给复制按钮；诊断项给出"怎么办"而不只是"❌"。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

# 先挂上「不弹控制台窗口」的补丁，再导入别的东西
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from listenup import noflash as _noflash  # noqa: E402

_noflash.install()

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
TOOLS = ROOT / "tools"
DATA = ROOT / "data"
LOGS = DATA / "logs"
PORT = 8765

# ---- 配色（和网页界面统一） ------------------------------------------------
BG = "#0b0d12"
CARD = "#141821"
CARD2 = "#1b2029"
CARD3 = "#232a36"
TEXT = "#e8ecf3"
DIM = "#8b95a7"
DIM2 = "#5f6878"
ACCENT = "#0a84ff"
GREEN = "#30d158"
RED = "#ff453a"
ORANGE = "#ff9f0a"
PURPLE = "#5e5ce6"


# ===========================================================================
#  服务控制
# ===========================================================================

def pythonw_path() -> str:
    """无窗口的 Python 解释器，避免服务弹出一个黑框。

    优先用随程序附带的 tools\\python —— 系统里的 python 可能是 Windows 商店
    版的「应用执行别名」，它依赖商店包，一旦商店包损坏或权限变化就会
    报 "Python was not found"，整个软件都起不来。
    自带解释器则完全不受系统环境影响。
    """
    for name in ("pythonw.exe", "python.exe"):
        p = TOOLS / "python" / name
        if p.exists():
            return str(p)

    exe = Path(sys.executable)
    for cand in ("pythonw.exe", "pythonw3.12.exe"):
        p = exe.with_name(cand)
        if p.exists():
            return str(p)
    return str(exe)


def python_exe() -> str:
    """带控制台的解释器（给需要在窗口里跑的场景用）。"""
    p = TOOLS / "python" / "python.exe"
    if p.exists():
        return str(p)
    return sys.executable


def service_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{TOOLS / 'pydeps'};{APP_DIR}"
    env["TEMP"] = str(TOOLS / "tmp")
    env["TMP"] = str(TOOLS / "tmp")
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["HF_HOME"] = str(TOOLS / "models")
    env["HF_HUB_CACHE"] = str(TOOLS / "models" / "hub")
    env["HF_HUB_DISABLE_SYMLINKS"] = "1"
    env["HF_HUB_DISABLE_XET"] = "1"
    env["OMP_NUM_THREADS"] = "6"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def listening_pids(port: int = PORT) -> list[int]:
    """端口上处于 LISTENING 的进程号；没起来时返回空列表。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, errors="replace", timeout=15).stdout
    except Exception:  # noqa: BLE001
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if f":{port}" not in line or "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[-1].isdigit():
            pid = int(parts[-1])
            if pid and pid not in pids:
                pids.append(pid)
    return pids


def healthy(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def fetch_json(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}",
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start_service() -> tuple[bool, str]:
    if healthy():
        return True, "服务已经在运行了"
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [pythonw_path(), str(APP_DIR / "run.py"),
             "--port", str(PORT), "--log", "--quiet"],
            cwd=str(ROOT), env=service_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except Exception as e:  # noqa: BLE001
        return False, f"启动失败：{e}"
    for _ in range(60):
        time.sleep(0.5)
        if healthy():
            return True, "服务已启动"
    return False, "30 秒内没有响应，请看日志"


def stop_service() -> tuple[bool, str]:
    pids = listening_pids()
    if not pids:
        return True, "服务本来就没有运行"
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=15)
        except Exception:  # noqa: BLE001
            pass
    for _ in range(20):
        time.sleep(0.4)
        if not listening_pids():
            return True, "服务已停止"
    return False, "尝试停止但端口仍被占用，可能需要管理员权限"


def restart_service() -> tuple[bool, str]:
    stop_service()
    time.sleep(1.0)
    return start_service()


# ---- 开机自启 --------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ListenUp"


def autostart_installed() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def install_autostart() -> tuple[bool, str]:
    try:
        import winreg
        target = f'"{ROOT / "开机启动.bat"}"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, target)
        return True, "已设置开机自启"
    except Exception as e:  # noqa: BLE001
        return False, f"设置失败：{e}"


def uninstall_autostart() -> tuple[bool, str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_NAME)
        return True, "已取消开机自启"
    except FileNotFoundError:
        return True, "本来就没有设置自启"
    except Exception as e:  # noqa: BLE001
        return False, f"取消失败：{e}"


# ---- 内网穿透 --------------------------------------------------------------
def tunnel_pid() -> int | None:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq natapp.exe",
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True,
                             errors="replace", timeout=15).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("natapp"):
            try:
                return int(parts[1])
            except ValueError:
                pass
    return None


def tunnel_url() -> str | None:
    """从 natapp 日志里抓最近一次的公网地址。"""
    log = LOGS / "natapp.out"
    if not log.exists():
        return None
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    import re
    found = re.findall(r"https?://[A-Za-z0-9._-]*natapp[A-Za-z0-9._-]*", text)
    return found[-1] if found else None


def tunnel_local() -> str | None:
    log = LOGS / "natapp.out"
    if not log.exists():
        return None
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    import re
    found = re.findall(r"local=([^\s]+)", text)
    return found[-1] if found else None


# ===========================================================================
#  数据读取（只读连接，不打扰运行中的服务）
# ===========================================================================

def db_query(sql: str, params: tuple = ()) -> list[dict]:
    import sqlite3

    path = DATA / "library.sqlite3"
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return []


def db_write(fn) -> tuple[bool, str]:
    """在写事务里执行 fn(conn)，失败返回原因。

    服务在跑的时候数据库是 WAL 模式，短写可以并发；这里给 10 秒超时，
    避免和服务抢占时直接报 "database is locked"。
    """
    import sqlite3

    path = DATA / "library.sqlite3"
    if not path.exists():
        return False, "数据库还不存在"
    try:
        con = sqlite3.connect(str(path), timeout=10)
        try:
            with con:
                fn(con)
            return True, "完成"
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def hash_password(password: str) -> str:
    """用服务端同一套算法生成密码哈希（PBKDF2-HMAC-SHA256, 60 万次）。"""
    import hashlib
    import secrets

    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 600_000)
    return f"pbkdf2_sha256$600000${salt}${dk.hex()}"


def dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:  # noqa: BLE001
        return 0


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "-"


def fmt_dur(sec: float | None) -> str:
    if not sec:
        return "0:00"
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def process_mem(pid: int) -> str:
    """某个进程占多少内存。

    用 ctypes 直接调 GetProcessMemoryInfo，不解析 tasklist 的输出——
    实测 `tasklist /FI "PID eq xxx"` 在某些 PID 上会返回
    "No tasks are running which match the specified criteria"，不可靠。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return "-"
        try:
            c = PMC()
            c.cb = ctypes.sizeof(c)
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    h, ctypes.byref(c), c.cb):
                return human(c.WorkingSetSize)
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return "-"


# ===========================================================================
#  诊断项
# ===========================================================================

def diag_deps() -> list[tuple[bool, str, str]]:
    """(是否正常, 名称, 说明)"""
    rows: list[tuple[bool, str, str]] = []
    try:
        import importlib.metadata as md
    except Exception:  # noqa: BLE001
        return [(False, "依赖检查", "无法读取包信息")]

    sys.path.insert(0, str(TOOLS / "pydeps"))
    want = [
        ("fastapi", "Web 框架"),
        ("uvicorn", "ASGI 服务器"),
        ("faster-whisper", "语音识别"),
        ("ctranslate2", "识别推理引擎"),
        ("av", "音视频解码"),
        ("onnxruntime", "离线语音合成"),
        ("yt-dlp", "平台链接下载"),
        ("qrcode", "二维码（控制台用）"),
    ]
    for pkg, desc in want:
        try:
            v = md.version(pkg)
            rows.append((True, pkg, f"{v} · {desc}"))
        except Exception:  # noqa: BLE001
            rows.append((False, pkg, f"未安装 · {desc} 需要它"))
    return rows


def diag_assets() -> list[tuple[bool, str, str]]:
    rows: list[tuple[bool, str, str]] = []

    ffmpeg = TOOLS / "bin" / "ffmpeg.exe"
    rows.append((ffmpeg.exists(), "ffmpeg",
                 "已就位" if ffmpeg.exists() else "缺失，切音频和下载都会失败"))

    ffprobe = TOOLS / "bin" / "ffprobe.exe"
    rows.append((ffprobe.exists(), "ffprobe",
                 "已就位" if ffprobe.exists() else "缺失，无法读取媒体时长"))

    models = TOOLS / "models"
    whisper_dirs = list(models.rglob("*small.en*")) if models.exists() else []
    if whisper_dirs:
        rows.append((True, "识别模型",
                     f"small.en · {human(dir_size(models))}"))
    else:
        rows.append((False, "识别模型", "没找到 small.en，导入会先下载约 500 MB"))

    voices = list((TOOLS / "models").rglob("*.onnx")) if models.exists() else []
    rows.append((bool(voices), "离线语音",
                 f"{len(voices)} 个音色" if voices else "没有音色，朗读功能不可用"))

    natapp = TOOLS / "bin" / "natapp.exe"
    rows.append((natapp.exists(), "内网穿透客户端",
                 "已就位" if natapp.exists() else "缺失，手机在外网访问不了"))
    return rows


def diag_runtime() -> list[tuple[bool, str, str]]:
    rows: list[tuple[bool, str, str]] = []

    rows.append((True, "Python", f"{sys.version.split()[0]} · {sys.executable}"))

    try:
        u = shutil.disk_usage(ROOT)
        free_gb = u.free / (1024 ** 3)
        rows.append((free_gb > 5, "磁盘剩余",
                     f"{free_gb:.1f} GB 可用"
                     + ("" if free_gb > 5 else " · 空间偏少，导入长视频可能失败")))
    except Exception:  # noqa: BLE001
        rows.append((False, "磁盘剩余", "读取失败"))

    # 数据目录可写
    try:
        probe = DATA / ".console-write-test"
        DATA.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rows.append((True, "数据目录可写", str(DATA)))
    except Exception as e:  # noqa: BLE001
        rows.append((False, "数据目录可写", f"写入失败：{e}"))

    # 端口
    pids = listening_pids()
    rows.append((bool(pids), f"端口 {PORT}",
                 f"被 PID {', '.join(map(str, pids))} 占用" if pids else "空闲（服务未运行）"))

    # 局域网
    ip = lan_ip()
    rows.append((bool(ip), "局域网地址", ip or "没拿到，可能没连网络"))

    # 数据库
    dbp = DATA / "library.sqlite3"
    if dbp.exists():
        rows.append((True, "数据库", f"{human(dbp.stat().st_size)} · {dbp.name}"))
    else:
        rows.append((False, "数据库", "还没创建，第一次启动服务时会自动建"))
    return rows


# ===========================================================================
#  主窗口
# ===========================================================================

class Console(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("听力训练 · 控制台")
        self.geometry("1120x760")
        self.minsize(940, 640)
        self.configure(bg=BG)

        self._started_at: float | None = None
        self._last_log_sig: tuple | None = None
        self._log_paused = False
        self._auto_scroll = tk.BooleanVar(value=True)
        self._busy = False

        self._style()
        self._build()
        self._tick()          # 启动时刷一次；之后只在手动触发时刷新

    # ---------------------------------------------------------------- 样式

    def _style(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(10, 8, 10, 0))
        st.configure("TNotebook.Tab", background=CARD, foreground=DIM,
                     padding=(18, 9), borderwidth=0)
        st.map("TNotebook.Tab",
               background=[("selected", CARD2)],
               foreground=[("selected", TEXT)])
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, borderwidth=0, rowheight=27)
        st.configure("Treeview.Heading", background=CARD2, foreground=DIM,
                     borderwidth=0, padding=(8, 7))
        st.map("Treeview.Heading", background=[("active", CARD3)])
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#ffffff")])
        st.configure("TCombobox", fieldbackground=CARD3, background=CARD3,
                     foreground=TEXT, arrowcolor=DIM)
        st.configure("Vertical.TScrollbar", background=CARD2,
                     troughcolor=BG, borderwidth=0, arrowcolor=DIM)

    # ---------------------------------------------------------------- 布局

    def _build(self) -> None:
        # ---- 顶部状态条 ----
        top = tk.Frame(self, bg=CARD, height=76)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        left = tk.Frame(top, bg=CARD)
        left.pack(side="left", fill="y", padx=(20, 0))

        self.dot = tk.Label(left, text="●", bg=CARD, fg=DIM2,
                            font=("Segoe UI", 22))
        self.dot.pack(side="left")

        txt = tk.Frame(left, bg=CARD)
        txt.pack(side="left", padx=(10, 0), pady=14)
        self.lb_state = tk.Label(txt, text="正在检查…", bg=CARD, fg=TEXT,
                                 font=("Microsoft YaHei UI", 14, "bold"),
                                 anchor="w")
        self.lb_state.pack(anchor="w")
        self.lb_sub = tk.Label(txt, text="", bg=CARD, fg=DIM,
                               font=("Microsoft YaHei UI", 9), anchor="w")
        self.lb_sub.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(top, bg=CARD)
        right.pack(side="right", padx=20, pady=16)
        self.btn_refresh = self._btn(right, "⟳ 刷新", self._manual_refresh, "ghost", 8)
        self.btn_refresh.pack(side="left", padx=(0, 10))
        self.btn_start = self._btn(right, "启动服务", self._do_start, "primary", 12)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = self._btn(right, "停止", self._do_stop, "danger", 8)
        self.btn_stop.pack(side="left", padx=4)
        self.btn_restart = self._btn(right, "重启", self._do_restart, "ghost", 8)
        self.btn_restart.pack(side="left", padx=4)

        # ---- 标签页 ----
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        self.tab_overview = ttk.Frame(self.nb)
        self.tab_users = ttk.Frame(self.nb)
        self.tab_lib = ttk.Frame(self.nb)
        self.tab_logs = ttk.Frame(self.nb)
        self.tab_diag = ttk.Frame(self.nb)
        self.tab_set = ttk.Frame(self.nb)

        self.nb.add(self.tab_overview, text="概览")
        self.nb.add(self.tab_users, text="用户")
        self.nb.add(self.tab_lib, text="素材")
        self.nb.add(self.tab_logs, text="日志")
        self.nb.add(self.tab_diag, text="诊断")
        self.nb.add(self.tab_set, text="设置")

        self._build_overview()
        self._build_users()
        self._build_lib()
        self._build_logs()
        self._build_diag()
        self._build_settings()

        # 切到某个标签页时再按需加载内容。
        # 诊断页要 import 一堆包，不能每 3 秒重跑一次，只在进页面时算。
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- 底部状态栏 ----
        bottom = tk.Frame(self, bg=BG, height=26)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        self.lb_footer = tk.Label(bottom, text="就绪", bg=BG, fg=DIM2,
                                  font=("Microsoft YaHei UI", 9), anchor="w")
        self.lb_footer.pack(side="left", padx=16)
        self.lb_clock = tk.Label(bottom, text="", bg=BG, fg=DIM2,
                                 font=("Consolas", 9), anchor="e")
        self.lb_clock.pack(side="right", padx=16)

    # ---------------------------------------------------------------- 概览页

    def _build_overview(self) -> None:
        wrap = tk.Frame(self.tab_overview, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        # 左：访问地址 | 右：二维码
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")

        left = self._card(row)
        left.pack(side="left", fill="both", expand=True)

        self._h(left, "访问地址")
        self.url_rows: dict[str, dict] = {}
        for key, label in (("local", "本机"), ("mdns", "局域网名字"),
                           ("ip", "局域网 IP"), ("hostname", "电脑名"),
                           ("public", "公网（手机在外面用）")):
            self.url_rows[key] = self._url_row(left, label)

        # 注意：pack_propagate(False) 之后必须显式给宽高，
        # 否则框架会塌成一条线，里面的二维码画布根本看不见。
        right_card = self._card(row, width=238, height=290)
        right_card.pack(side="right", padx=(12, 0))
        right_card.pack_propagate(False)
        self._h(right_card, "手机扫码打开")
        self.qr_canvas = tk.Canvas(right_card, width=190, height=190,
                                   bg="#ffffff", highlightthickness=0)
        self.qr_canvas.pack(pady=(0, 6))
        self.qr_hint = tk.Label(right_card, text="", bg=CARD, fg=DIM,
                                font=("Microsoft YaHei UI", 8),
                                wraplength=200, justify="center")
        self.qr_hint.pack()

        # 服务信息
        info = self._card(wrap)
        info.pack(fill="x", pady=(12, 0))
        self._h(info, "服务信息")
        self.info_labels: dict[str, tk.Label] = {}
        grid = tk.Frame(info, bg=CARD)
        grid.pack(fill="x")
        pairs = [("端口", "port"), ("进程号", "pid"), ("内存", "mem"),
                 ("启动时间", "uptime"), ("运行时长", "duration"),
                 ("数据目录", "data"), ("日志文件", "log")]
        for i, (label, key) in enumerate(pairs):
            col = i % 2
            r = i // 2
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=r, column=col, sticky="w", padx=(0, 34), pady=3)
            tk.Label(cell, text=label + "  ", bg=CARD, fg=DIM2,
                     font=("Microsoft YaHei UI", 9)).pack(side="left")
            lb = tk.Label(cell, text="-", bg=CARD, fg=TEXT,
                          font=("Consolas", 9), anchor="w")
            lb.pack(side="left")
            self.info_labels[key] = lb

        # 快捷操作
        quick = self._card(wrap)
        quick.pack(fill="x", pady=(12, 0))
        self._h(quick, "快捷操作")
        bar = tk.Frame(quick, bg=CARD)
        bar.pack(fill="x")
        for text, cmd in (
            ("打开网页界面", lambda: self._open(f"http://127.0.0.1:{PORT}/")),
            ("素材文件夹", lambda: self._open(str(DATA / "media"))),
            ("日志目录", lambda: self._open(str(LOGS))),
            ("数据目录", lambda: self._open(str(DATA))),
            ("刷新", self.refresh),
        ):
            self._btn(bar, text, cmd, "ghost", 0).pack(side="left", padx=(0, 8))

    def _url_row(self, parent: tk.Frame, label: str) -> dict:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=CARD, fg=DIM2, width=16, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        value = tk.Label(row, text="—", bg=CARD, fg=DIM,
                         font=("Consolas", 9), anchor="w")
        value.pack(side="left", fill="x", expand=True)
        btn = self._btn(row, "复制", lambda: None, "tiny", 0)
        btn.pack(side="right")
        return {"value": value, "button": btn}

    # ---------------------------------------------------------------- 用户页

    def _build_users(self) -> None:
        wrap = tk.Frame(self.tab_users, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x", pady=(0, 8))
        for text, cmd, kind in (
            ("刷新", self.refresh_users, "ghost"),
            ("新建用户", self._user_add, "primary"),
            ("重置密码", self._user_reset, "ghost"),
            ("踢下线", self._user_kick, "ghost"),
            ("删除用户", self._user_del, "danger"),
        ):
            self._btn(bar, text, cmd, kind, 0).pack(side="left", padx=(0, 8))
        self.lb_user_sum = tk.Label(bar, text="", bg=BG, fg=DIM,
                                    font=("Microsoft YaHei UI", 9))
        self.lb_user_sum.pack(side="right")

        # 上半：列表
        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="both", expand=True)

        cols = ("username", "email", "display", "role", "lessons",
                "sent", "done", "devices", "last", "created")
        heads = ("用户名", "邮箱", "昵称", "角色", "课程",
                 "总句数", "已学", "设备", "最后登录", "注册时间")
        widths = (110, 165, 90, 56, 48, 62, 62, 46, 128, 128)
        self.tv_users = ttk.Treeview(top, columns=cols, show="headings",
                                     selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.tv_users.heading(c, text=h)
            self.tv_users.column(c, width=w,
                                 anchor="w" if c in ("username", "email", "display")
                                 else "center")
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tv_users.yview)
        self.tv_users.configure(yscrollcommand=sb.set)
        self.tv_users.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv_users.bind("<<TreeviewSelect>>", lambda e: self._show_user_detail())

        # 下半：选中用户的详细信息
        detail = self._card(wrap)
        detail.pack(fill="x", pady=(10, 0))
        self._h(detail, "用户详情")
        self.lb_user_detail = tk.Label(
            detail, text="选中上面某一行查看详细信息",
            bg=CARD, fg=DIM2, font=("Consolas", 9),
            justify="left", anchor="nw", wraplength=1040)
        self.lb_user_detail.pack(fill="x")

    # ---------------------------------------------------------------- 素材页

    def _build_lib(self) -> None:
        wrap = tk.Frame(self.tab_lib, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x", pady=(0, 8))
        for text, cmd, kind in (
            ("刷新", self.refresh_lib, "ghost"),
            ("打开素材目录", lambda: self._open(str(DATA / "media")), "ghost"),
            ("打开片段目录", lambda: self._open(str(DATA / "clips")), "ghost"),
            ("删除选中课程", self._lib_del, "danger"),
        ):
            self._btn(bar, text, cmd, kind, 0).pack(side="left", padx=(0, 8))
        self.lb_lib_sum = tk.Label(bar, text="", bg=BG, fg=DIM,
                                   font=("Microsoft YaHei UI", 9))
        self.lb_lib_sum.pack(side="right")

        cols = ("title", "sent", "dur", "owner", "source", "video", "created")
        heads = ("标题", "句数", "时长", "归属", "来源", "视频", "导入时间")
        widths = (360, 70, 80, 110, 90, 60, 140)
        self.tv_lib = ttk.Treeview(wrap, columns=cols, show="headings",
                                   selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.tv_lib.heading(c, text=h)
            self.tv_lib.column(c, width=w,
                               anchor="w" if c == "title" else "center")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tv_lib.yview)
        self.tv_lib.configure(yscrollcommand=sb.set)
        self.tv_lib.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------------------------------------------------------- 日志页

    def _build_logs(self) -> None:
        wrap = tk.Frame(self.tab_logs, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x", pady=(0, 8))

        tk.Label(bar, text="文件", bg=BG, fg=DIM2,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.cb_log = ttk.Combobox(bar, values=self._log_files(),
                                   state="readonly", width=18)
        if self.cb_log["values"]:
            self.cb_log.current(0)
        self.cb_log.pack(side="left", padx=(6, 14))
        self.cb_log.bind("<<ComboboxSelected>>", lambda e: self._force_log_reload())

        self._btn(bar, "暂停", self._log_toggle, "ghost", 0).pack(side="left", padx=4)
        self._btn(bar, "清屏", self._log_clear, "ghost", 0).pack(side="left", padx=4)
        self._btn(bar, "打开文件", self._log_open, "ghost", 0).pack(side="left", padx=4)
        tk.Checkbutton(bar, text="自动滚到底", variable=self._auto_scroll,
                       bg=BG, fg=DIM, selectcolor=CARD,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Microsoft YaHei UI", 9),
                       borderwidth=0).pack(side="left", padx=(14, 0))

        self.lb_log_sig = tk.Label(bar, text="", bg=BG, fg=DIM2,
                                   font=("Consolas", 8))
        self.lb_log_sig.pack(side="right")

        body = tk.Frame(wrap, bg=CARD)
        body.pack(fill="both", expand=True)
        self.txt_log = tk.Text(body, bg=CARD, fg=TEXT, insertbackground=TEXT,
                               font=("Consolas", 9), borderwidth=0,
                               wrap="none", padx=10, pady=8)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt_log.tag_configure("err", foreground=RED)
        self.txt_log.tag_configure("warn", foreground=ORANGE)
        self.txt_log.tag_configure("time", foreground=DIM2)

    def _log_files(self) -> list[str]:
        if not LOGS.exists():
            return []
        files = sorted(LOGS.glob("*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files] or ["(没有日志文件)"]

    # ---------------------------------------------------------------- 诊断页

    def _build_diag(self) -> None:
        wrap = tk.Frame(self.tab_diag, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "重新检查", self.refresh_diag, "primary", 0).pack(side="left")
        self.lb_diag_sum = tk.Label(bar, text="", bg=BG, fg=DIM,
                                    font=("Microsoft YaHei UI", 9))
        self.lb_diag_sum.pack(side="left", padx=14)

        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.diag_inner = inner

    # ---------------------------------------------------------------- 设置页

    def _build_settings(self) -> None:
        wrap = tk.Frame(self.tab_set, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        # 开机自启
        c1 = self._card(wrap)
        c1.pack(fill="x")
        self._h(c1, "开机自启")
        row = tk.Frame(c1, bg=CARD)
        row.pack(fill="x")
        self.lb_auto = tk.Label(row, text="检查中…", bg=CARD, fg=DIM,
                                font=("Microsoft YaHei UI", 9))
        self.lb_auto.pack(side="left")
        self.btn_auto = self._btn(row, "安装", self._toggle_autostart, "primary", 0)
        self.btn_auto.pack(side="right")

        # 内网穿透
        c2 = self._card(wrap)
        c2.pack(fill="x", pady=(12, 0))
        self._h(c2, "内网穿透（手机在外网访问）")
        row2 = tk.Frame(c2, bg=CARD)
        row2.pack(fill="x")
        self.lb_tunnel = tk.Label(row2, text="检查中…", bg=CARD, fg=DIM,
                                  font=("Microsoft YaHei UI", 9), anchor="w")
        self.lb_tunnel.pack(side="left")
        self.btn_tunnel = self._btn(row2, "启动", self._toggle_tunnel, "primary", 0)
        self.btn_tunnel.pack(side="right")
        self.lb_tunnel_url = tk.Label(c2, text="", bg=CARD, fg=ACCENT,
                                      font=("Consolas", 10), anchor="w")
        self.lb_tunnel_url.pack(fill="x", pady=(6, 0))

        # 危险操作
        c3 = self._card(wrap)
        c3.pack(fill="x", pady=(12, 0))
        self._h(c3, "维护")
        row3 = tk.Frame(c3, bg=CARD)
        row3.pack(fill="x")
        self._btn(row3, "清空识别缓存", self._clear_cache, "ghost", 0).pack(side="left", padx=(0, 8))
        self._btn(row3, "刷新全部", self.refresh, "ghost", 0).pack(side="left", padx=(0, 8))
        tk.Label(c3, text="识别缓存只用来自动切句加速，删掉不会丢课程。",
                 bg=CARD, fg=DIM2, font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(fill="x", pady=(8, 0))

    # ---------------------------------------------------------------- 组件工厂

    def _card(self, parent: tk.Frame, width: int | None = None,
              height: int | None = None) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=16, pady=12,
                     highlightbackground="#20262f", highlightthickness=1)
        if width:
            f.configure(width=width)
        if height:
            f.configure(height=height)
        return f

    def _h(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold"),
                 anchor="w").pack(fill="x", pady=(0, 8))

    def _btn(self, parent, text, cmd, kind="normal", _w=0) -> tk.Button:
        colors = {
            "primary": (ACCENT, "#ffffff", ACCENT),
            "danger": (CARD3, RED, "#2a1518"),
            "ghost": (CARD3, TEXT, CARD2),
            "tiny": (CARD3, DIM, CARD2),
        }
        bg, fg, hover = colors.get(kind, (CARD3, TEXT, CARD2))
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=hover, activeforeground=fg,
                      relief="flat", borderwidth=0, cursor="hand2",
                      font=("Microsoft YaHei UI", 9, "bold" if kind == "primary" else "normal"),
                      padx=14 if kind != "tiny" else 8,
                      pady=6 if kind != "tiny" else 2)
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    # ---------------------------------------------------------------- 手动刷新

    def _tick(self) -> None:
        """刷新一次全部信息。

        这里**不做定时器**：早期版本每 3 秒自动刷一次，会让人看不清
        列表（选中行被刷掉）也浪费 CPU。现在只在你点「刷新」、
        切换标签页、或执行完操作后才刷新。
        """
        try:
            self.lb_clock.configure(
                text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _manual_refresh(self) -> None:
        self._tick()
        self.lb_footer.configure(text=f"已刷新 · {datetime.now():%H:%M:%S}")


    def _on_tab_changed(self, _event=None) -> None:
        """切页面时加载该页内容（诊断页比较重，只在这里算一次）。"""
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:  # noqa: BLE001
            return
        if idx == 1:
            self.refresh_users()
        elif idx == 2:
            self.refresh_lib()
        elif idx == 3:
            self._force_log_reload()
        elif idx == 4:
            self.refresh_diag()

    def refresh(self) -> None:
        self._refresh_status()
        self._refresh_urls()
        self._refresh_info()
        self._refresh_log()
        self._refresh_settings()
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:  # noqa: BLE001
            return
        if idx == 1:
            self.refresh_users()
        elif idx == 2:
            self.refresh_lib()

    # ---- 状态 ----
    def _refresh_status(self) -> None:
        running = healthy()
        pids = listening_pids()
        if running:
            self.dot.configure(fg=GREEN)
            self.lb_state.configure(text="服务运行中", fg=TEXT)
            mem = process_mem(pids[0]) if pids else "-"
            self.lb_sub.configure(
                text=f"端口 {PORT} · PID {pids[0] if pids else '-'} · 内存 {mem}")
            if self._started_at is None:
                self._started_at = time.time()
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.dot.configure(fg=RED if pids else DIM2)
            self.lb_state.configure(text="服务已停止" if not pids else "端口被占用，服务无响应",
                                    fg=RED if pids else DIM)
            self.lb_sub.configure(
                text=f"端口 {PORT} · " + (f"PID {pids[0]} 未响应" if pids else "点击右上角启动"))
            self._started_at = None
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
        self.lb_footer.configure(
            text=f"数据目录 {DATA}   ·   日志 {LOGS}   ·   手动刷新（点右上角 ⟳ 刷新）")

    # ---- 地址 ----
    def _refresh_urls(self) -> None:
        access = fetch_json("/api/access") if healthy() else None
        # /api/access 返回的是 {urls: {...}, mdns: bool, hostname: str, lanIp: str}
        # —— mdns 是布尔值，取成字符串会变成 "1"，所以要走 urls 子对象。
        urls_api = (access or {}).get("urls") or {}
        ip = (access or {}).get("lanIp") or lan_ip() or ""
        host = (access or {}).get("hostname") or socket.gethostname()

        values = {
            "local": urls_api.get("local") or f"http://127.0.0.1:{PORT}",
            "mdns": urls_api.get("mdns") or f"http://listenup.local:{PORT}",
            "ip": urls_api.get("ip") or (f"http://{ip}:{PORT}" if ip else ""),
            "hostname": urls_api.get("hostname") or f"http://{host}:{PORT}",
            "public": tunnel_url() or "",
        }

        best = ""
        for key, row in self.url_rows.items():
            v = values.get(key, "")
            if v:
                row["value"].configure(text=v, fg=TEXT if key in ("ip", "public") else DIM)
                row["button"].configure(
                    state="normal", command=lambda val=v: self._copy(val))
                if key == "public" or (not best and key == "ip"):
                    best = v
            else:
                row["value"].configure(
                    text="—" if key != "public" else "隧道未启动",
                    fg=DIM2)
                row["button"].configure(state="disabled")

        self._draw_qr(best or f"http://127.0.0.1:{PORT}",
                      "手机连同一个 WiFi 扫码" if not values["public"]
                      else "手机在任何网络下都能扫码")

    def _draw_qr(self, data: str, hint: str) -> None:
        self.qr_hint.configure(text=hint)
        self.qr_canvas.delete("all")
        try:
            import qrcode
        except Exception:  # noqa: BLE001
            self.qr_canvas.create_text(95, 95, text="二维码\n不可用",
                                       fill="#666", font=("Microsoft YaHei UI", 10),
                                       justify="center")
            return
        try:
            qr = qrcode.QRCode(border=1, box_size=1)
            qr.add_data(data)
            qr.make(fit=True)
            m = qr.get_matrix()
            n = len(m)
            size = 186
            cell = size / n
            for y, rowv in enumerate(m):
                for x, v in enumerate(rowv):
                    if v:
                        self.qr_canvas.create_rectangle(
                            x * cell, y * cell, (x + 1) * cell, (y + 1) * cell,
                            fill="#000000", outline="")
        except Exception:  # noqa: BLE001
            self.qr_canvas.create_text(95, 95, text="二维码\n生成失败",
                                       fill="#666", font=("Microsoft YaHei UI", 10),
                                       justify="center")

    # ---- 服务信息 ----
    def _refresh_info(self) -> None:
        pids = listening_pids()
        self.info_labels["port"].configure(text=str(PORT))
        self.info_labels["pid"].configure(text=str(pids[0]) if pids else "-")
        self.info_labels["mem"].configure(text=process_mem(pids[0]) if pids else "-")
        if self._started_at:
            self.info_labels["uptime"].configure(
                text=datetime.fromtimestamp(self._started_at).strftime("%H:%M:%S"))
            sec = int(time.time() - self._started_at)
            self.info_labels["duration"].configure(
                text=f"{sec // 3600}时{sec % 3600 // 60}分{sec % 60}秒")
        else:
            self.info_labels["uptime"].configure(text="-")
            self.info_labels["duration"].configure(text="-")
        self.info_labels["data"].configure(text=str(DATA))
        self.info_labels["log"].configure(
            text=(LOGS / "server.log").name if (LOGS / "server.log").exists() else "-")

    # ---- 日志 ----
    def _log_toggle(self) -> None:
        self._log_paused = not self._log_paused
        for w in self.tab_logs.winfo_children():
            pass
        self.lb_log_sig.configure(text="已暂停" if self._log_paused else "")

    def _force_log_reload(self) -> None:
        self._last_log_sig = None
        self._refresh_log(force=True)

    def _log_clear(self) -> None:
        self.txt_log.delete("1.0", "end")

    def _log_open(self) -> None:
        name = self.cb_log.get()
        p = LOGS / name if name else LOGS / "server.log"
        if p.exists():
            try:
                os.startfile(str(p))   # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                self._open(str(LOGS))

    def _refresh_log(self, force: bool = False) -> None:
        if self._log_paused and not force:
            return
        name = self.cb_log.get() or "server.log"
        path = LOGS / name
        if not path.exists():
            return
        try:
            st = path.stat()
            sig = (st.st_size, st.st_mtime)
        except Exception:  # noqa: BLE001
            return
        if sig == self._last_log_sig:
            return
        self._last_log_sig = sig

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        lines = text.splitlines()[-500:]

        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        for ln in lines:
            tag = ()
            low = ln.lower()
            if "error" in low or "traceback" in low or "失败" in ln:
                tag = ("err",)
            elif "warn" in low or "警告" in ln:
                tag = ("warn",)
            # 时间戳单独上色
            if len(ln) > 10 and ln[2:3] == ":" and ln[5:6] == ":":
                self.txt_log.insert("end", ln[:10], ("time",))
                self.txt_log.insert("end", ln[10:] + "\n", tag)
            else:
                self.txt_log.insert("end", ln + "\n", tag)
        self.txt_log.configure(state="disabled")
        if self._auto_scroll.get():
            self.txt_log.see("end")
        self.lb_log_sig.configure(text=f"{len(lines)} 行 · {human(st.st_size)}")

    # ---- 设置 ----
    def _refresh_settings(self) -> None:
        # 自启
        if autostart_installed():
            self.lb_auto.configure(text="已开启 —— 开机后自动启动「服务 + 内网穿透守护」", fg=GREEN)
            self.btn_auto.configure(text="取消", bg=CARD3, fg=TEXT)
            self.btn_auto.configure(command=self._toggle_autostart)
        else:
            self.lb_auto.configure(text="未开启 —— 每次开机需要手动启动", fg=DIM)
            self.btn_auto.configure(text="安装", bg=ACCENT, fg="#ffffff")

        # 隧道
        pid = tunnel_pid()
        if pid:
            url = tunnel_url() or "（日志里还没读到地址）"
            local = tunnel_local()
            note = ""
            if local and local not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
                note = f"   ⚠ 隧道指向 {local}，脚本会自动转发到 {PORT}"
            self.lb_tunnel.configure(
                text=f"运行中 · PID {pid}{note}", fg=GREEN)
            self.lb_tunnel_url.configure(text=url)
            self.btn_tunnel.configure(text="停止", bg=CARD3, fg=RED,
                                      command=self._toggle_tunnel)
        else:
            self.lb_tunnel.configure(text="未启动 —— 手机在外网将无法访问", fg=DIM)
            self.lb_tunnel_url.configure(text="")
            self.btn_tunnel.configure(text="启动", bg=ACCENT, fg="#ffffff",
                                      command=self._toggle_tunnel)

    # ================================================================ 用户页

    def refresh_users(self) -> None:
        for i in self.tv_users.get_children():
            self.tv_users.delete(i)
        # 课程数 / 总句数 / 已学句数 / 登录设备数 / 最后登录时间，一次查齐
        rows = db_query("""
            SELECT u.id, u.username, u.email, u.display_name, u.role,
                   u.created_at, u.avatar, u.background, u.password_hash,
                   (SELECT COUNT(*) FROM lessons l WHERE l.owner_id = u.id) AS n,
                   (SELECT COALESCE(SUM(l.sentence_count),0) FROM lessons l
                     WHERE l.owner_id = u.id) AS total_sent,
                   (SELECT COUNT(*) FROM progress p
                     WHERE p.user_id = u.id AND p.sentence_index >= 0) AS done_sent,
                   (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id) AS devices,
                   (SELECT MAX(s.created_at) FROM sessions s
                     WHERE s.user_id = u.id) AS last_login
            FROM users u ORDER BY u.id
        """)
        if not rows:
            self.lb_user_sum.configure(text="读不到用户（数据库可能还没建）")
            return
        for r in rows:
            total = r["total_sent"] or 0
            done = r["done_sent"] or 0
            pct = f"{done} ({round(done / total * 100)}%)" if total else "0"
            self.tv_users.insert("", "end", iid=str(r["id"]), values=(
                r["username"],
                r["email"] or "-",
                r["display_name"] or "-",
                "管理员" if r["role"] == "admin" else "普通",
                r["n"],
                total,
                pct,
                r["devices"],
                fmt_time(r["last_login"]),
                fmt_time(r["created_at"]),
            ))
        total = len(rows)
        admins = sum(1 for r in rows if r["role"] == "admin")
        self.lb_user_sum.configure(text=f"共 {total} 个账号，其中管理员 {admins} 个")

    def _selected_user(self) -> tuple[int, str] | None:
        sel = self.tv_users.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个用户")
            return None
        iid = sel[0]
        vals = self.tv_users.item(iid, "values")
        return int(iid), vals[0] if vals else ""

    def _user_add(self) -> None:
        name = simpledialog.askstring("新建用户", "用户名（登录用，不能重复）：", parent=self)
        if not name:
            return
        pw = simpledialog.askstring("新建用户", "密码（至少 6 位）：", parent=self,
                                    show="*")
        if not pw or len(pw) < 6:
            messagebox.showwarning("提示", "密码至少要 6 位")
            return
        body = json.dumps({"username": name.strip(), "password": pw}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/auth/register", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 200:
                    messagebox.showinfo("成功", f"已创建用户 {name}")
                    self.refresh_users()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("创建失败", str(e))

    def _show_user_detail(self) -> None:
        """把选中用户的所有信息摊开，包括密码的存储状态。"""
        sel = self.tv_users.selection()
        if not sel:
            return
        uid = int(sel[0])
        rows = db_query("""
            SELECT u.*,
                   (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id) AS devices,
                   (SELECT MAX(s.created_at) FROM sessions s
                     WHERE s.user_id = u.id) AS last_login,
                   (SELECT COALESCE(SUM(l.sentence_count),0) FROM lessons l
                     WHERE l.owner_id = u.id) AS total_sent,
                   (SELECT COUNT(*) FROM progress p
                     WHERE p.user_id = u.id AND p.sentence_index >= 0) AS done_sent
            FROM users u WHERE u.id = ?
        """, (uid,))
        if not rows:
            return
        u = rows[0]

        pw = u.get("password_hash") or ""
        parts = pw.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            algo, rounds, salt, digest = parts
            pw_info = (f"PBKDF2-SHA256 · {int(rounds):,} 次迭代 · "
                       f"盐 {salt[:8]}… · 哈希 {digest[:16]}…")
        else:
            pw_info = "格式无法识别" if pw else "空"

        bg = u.get("background") or ""
        if bg.startswith("data:image"):
            head = bg.split(",", 1)[0]
            bg_info = f"自定义图片（{head}，约 {len(bg) * 3 // 4 // 1024} KB）"
        elif bg:
            bg_info = f"内置 · {bg}"
        else:
            bg_info = "未设置（用默认）"

        av = u.get("avatar") or ""
        av_info = (f"自定义头像（约 {len(av) * 3 // 4 // 1024} KB）"
                   if av.startswith("data:image") else "默认（首字）")

        total = u.get("total_sent") or 0
        done = u.get("done_sent") or 0
        pct = round(done / total * 100) if total else 0

        text = (
            f"用户名      {u['username']}\n"
            f"邮箱        {u.get('email') or '—'}\n"
            f"昵称        {u.get('display_name') or '—'}\n"
            f"角色        {'管理员（可浏览任意目录、可管理用户）' if u['role'] == 'admin' else '普通用户（只能看自己的素材）'}\n"
            f"注册时间    {fmt_time(u.get('created_at'))}\n"
            f"最后登录    {fmt_time(u.get('last_login'))}\n"
            f"在线设备    {u.get('devices') or 0} 个会话\n"
            f"课程        {total} 句，已学 {done} 句（{pct}%）\n"
            f"头像        {av_info}\n"
            f"背景        {bg_info}\n"
            f"密码        {pw_info}\n"
            f"\n"
            f"说明：密码用的是单向哈希（PBKDF2-SHA256），数学上无法还原成明文，\n"
            f"所以这里只能显示它的存储状态。忘记密码请用「重置密码」生成一个新的。"
        )
        self.lb_user_detail.configure(text=text, fg=TEXT)

    def _user_reset(self) -> None:
        """真正地重置密码：生成/输入新密码 → 写哈希 → 踢掉旧会话。"""
        got = self._selected_user()
        if not got:
            return
        uid, uname = got

        choice = messagebox.askyesnocancel(
            "重置密码",
            f"给「{uname}」设置新密码。\n\n"
            "选「是」自动生成一个随机密码（推荐，会显示给你）\n"
            "选「否」自己输入\n"
            "选「取消」放弃")
        if choice is None:
            return

        if choice:
            import secrets

            pw = secrets.token_urlsafe(9)
        else:
            pw = simpledialog.askstring("重置密码", "输入新密码（至少 6 位）：",
                                        parent=self, show="*")
            if not pw:
                return
            if len(pw) < 6:
                messagebox.showwarning("太短", "密码至少要 6 位")
                return

        hashed = hash_password(pw)

        def work(con):
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (hashed, uid))
            # 改密码后让旧会话全部失效，避免别人还挂着
            con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

        ok, msg = db_write(work)
        if not ok:
            messagebox.showerror("重置失败", msg)
            return

        self.refresh_users()
        messagebox.showinfo(
            "密码已重置",
            f"用户：{uname}\n\n新密码：{pw}\n\n"
            "请立刻记下来——这个密码只显示这一次。\n"
            "该用户所有设备上的登录都已失效，需要用新密码重新登录。")
        try:
            self.clipboard_clear()
            self.clipboard_append(pw)
            self.lb_footer.configure(text=f"新密码已复制到剪贴板：{pw}")
        except Exception:  # noqa: BLE001
            pass

    def _user_kick(self) -> None:
        """把某个用户在所有设备上踢下线（不删账号）。"""
        got = self._selected_user()
        if not got:
            return
        uid, uname = got
        rows = db_query("SELECT COUNT(*) c FROM sessions WHERE user_id = ?", (uid,))
        n = rows[0]["c"] if rows else 0
        if not n:
            messagebox.showinfo("提示", f"{uname} 当前没有登录中的设备")
            return
        if not messagebox.askyesno(
                "踢下线", f"让「{uname}」在 {n} 台设备上退出登录？\n"
                          "账号本身不删，只是需要重新登录。"):
            return

        def work(con):
            con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

        ok, msg = db_write(work)
        if not ok:
            messagebox.showerror("失败", msg)
            return
        self.refresh_users()
        self.lb_footer.configure(text=f"已让 {uname} 在 {n} 台设备上退出")

    def _user_del(self) -> None:
        got = self._selected_user()
        if not got:
            return
        uid, uname = got
        if not messagebox.askyesno("确认删除",
                                   f"确定删除用户「{uname}」吗？\n"
                                   "他/她的课程和学习进度也会一起删除。\n\n"
                                   "这个操作不可撤销。"):
            return
        import sqlite3
        try:
            con = sqlite3.connect(str(DATA / "library.sqlite3"), timeout=10)
            with con:
                con.execute("DELETE FROM lessons WHERE owner_id = ?", (uid,))
                con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
                con.execute("DELETE FROM progress WHERE user_id = ?", (uid,))
                con.execute("DELETE FROM lesson_state WHERE user_id = ?", (uid,))
                con.execute("DELETE FROM settings WHERE user_id = ?", (uid,))
                con.execute("DELETE FROM users WHERE id = ?", (uid,))
            con.close()
            messagebox.showinfo("已删除", f"用户 {uname} 及其数据已删除")
            self.refresh_users()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("删除失败", str(e))

    # ================================================================ 素材页

    def refresh_lib(self) -> None:
        for i in self.tv_lib.get_children():
            self.tv_lib.delete(i)
        rows = db_query("""
            SELECT l.id, l.title, l.sentence_count, l.duration, l.source,
                   l.has_video, l.created_at, COALESCE(u.username,'?') AS owner
            FROM lessons l LEFT JOIN users u ON u.id = l.owner_id
            ORDER BY l.created_at DESC
        """)
        if not rows:
            self.lb_lib_sum.configure(text="还没有课程")
            return
        for r in rows:
            self.tv_lib.insert("", "end", iid=str(r["id"]), values=(
                r["title"] or "(无标题)",
                r["sentence_count"] or 0,
                fmt_dur(r["duration"]),
                r["owner"],
                r["source"] or "-",
                "有" if r["has_video"] else "音频",
                fmt_time(r["created_at"]),
            ))
        media = dir_size(DATA / "media")
        clips = dir_size(DATA / "clips")
        total_sent = sum(r["sentence_count"] or 0 for r in rows)
        self.lb_lib_sum.configure(
            text=f"{len(rows)} 门课 · {total_sent} 句 · "
                 f"媒体 {human(media)} + 片段 {human(clips)}")

    def _lib_del(self) -> None:
        sel = self.tv_lib.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一门课")
            return
        lid = sel[0]
        title = self.tv_lib.item(lid, "values")[0]
        if not messagebox.askyesno("确认删除", f"删除课程「{title}」？\n文件也会一起删掉。"):
            return
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/lessons/{lid}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status in (200, 204):
                    self.refresh_lib()
                    return
        except Exception:  # noqa: BLE001
            pass
        # HTTP 不行就直接删文件（本地工具，允许兜底）
        import shutil as sh
        for d in (DATA / "media" / lid, DATA / "clips" / lid, DATA / "subs" / lid):
            if d.exists():
                sh.rmtree(d, ignore_errors=True)
        import sqlite3
        try:
            con = sqlite3.connect(str(DATA / "library.sqlite3"), timeout=10)
            with con:
                con.execute("DELETE FROM lessons WHERE id = ?", (lid,))
                con.execute("DELETE FROM progress WHERE lesson_id = ?", (lid,))
                con.execute("DELETE FROM lesson_state WHERE lesson_id = ?", (lid,))
            con.close()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("删除失败", str(e))
        self.refresh_lib()

    # ================================================================ 诊断页

    def refresh_diag(self) -> None:
        for w in self.diag_inner.winfo_children():
            w.destroy()

        sections = [
            ("运行环境", diag_runtime()),
            ("依赖包", diag_deps()),
            ("资源文件", diag_assets()),
        ]

        ok_n = bad_n = 0
        for title, rows in sections:
            tk.Label(self.diag_inner, text=title, bg=BG, fg=DIM,
                     font=("Microsoft YaHei UI", 9, "bold"),
                     anchor="w").pack(fill="x", pady=(10, 4))
            card = tk.Frame(self.diag_inner, bg=CARD, padx=14, pady=8,
                            highlightbackground="#20262f", highlightthickness=1)
            card.pack(fill="x")
            for ok, name, desc in rows:
                ok_n += 1 if ok else 0
                bad_n += 0 if ok else 1
                line = tk.Frame(card, bg=CARD)
                line.pack(fill="x", pady=3)
                tk.Label(line, text="✓" if ok else "✗", bg=CARD,
                         fg=GREEN if ok else RED, width=3,
                         font=("Segoe UI", 11, "bold")).pack(side="left")
                tk.Label(line, text=name, bg=CARD, fg=TEXT, width=18,
                         anchor="w", font=("Microsoft YaHei UI", 9)).pack(side="left")
                tk.Label(line, text=desc, bg=CARD, fg=DIM if ok else ORANGE,
                         anchor="w", font=("Microsoft YaHei UI", 9),
                         wraplength=640, justify="left").pack(side="left", fill="x",
                                                              expand=True)

        self.lb_diag_sum.configure(
            text=f"共 {ok_n + bad_n} 项，{ok_n} 正常" +
                 (f"，{bad_n} 项需要注意" if bad_n else "，全部正常"),
            fg=GREEN if not bad_n else ORANGE)

    # ================================================================ 设置动作

    def _toggle_autostart(self) -> None:
        if autostart_installed():
            ok, msg = uninstall_autostart()
        else:
            ok, msg = install_autostart()
        if not ok:
            messagebox.showerror("失败", msg)
        self._refresh_settings()

    def _toggle_tunnel(self) -> None:
        if tunnel_pid():
            subprocess.run(["taskkill", "/IM", "natapp.exe", "/F"],
                           capture_output=True, timeout=15)
            self._refresh_settings()
            return
        script = TOOLS / "natapp_tunnel.py"
        if not script.exists():
            messagebox.showerror("缺失", f"找不到 {script}")
            return
        try:
            subprocess.Popen(
                [python_exe(), str(script)],
                cwd=str(ROOT), env=service_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("启动失败", str(e))

    def _clear_cache(self) -> None:
        cache = DATA / "asr_cache"
        if not cache.exists():
            messagebox.showinfo("提示", "本来就没有缓存")
            return
        size = human(dir_size(cache))
        if not messagebox.askyesno("确认", f"删除识别缓存（{size}）？\n课程不会丢，只是下次导入会重新识别。"):
            return
        try:
            shutil.rmtree(cache, ignore_errors=True)
            cache.mkdir(parents=True, exist_ok=True)
            messagebox.showinfo("完成", "缓存已清空")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("失败", str(e))

    # ================================================================ 服务动作

    def _run_async(self, fn, title: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.lb_footer.configure(text=title + "…")

        def work() -> None:
            try:
                ok, msg = fn()
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            self.after(0, lambda: self._done(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok: bool, msg: str) -> None:
        self._busy = False
        self.lb_footer.configure(text=("✓ " if ok else "✗ ") + msg)
        if not ok:
            messagebox.showerror("操作失败", msg)
        self.refresh()

    def _do_start(self) -> None:
        self._run_async(start_service, "正在启动服务")

    def _do_stop(self) -> None:
        self._run_async(stop_service, "正在停止服务")

    def _do_restart(self) -> None:
        self._run_async(restart_service, "正在重启服务")

    # ================================================================ 工具

    def _copy(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.lb_footer.configure(text=f"已复制：{text}")
        except Exception:  # noqa: BLE001
            pass

    def _open(self, target: str) -> None:
        try:
            os.startfile(target)      # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            webbrowser.open(target)


def _single_instance() -> bool:
    """保证同时只开一个控制台窗口。

    之前测试时不小心启动了多个实例，桌面上堆了好几个一模一样的窗口。
    用 Windows 命名互斥体做判断：如果已经有一个在跑，就直接退出。
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW(None, False, "Local\\ListenUpConsole")
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True      # 判断失败时不阻止启动


def main() -> int:
    if not _single_instance():
        return 0
    app = Console()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
