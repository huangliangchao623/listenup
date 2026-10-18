"""把 Cloudflare 临时隧道换成「固定域名」的命名隧道。

## 为什么需要这个

免费试用隧道（`*.trycloudflare.com`）有三个硬伤：
  · 地址随机，每次重启都变
  · 官方明确「无可用性保证」
  · 出口在美国节点，国内访问延迟 1.5–2.5 秒

命名隧道（named tunnel）绑定你自己的域名后：
  · 地址永远固定，比如 `listen.你的域名.com`
  · 可以指定更近的边缘节点
  · 可以装成 Windows 服务，开机自启

## 前提（必须你先做好）

1. 有一个域名，并且**已经托管到 Cloudflare**
   （在 Cloudflare 后台「添加站点」，然后把域名商的 NS 改成 Cloudflare 给的两个）
2. 跑过 `cloudflared tunnel login`，浏览器里点授权
   → 会在 `%USERPROFILE%\\.cloudflared\\cert.pem` 生成证书

第 2 步必须你本人点，脚本没法替你登录。

## 用法

    python tools/cf_named_tunnel.py listen.你的域名.com

脚本会自动：建隧道 → 绑定 DNS → 写 config.yml → 打印启动命令
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CF = ROOT / "tools" / "bin" / "cloudflared.exe"
CF_DIR = Path(os.path.expanduser("~")) / ".cloudflared"
CERT = CF_DIR / "cert.pem"
TUNNEL_NAME = "listenup"
LOCAL_PORT = 8765


def run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CF), *args],
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
    )


def check_prereq() -> bool:
    ok = True
    print("[1/4] 检查前提")
    if not CF.exists():
        print(f"  ✗ 找不到 cloudflared：{CF}")
        return False
    print(f"  ✓ cloudflared 存在")

    if not CERT.exists():
        print(f"  ✗ 还没有登录证书：{CERT}")
        print()
        print("  请先运行下面这条命令，然后在弹出的浏览器里选择你的域名并授权：")
        print()
        print(f'      "{CF}" tunnel login')
        print()
        print("  授权成功后再回来跑这个脚本。")
        ok = False
    else:
        print(f"  ✓ 已登录（cert.pem 存在）")
    return ok


def create_tunnel() -> str | None:
    print("\n[2/4] 创建/复用命名隧道")
    r = run(["tunnel", "list", "--output", "json"])
    tunnels: list[dict] = []
    if r.returncode == 0 and r.stdout.strip():
        try:
            tunnels = json.loads(r.stdout)
        except json.JSONDecodeError:
            tunnels = []

    existing = next((t for t in tunnels
                     if t.get("name") == TUNNEL_NAME), None)
    if existing:
        tid = existing["id"]
        print(f"  ✓ 已存在隧道 {TUNNEL_NAME} ({tid[:8]}…)，直接复用")
        return tid

    r = run(["tunnel", "create", TUNNEL_NAME])
    if r.returncode != 0:
        print(f"  ✗ 创建失败：{(r.stderr or r.stdout).strip()[:300]}")
        return None
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                  r"[0-9a-f]{4}-[0-9a-f]{12})", r.stdout + r.stderr)
    if not m:
        print(f"  ✗ 没能从输出里解析出隧道 ID：\n{r.stdout}")
        return None
    tid = m.group(1)
    print(f"  ✓ 已创建隧道 {TUNNEL_NAME} ({tid[:8]}…)")
    return tid


def route_dns(hostname: str, tid: str) -> bool:
    print(f"\n[3/4] 把 {hostname} 指向这条隧道")
    r = run(["tunnel", "route", "dns", "--overwrite-dns", tid, hostname])
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if "already exists" in out.lower():
            print(f"  ✓ DNS 记录已存在，跳过")
            return True
        print(f"  ✗ 绑定失败：{out[:300]}")
        return False
    print(f"  ✓ 已创建 CNAME：{hostname} → {tid[:8]}.cfargotunnel.com")
    return True


def write_config(hostname: str, tid: str) -> Path:
    print("\n[4/4] 写配置文件")
    CF_DIR.mkdir(parents=True, exist_ok=True)
    cfg = CF_DIR / "config.yml"
    content = f"""# 英语听力训练 · Cloudflare 命名隧道配置
# 由 tools/cf_named_tunnel.py 生成
tunnel: {tid}
credentials-file: {CF_DIR / 'credentials' / (tid + '.json')}

# 关掉自动更新，避免服务重启时被换版本
no-autoupdate: true

# 入口规则：域名 → 本机服务
ingress:
  - hostname: {hostname}
    service: http://127.0.0.1:{LOCAL_PORT}
    originRequest:
      # 语音入库/切音频可能耗时较久，给足超时
      connectTimeout: 30s
      noTLSVerify: false
  - service: http_status:404
"""
    cfg.write_text(content, encoding="utf-8")
    print(f"  ✓ 已写入 {cfg}")
    return cfg


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/cf_named_tunnel.py listen.你的域名.com")
        print()
        print("域名必须已经托管到 Cloudflare（NS 已改过去）。")
        return 2

    hostname = sys.argv[1].strip().lower()
    if "." not in hostname or "/" in hostname:
        print(f"域名格式看起来不对：{hostname}")
        return 2

    print("=" * 60)
    print("  配置固定域名（Cloudflare 命名隧道）")
    print("=" * 60)
    print(f"  目标域名：{hostname}")
    print(f"  本地服务：http://127.0.0.1:{LOCAL_PORT}")
    print()

    if not check_prereq():
        return 1

    tid = create_tunnel()
    if not tid:
        return 1

    if not route_dns(hostname, tid):
        return 1

    cfg = write_config(hostname, tid)

    print()
    print("=" * 60)
    print("  配置完成")
    print("=" * 60)
    print()
    print("  以后用这条命令启动隧道：")
    print(f'      "{CF}" tunnel --config "{cfg}" run {TUNNEL_NAME}')
    print()
    print(f"  或者装成开机自启的 Windows 服务（需要管理员权限）：")
    print(f'      "{CF}" service install')
    print(f'      "{CF}" service start')
    print()
    print(f"  装好之后，手机在任何网络下访问：")
    print(f"      https://{hostname}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
