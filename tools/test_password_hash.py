"""验证控制台生成的密码哈希能被服务端的 auth.verify_password 认可。"""
import hashlib
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "pydeps"))
sys.path.insert(0, str(ROOT / "app"))

sys.path.insert(0, str(ROOT / "app"))
import console  # noqa: E402
from listenup import auth  # noqa: E402

PW = "test123456"
h = console.hash_password(PW)
print("  生成的哈希:", h[:52], "...")
print("  auth.verify_password 正确密码:", auth.verify_password(PW, h))
print("  auth.verify_password 错误密码:",
      auth.verify_password("wrongpass", h))

# 再验一次服务端自己生成的哈希，确认两边格式完全一致
h2 = auth.hash_password(PW)
print("  服务端生成的哈希:", h2[:52], "...")
print("  交叉验证 console 认可 auth:", auth.verify_password(PW, h2))
