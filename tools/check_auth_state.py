"""检查账号与会话状态（只读）。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
db = ROOT / "data" / "library.sqlite3"

con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("=== users ===")
for r in con.execute(
        "SELECT id, username, display_name, role, password_hash "
        "FROM users ORDER BY id"):
    d = dict(r)
    print(f"  id={d['id']}  {d['username']:12} {str(d['display_name'] or ''):12} "
          f"{d['role']:6} hash={d['password_hash'][:22]}... "
          f"len={len(d['password_hash'])}")

print()
print("=== sessions 按用户 ===")
for r in con.execute(
        "SELECT user_id, COUNT(*) c FROM sessions GROUP BY user_id"):
    print(f"  user={r['user_id']}  {r['c']} 个")

print()
print("=== sessions 总数 / 最早最晚 ===")
row = con.execute(
    "SELECT COUNT(*) c, MIN(created_at) a, MAX(created_at) b FROM sessions"
).fetchone()
import datetime
print(f"  {row['c']} 条")
for k, v in (("最早", row["a"]), ("最晚", row["b"])):
    if v:
        print(f"  {k}: {datetime.datetime.fromtimestamp(v):%Y-%m-%d %H:%M:%S}")

con.close()
