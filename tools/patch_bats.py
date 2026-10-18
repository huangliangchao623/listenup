"""把所有 .bat 里的 `python` 调用改成优先使用随程序附带的解释器。

背景：这台机器的系统 `python` 是 Windows 商店版的应用执行别名，
商店包一旦损坏就报 "Python was not found"，所有 .bat 全部失效。
把解释器路径收敛到 tools\\python 后，软件不再依赖系统环境。

做法：在每个 .bat 的 `set "APP=..."` 之后插入两行——
    set "PY=%APP%tools\python\python.exe"
    if not exist "%PY%" set "PY=python"
然后把命令里的 `python "` 换成 `"%PY%" "`。
找不到自带解释器时自动退回系统 python，保持兼容。

注意：.bat 必须是 GBK 编码 + CRLF 换行，否则 cmd 会乱码或解析失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GBK = "gbk"

SET_PY = (
    'set "PY=%APP%tools\\python\\python.exe"\r\n'
    'if not exist "%PY%" set "PY=python"\r\n'
)


def patch(path: Path) -> tuple[bool, str]:
    raw = path.read_bytes()
    try:
        text = raw.decode(GBK)
    except UnicodeDecodeError:
        return False, "不是 GBK，跳过"

    if 'set "PY=' in text:
        return False, "已经改过"
    if 'set "APP=' not in text:
        return False, "没有 APP 变量，跳过"

    lines = text.split("\r\n")
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith('set "APP='):
            out.append('set "PY=%APP%tools\\python\\python.exe"')
            out.append('if not exist "%PY%" set "PY=python"')
            inserted = True

    text2 = "\r\n".join(out)

    # python "xxx"  ->  "%PY%" "xxx"
    changed = text2.count('python "')
    text2 = text2.replace('python "%APP%', '"%PY%" "%APP%')
    # 处理 python 后跟其他引号形式
    text2 = text2.replace(' python "', ' "%PY%" "')

    if text2 == text:
        return False, "没有可替换的调用"

    path.write_bytes(text2.encode(GBK))
    return True, f"替换 {changed} 处"


def main() -> int:
    bats = sorted(ROOT.glob("*.bat"))
    if not bats:
        print("没找到 .bat")
        return 1
    for b in bats:
        ok, msg = patch(b)
        mark = "✓" if ok else "·"
        print(f"  {mark} {b.name:24} {msg}")
    print()
    print("校验：")
    for b in bats:
        data = b.read_bytes()
        text = data.decode(GBK, errors="replace")
        bad_lf = text.count("\n") - text.count("\r\n")
        has_py = 'set "PY=' in text
        flag = "OK" if bad_lf == 0 and has_py else ("缺PY" if not has_py else "换行错误")
        print(f"  {b.name:24} {flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
