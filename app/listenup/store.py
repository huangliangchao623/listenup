"""SQLite storage for the lesson library and study progress.

Uses the stdlib sqlite3 module directly - the schema is small enough that an
ORM would only add indirection.  Media and clips live on disk; the database
holds the registry plus per-sentence progress.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    avatar        TEXT,
    background    TEXT,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id            TEXT PRIMARY KEY,
    owner_id      INTEGER,
    title         TEXT NOT NULL,
    duration      REAL NOT NULL DEFAULT 0,
    has_video     INTEGER NOT NULL DEFAULT 0,
    media_name    TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'local',
    platform      TEXT,
    origin        TEXT,
    sentence_source TEXT,
    sentence_count INTEGER NOT NULL DEFAULT 0,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    user_id     INTEGER NOT NULL DEFAULT 0,
    lesson_id   TEXT NOT NULL,
    sentence_index INTEGER NOT NULL,
    plays       INTEGER NOT NULL DEFAULT 0,
    loops       INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    completed   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, lesson_id, sentence_index)
);

CREATE TABLE IF NOT EXISTS lesson_state (
    user_id       INTEGER NOT NULL DEFAULT 0,
    lesson_id     TEXT NOT NULL,
    last_sentence INTEGER NOT NULL DEFAULT 0,
    position      REAL NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (user_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL DEFAULT 0,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""


# Older databases used single-user tables (no user_id / owner_id).  These
# migrations add the columns in place so an existing install keeps its data.
MIGRATIONS = [
    ("lessons", "owner_id", "ALTER TABLE lessons ADD COLUMN owner_id INTEGER"),
    ("progress", "user_id",
     "ALTER TABLE progress ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"),
    ("lesson_state", "user_id",
     "ALTER TABLE lesson_state ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"),
    ("settings", "user_id",
     "ALTER TABLE settings ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"),
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.DatabaseError:
        return False
    return any(r["name"] == column for r in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older single-user schema up to the multi-user one."""
    for table, column, ddl in MIGRATIONS:
        if not _column_exists(conn, table, column):
            try:
                conn.execute(ddl)
            except sqlite3.DatabaseError:
                pass        # table may not exist yet; SCHEMA covers that

    # progress / lesson_state / settings gained a leading user_id in the primary
    # key.  SQLite cannot alter a primary key, so rebuild those tables if the
    # old single-column key is still in place.
    for table, expected in (("progress", ("user_id", "lesson_id",
                                         "sentence_index")),
                            ("lesson_state", ("user_id", "lesson_id")),
                            ("settings", ("user_id", "key"))):
        try:
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.DatabaseError:
            continue
        pk = [r["name"] for r in sorted(info, key=lambda r: r["pk"])
              if r["pk"]]
        if pk and tuple(pk) != expected:
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            conn.executescript(SCHEMA)      # recreate with the new key
            old_cols = [r["name"] for r in info]
            target = [c for c in expected if c in old_cols]
            cols = ", ".join(target)
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({cols}) "
                f"SELECT {cols} FROM {table}_old")
            conn.execute(f"DROP TABLE {table}_old")

    # Indexes must be created after the columns exist, otherwise an upgrade
    # from the single-user schema fails with "no such column: owner_id".
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_lessons_owner ON lessons(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_progress_user ON progress(user_id)",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.DatabaseError:
            pass


def _connect() -> sqlite3.Connection:
    C.apply_env()
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(C.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------

def upsert_lesson(lesson, owner_id: int | None = None) -> None:
    """Insert or replace a lesson row from a pipeline Lesson object."""
    d = lesson.to_dict()
    meta = {k: v for k, v in d["meta"].items() if k != "sentences"}
    with db() as conn:
        conn.execute(
            """
            INSERT INTO lessons (id, owner_id, title, duration, has_video,
                media_name, source, platform, origin, sentence_source,
                sentence_count, meta_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, duration=excluded.duration,
                has_video=excluded.has_video, media_name=excluded.media_name,
                sentence_source=excluded.sentence_source,
                sentence_count=excluded.sentence_count,
                meta_json=excluded.meta_json
            """,
            (
                d["id"], owner_id, d["title"], d["duration"],
                1 if d["hasVideo"] else 0,
                lesson.media_path.name,
                meta.get("source", "local"), meta.get("platform"),
                meta.get("origin") or meta.get("url"),
                meta.get("sentenceSource"), len(d["sentences"]),
                json.dumps(meta, ensure_ascii=False), time.time(),
            ),
        )


def lesson_owner(lesson_id: str) -> int | None:
    with db() as conn:
        row = conn.execute("SELECT owner_id FROM lessons WHERE id = ?",
                           (lesson_id,)).fetchone()
    return row["owner_id"] if row else None


def list_lessons(user_id: int | None = None) -> list[dict]:
    """List lessons owned by `user_id` that still exist on disk.

    A row whose manifest is gone is a leftover (interrupted import, manual
    cleanup, or a test): showing it would open an empty lesson, so those rows
    are pruned here instead.
    """
    with db() as conn:
        where, params = "", ()
        if user_id is not None:
            where, params = "WHERE l.owner_id = ?", (user_id,)
        rows = conn.execute(
            f"""
            SELECT l.*,
                   COALESCE(s.last_sentence, 0) AS last_sentence,
                   COALESCE(s.position, 0)      AS position,
                   (SELECT COUNT(*) FROM progress p
                     WHERE p.lesson_id = l.id AND p.completed = 1
                       AND p.user_id = ?) AS done
            FROM lessons l
            LEFT JOIN lesson_state s
                   ON s.lesson_id = l.id AND s.user_id = ?
            {where}
            ORDER BY l.created_at DESC
            """,
            (user_id or 0, user_id or 0, *params),
        ).fetchall()

        stale = [r["id"] for r in rows
                 if not (C.MEDIA_DIR / r["id"] / "lesson.json").exists()]
        for lid in stale:
            conn.execute("DELETE FROM progress WHERE lesson_id = ?", (lid,))
            conn.execute("DELETE FROM lesson_state WHERE lesson_id = ?", (lid,))
            conn.execute("DELETE FROM lessons WHERE id = ?", (lid,))

    out = []
    for r in rows:
        if r["id"] in stale:
            continue
        count = r["sentence_count"] or 0
        done = r["done"] or 0
        out.append({
            "id": r["id"],
            "title": r["title"],
            "duration": r["duration"],
            "hasVideo": bool(r["has_video"]),
            "source": r["source"],
            "platform": r["platform"],
            "sentenceCount": count,
            "sentencesDone": done,
            "progress": round(done / count, 3) if count else 0.0,
            "lastSentence": r["last_sentence"],
            "createdAt": r["created_at"],
            "media": f"/media/{r['id']}/{r['media_name']}",
        })
    return out


def get_lesson(lesson_id: str, user_id: int | None = None) -> dict | None:
    """Read a lesson manifest from disk and attach this user's progress."""
    manifest = C.MEDIA_DIR / lesson_id / "lesson.json"
    if not manifest.exists():
        return None
    uid = user_id or 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    with db() as conn:
        rows = conn.execute(
            "SELECT sentence_index, plays, loops, completed FROM progress "
            "WHERE lesson_id = ? AND user_id = ?", (lesson_id, uid)).fetchall()
        state = conn.execute(
            "SELECT last_sentence, position FROM lesson_state "
            "WHERE lesson_id = ? AND user_id = ?", (lesson_id, uid)).fetchone()
    prog = {r["sentence_index"]: dict(r) for r in rows}
    for s in data["sentences"]:
        p = prog.get(s["index"], {})
        s["plays"] = p.get("plays", 0)
        s["loops"] = p.get("loops", 0)
        s["completed"] = bool(p.get("completed", 0))
        if s.get("audio"):
            s["audioUrl"] = f"/clips/{lesson_id}/{s['audio']}"
    data["lastSentence"] = state["last_sentence"] if state else 0
    data["position"] = state["position"] if state else 0
    return data


def sync_lesson_from_manifest(lesson_id: str) -> bool:
    """Re-sync the DB row from the on-disk lesson.json.

    The manifest is the source of truth for sentence count and title, but the
    list query reads the DB for speed.  After an operation that rewrites the
    manifest without going through the pipeline (e.g. `resplit`), the two drift
    apart - the library card then advertises a stale sentence count and a
    progress percentage computed against the wrong denominator.
    """
    manifest = C.MEDIA_DIR / lesson_id / "lesson.json"
    if not manifest.exists():
        return False
    data = json.loads(manifest.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}
    with db() as conn:
        conn.execute(
            """
            UPDATE lessons
               SET title = ?, duration = ?, has_video = ?,
                   sentence_count = ?, sentence_source = ?,
                   meta_json = ?
             WHERE id = ?
            """,
            (data.get("title", ""), float(data.get("duration") or 0.0),
             1 if data.get("hasVideo") else 0, len(data.get("sentences") or []),
             meta.get("sentenceSource"),
             json.dumps(meta, ensure_ascii=False), lesson_id),
        )
    return True


def delete_lesson(lesson_id: str, user_id: int | None = None) -> bool:
    """Delete a lesson.  When `user_id` is given the caller must own it."""
    import shutil

    with db() as conn:
        if user_id is None:
            row = conn.execute("SELECT id FROM lessons WHERE id = ?",
                               (lesson_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM lessons WHERE id = ? AND owner_id = ?",
                (lesson_id, user_id)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM progress WHERE lesson_id = ?", (lesson_id,))
        conn.execute("DELETE FROM lesson_state WHERE lesson_id = ?", (lesson_id,))
        conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    for d in (C.MEDIA_DIR / lesson_id, C.CLIP_DIR / lesson_id):
        shutil.rmtree(d, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

def record_play(lesson_id: str, index: int, looped: bool = False,
                user_id: int | None = None) -> None:
    uid = user_id or 0
    with db() as conn:
        conn.execute(
            """
            INSERT INTO progress (user_id, lesson_id, sentence_index, plays,
                                  loops, last_played, completed)
            VALUES (?,?,?,1,?,?,1)
            ON CONFLICT(user_id, lesson_id, sentence_index) DO UPDATE SET
                plays = plays + 1,
                loops = loops + ?,
                last_played = ?,
                completed = 1
            """,
            (uid, lesson_id, index, 1 if looped else 0, time.time(),
             1 if looped else 0, time.time()),
        )


def save_state(lesson_id: str, last_sentence: int, position: float,
               user_id: int | None = None) -> None:
    uid = user_id or 0
    with db() as conn:
        conn.execute(
            """
            INSERT INTO lesson_state (user_id, lesson_id, last_sentence,
                                      position, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                last_sentence=excluded.last_sentence,
                position=excluded.position,
                updated_at=excluded.updated_at
            """,
            (uid, lesson_id, last_sentence, position, time.time()),
        )


def reset_progress(lesson_id: str, user_id: int | None = None) -> None:
    uid = user_id or 0
    with db() as conn:
        conn.execute(
            "DELETE FROM progress WHERE lesson_id = ? AND user_id = ?",
            (lesson_id, uid))
        conn.execute(
            "DELETE FROM lesson_state WHERE lesson_id = ? AND user_id = ?",
            (lesson_id, uid))


def stats(user_id: int | None = None) -> dict:
    uid = user_id or 0
    with db() as conn:
        n_lessons = conn.execute(
            "SELECT COUNT(*) c FROM lessons WHERE owner_id = ?",
            (uid,)).fetchone()["c"]
        total_sentences = conn.execute(
            "SELECT COALESCE(SUM(sentence_count),0) c FROM lessons "
            "WHERE owner_id = ?", (uid,)).fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(*) c FROM progress WHERE completed = 1 AND user_id = ?",
            (uid,)).fetchone()["c"]
        plays = conn.execute(
            "SELECT COALESCE(SUM(plays),0) c FROM progress WHERE user_id = ?",
            (uid,)).fetchone()["c"]
        seconds = conn.execute(
            "SELECT COALESCE(SUM(plays * 2.0),0) c FROM progress WHERE user_id = ?",
            (uid,)).fetchone()["c"]
        days = conn.execute(
            """
            SELECT DISTINCT date(last_played, 'unixepoch', 'localtime') d
            FROM progress WHERE last_played IS NOT NULL AND user_id = ?
            ORDER BY d DESC LIMIT 60
            """, (uid,)
        ).fetchall()
    return {
        "lessons": n_lessons,
        "totalSentences": total_sentences,
        "sentencesDone": done,
        "totalPlays": plays,
        "estimatedMinutes": round(seconds / 60.0, 1),
        "activeDays": [r["d"] for r in days],
    }


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "autoAdvance": True,
    "showTranslation": False,
    "playbackRate": 1.0,
    "loopCurrent": False,
    "ttsVoice": "en-US-AriaNeural",
    "ttsEngine": "offline",
    "theme": "dark",
    "background": "aurora",
}


def get_settings(user_id: int | None = None) -> dict:
    uid = user_id or 0
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?",
                            (uid,)).fetchall()
    out = dict(DEFAULT_SETTINGS)
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            out[r["key"]] = r["value"]
    return out


def save_settings(values: dict, user_id: int | None = None) -> dict:
    uid = user_id or 0
    with db() as conn:
        for k, v in values.items():
            if k not in DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                (uid, k, json.dumps(v, ensure_ascii=False)),
            )
    return get_settings(user_id)
