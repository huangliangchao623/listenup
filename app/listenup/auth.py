"""账户与会话。

设计取舍
--------
* **密码哈希用标准库的 PBKDF2-HMAC-SHA256**，不引入 bcrypt/argon2。
  理由：这两个都要编译或额外二进制依赖，而本机环境安装受限；
  PBKDF2 是 NIST 推荐方案，60 万次迭代足以抵御离线爆破，
  并且不需要任何第三方包。
* **会话用数据库里的随机 token + HttpOnly Cookie**，不用 JWT。
  理由：JWT 无法主动失效（用户改密码/登出后旧 token 仍然有效），
  而本地服务用户很少，查一次数据库的代价可以忽略，
  换来的是「能立刻踢下线」这个实打实的安全能力。
* **每个用户的数据完全隔离**：课程、进度、设置都按 user_id 归属。
  数据库层用 owner_id 约束，而不是只靠应用层判断。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

from . import config as C, store

# --- 参数 -----------------------------------------------------------------
PBKDF2_ROUNDS = 600_000          # OWASP 2023 对 SHA-256 的建议值
SALT_BYTES = 16
SESSION_DAYS = 30
SESSION_COOKIE = "listenup_session"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5-]{2,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 6


class AuthError(ValueError):
    """用户可见的认证错误（消息直接展示给用户）。"""


# --- 密码 -----------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return `pbkdf2_sha256$rounds$salt_hex$hash_hex`."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    # 恒定时间比较，避免时序侧信道
    return hmac.compare_digest(dk.hex(), hash_hex)


# --- 校验 -----------------------------------------------------------------

def validate_username(name: str) -> str:
    name = (name or "").strip()
    if not USERNAME_RE.match(name):
        raise AuthError("用户名需 2-24 位，可用中英文、数字、下划线、短横线")
    return name


def validate_password(pw: str) -> str:
    if not pw or len(pw) < MIN_PASSWORD:
        raise AuthError(f"密码至少 {MIN_PASSWORD} 位")
    if len(pw) > 200:
        raise AuthError("密码太长了")
    return pw


def validate_email(email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise AuthError("邮箱格式不正确")
    return email


# --- 用户 -----------------------------------------------------------------

def register(username: str, password: str, email: str | None = None,
             display_name: str | None = None) -> dict:
    username = validate_username(username)
    password = validate_password(password)
    email = validate_email(email)

    with store.db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?",
                        (username,)).fetchone():
            raise AuthError("这个用户名已经被注册了")
        if email and conn.execute("SELECT 1 FROM users WHERE email = ?",
                                  (email,)).fetchone():
            raise AuthError("这个邮箱已经注册过了")
        count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        # 第一个注册的人是管理员
        role = "admin" if count == 0 else "user"
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, display_name, "
            "role, created_at) VALUES (?,?,?,?,?,?)",
            (username, email, hash_password(password),
             (display_name or username).strip()[:32], role, time.time()),
        )
        uid = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row)


def authenticate(username: str, password: str) -> dict:
    with store.db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ? OR email = ?",
                           (username, username)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        # 统一提示，不区分「用户不存在」和「密码错误」，避免用户名枚举
        raise AuthError("用户名或密码不正确")
    return dict(row)


def get_user(user_id: int) -> dict | None:
    with store.db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?",
                           (user_id,)).fetchone()
    return dict(row) if row else None


def public_view(user: dict) -> dict:
    """去掉敏感字段，只留前端需要的。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "displayName": user.get("display_name") or user["username"],
        "email": user.get("email"),
        "role": user.get("role") or "user",
        "avatar": user.get("avatar"),
        "background": user.get("background"),
        "createdAt": user.get("created_at"),
    }


def update_profile(user_id: int, display_name: str | None = None,
                   background: str | None = None,
                   avatar: str | None = None) -> dict:
    fields, values = [], []
    if display_name is not None:
        fields.append("display_name = ?")
        values.append(display_name.strip()[:32] or "用户")
    if background is not None:
        # 只接受内置背景 id 或 data: 图片，避免 XSS/SSRF
        if background and not (background.startswith("data:image/")
                               or re.fullmatch(r"[a-z0-9_-]{1,40}", background)):
            raise AuthError("背景格式不正确")
        if len(background or "") > 3_000_000:
            raise AuthError("背景图片太大了（上限约 2 MB）")
        fields.append("background = ?")
        values.append(background or None)
    if avatar is not None:
        if avatar and len(avatar) > 400_000:
            raise AuthError("头像太大了")
        fields.append("avatar = ?")
        values.append(avatar or None)

    if not fields:
        return get_user(user_id) or {}
    values.append(user_id)
    with store.db() as conn:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?",
                     values)
    return get_user(user_id) or {}


def change_password(user_id: int, old: str, new: str) -> None:
    user = get_user(user_id)
    if not user or not verify_password(old, user["password_hash"]):
        raise AuthError("原密码不正确")
    validate_password(new)
    with store.db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (hash_password(new), user_id))
        # 改密码后踢掉其他所有会话
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# --- 会话 -----------------------------------------------------------------

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with store.db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) "
            "VALUES (?,?,?,?)",
            (token, user_id, now, now + SESSION_DAYS * 86400),
        )
        # 顺手清理过期会话，避免表无限增长
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with store.db() as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires_at > ?",
            (token, time.time()),
        ).fetchone()
    if not row:
        return None
    return get_user(row["user_id"])


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with store.db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def destroy_all_sessions(user_id: int) -> None:
    with store.db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def count_users() -> int:
    with store.db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
