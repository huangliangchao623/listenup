"""触发一次导入并等待完成，然后检查译文。用于回归测试。"""
from __future__ import annotations

import json
import secrets
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "library.sqlite3"
BASE = "http://127.0.0.1:8765"
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1fg411b7wx"


def main() -> int:
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    uid = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with con:
        con.execute("INSERT INTO sessions (token,user_id,created_at,expires_at) "
                    "VALUES (?,?,?,?)", (tok, uid, now, now + 1800))
    con.close()

    def post(path: str, payload: dict) -> dict:
        r = urllib.request.Request(BASE + path,
                                   data=json.dumps(payload).encode(), method="POST")
        r.add_header("Cookie", f"listenup_session={tok}")
        r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")

    def get(path: str) -> dict:
        r = urllib.request.Request(BASE + path)
        r.add_header("Cookie", f"listenup_session={tok}")
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")

    try:
        job = post("/api/build/url", {"url": URL}).get("jobId")
        print(f"  jobId: {job}")
        deadline = time.time() + 420
        while time.time() < deadline:
            time.sleep(6)
            d = get(f"/api/jobs/{job}")
            st = d.get("status")
            print(f"    {st}")
            if st in ("done", "error", "failed", "cancelled"):
                break
        if d.get("status") != "done":
            print("  ✗ 任务未成功:", json.dumps(d, ensure_ascii=False)[:300])
            return 1

        lid = (d.get("result") or {}).get("id")
        print(f"  完成: {lid}  耗时 {d.get('elapsed')}s  "
              f"{(d.get('result') or {}).get('sentences')} 句")

        mp = ROOT / "data" / "media" / lid / "lesson.json"
        data = json.loads(mp.read_text(encoding="utf-8"))
        ss = data.get("sentences", [])
        n = sum(1 for s in ss if (s.get("translation") or "").strip())
        print(f"  译文: {n}/{len(ss)} 句")
        for s in ss[:3]:
            print(f"    EN: {s['text'][:44]}")
            print(f"    ZH: {(s.get('translation') or '(空)')[:44]}")
        print(f"  LESSON_ID={lid}")
        return 0
    finally:
        con = sqlite3.connect(str(DB), timeout=10)
        with con:
            con.execute("DELETE FROM sessions WHERE token=?", (tok,))
        con.close()


if __name__ == "__main__":
    sys.exit(main())
