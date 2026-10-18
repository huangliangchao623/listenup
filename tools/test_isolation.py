"""验证多用户数据隔离：课程归属、越权访问、设置互不影响。

用手工构造的课程清单，而不是真的导入视频——这样可以精确控制归属，
并且几秒内跑完。
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "pydeps"))
sys.path.insert(0, str(ROOT / "app"))

from listenup import config as C, store  # noqa: E402

BASE = "http://127.0.0.1:8765"
LID = "TEST_ISOLATION_01"
USER_A = ("测试用户", "test123456")
USER_B = ("学生乙", "abc123456")


def make_lesson() -> int:
    """给用户 A 造一门课，返回 A 的 id。"""
    C.apply_env()
    store.init()

    with store.db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?",
                           (USER_A[0],)).fetchone()
    if not row:
        print(f"  找不到用户 {USER_A[0]}，先跑一次登录测试")
        sys.exit(1)
    aid = row["id"]

    work = C.MEDIA_DIR / LID
    (C.CLIP_DIR / LID).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": LID,
        "title": "A 的私有课程",
        "duration": 10.0,
        "hasVideo": False,
        "media": f"/media/{LID}/x.mp3",
        "sentences": [{
            "index": 1, "start": 0.0, "end": 2.0, "duration": 2.0,
            "text": "This belongs to A only.", "words": [],
            "audio": None, "source": "asr", "quality": 1.0,
        }],
        "meta": {"sentenceSource": "asr"},
    }
    (work / "lesson.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with store.db() as conn:
        conn.execute("DELETE FROM lessons WHERE id = ?", (LID,))
        conn.execute(
            """INSERT INTO lessons (id, owner_id, title, duration, has_video,
                   media_name, source, sentence_source, sentence_count,
                   meta_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (LID, aid, "A 的私有课程", 10.0, 0, "x.mp3", "local", "asr", 1,
             "{}", time.time()))
    return aid


def opener(user: tuple[str, str]):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": user[0], "password": user[1]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        op.open(req, timeout=20)
    except urllib.error.HTTPError as e:
        print(f"  {user[0]} 登录失败 HTTP {e.code}")
        sys.exit(1)
    return op


def get_json(op, path: str):
    return json.loads(op.open(BASE + path, timeout=20).read())


def try_call(op, path: str, method: str = "GET"):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        r = op.open(req, timeout=20)
        return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None


def main() -> int:
    aid = make_lesson()
    print(f"  已创建课程 {LID}，归属用户 A (id={aid})")

    opA = opener(USER_A)
    opB = opener(USER_B)

    la = get_json(opA, "/api/lessons")["lessons"]
    lb = get_json(opB, "/api/lessons")["lessons"]
    a_has = any(x["id"] == LID for x in la)
    b_has = any(x["id"] == LID for x in lb)

    print()
    print("=== 课程列表隔离 ===")
    print(f"  A 看到 {len(la)} 个课程，含测试课程 = {a_has}")
    print(f"  B 看到 {len(lb)} 个课程，含测试课程 = {b_has}")

    print()
    print("=== 越权访问 ===")
    for name, op in (("A", opA), ("B", opB)):
        code, _ = try_call(op, f"/api/lessons/{LID}")
        print(f"  {name} 打开该课程: HTTP {code}")

    print()
    print("=== 越权删除（B 不应能删）===")
    code, _ = try_call(opB, f"/api/lessons/{LID}", "DELETE")
    print(f"  B 删除: HTTP {code}")
    still = get_json(opA, "/api/lessons")["lessons"]
    print(f"  A 的课程还在: {any(x['id'] == LID for x in still)}")

    print()
    print("=== 设置隔离 ===")
    req = urllib.request.Request(
        BASE + "/api/settings",
        data=json.dumps({"playbackRate": 1.75}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opA.open(req, timeout=20)
    ra = get_json(opA, "/api/settings")["playbackRate"]
    rb = get_json(opB, "/api/settings")["playbackRate"]
    print(f"  A 改成 1.75 后: A={ra}  B={rb}")

    ok = (a_has and not b_has and
          try_call(opA, f"/api/lessons/{LID}")[0] == 200 and
          try_call(opB, f"/api/lessons/{LID}")[0] == 404 and
          try_call(opB, f"/api/lessons/{LID}", "DELETE")[0] == 404 and
          ra == 1.75 and rb != 1.75)

    print()
    print("=" * 50)
    print("  隔离验证", "全部通过 ✓" if ok else "有失败项 ✗")
    print("=" * 50)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
