"""创建 GitHub 仓库并推送。

凭据来自 Windows 凭据管理器（Visual Studio 存的那份），
通过 `git credential fill` 取，**绝不打印 token**。
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

STAGE = Path(r"C:\英语听力训练\开源版")
REPO = "listenup"
DESC = ("把视频/播客切成逐句可精听的听力材料 · "
        "Local-first English listening trainer with per-sentence playback")


def get_token() -> tuple[str, str]:
    r = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    if not out.get("password"):
        raise SystemExit("取不到 GitHub 凭据")
    return out.get("username", ""), out["password"]


def api(path: str, token: str, method: str = "GET",
        body: dict | None = None) -> tuple[int, dict | list | None]:
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "listenup-publish",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"message": raw.decode("utf-8", "replace")[:300]}


def main() -> int:
    user, token = get_token()
    print(f"  账号: {user}")
    print(f"  token: 已获取（长度 {len(token)}，不显示）")
    print()

    # 1) 看仓库是否已存在
    print("[1/3] 检查仓库是否已存在")
    code, data = api(f"/repos/{user}/{REPO}", token)
    if code == 200:
        print(f"  已存在：{data.get('html_url')}")
        exists = True
    elif code == 404:
        print("  不存在，准备创建")
        exists = False
    else:
        print(f"  查询返回 {code}：{(data or {}).get('message')}")
        exists = False

    # 2) 创建
    if not exists:
        print("[2/3] 创建仓库")
        code, data = api("/user/repos", token, "POST", {
            "name": REPO,
            "description": DESC,
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "has_projects": False,
            "auto_init": False,
        })
        if code == 201:
            print(f"  ✓ 已创建：{data.get('html_url')}")
        else:
            msg = (data or {}).get("message", "")
            print(f"  ✗ 创建失败 HTTP {code}：{msg}")
            if "scope" in str(data).lower() or code == 403:
                print()
                print("  这说明这个 token 没有建仓库的权限。")
                print("  解决办法（任选其一）：")
                print(f"    1. 你手动去 https://github.com/new 建一个名为 {REPO} 的空仓库，"
                      f"建好后我再来推")
                print("    2. 你发一个有 repo 权限的 Personal Access Token 给我")
            return 1
    else:
        print("[2/3] 跳过创建")

    print("[3/3] 配置远端")
    remote = f"https://github.com/{user}/{REPO}.git"
    subprocess.run(["git", "remote", "remove", "origin"],
                   cwd=STAGE, capture_output=True)
    r = subprocess.run(["git", "remote", "add", "origin", remote],
                       cwd=STAGE, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(f"  ✗ 配置失败：{r.stderr}")
        return 1
    print(f"  origin → {remote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
