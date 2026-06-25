from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt

DEFAULT_DB_PATH = Path.home() / ".config" / "quotegif" / "web.db"

# Brute-force limits
MAX_USER_FAILURES = 5
USER_LOCKOUT_MINUTES = 15
MAX_IP_FAILURES = 30
IP_WINDOW_MINUTES = 60


def db_path() -> Path:
    raw = os.environ.get("QUOTEGIF_WEB_DB")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                ip_address TEXT NOT NULL,
                success INTEGER NOT NULL,
                attempted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_login_attempts_user_time
                ON login_attempts(username, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
                ON login_attempts(ip_address, attempted_at);

            CREATE TABLE IF NOT EXISTS find_history (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                quote TEXT NOT NULL,
                params_json TEXT NOT NULL,
                status TEXT NOT NULL,
                output_path TEXT,
                output_format TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_find_history_user_time
                ON find_history(user_id, created_at DESC);
            """
        )
        _migrate_find_history(conn)


def _migrate_find_history(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(find_history)")}
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE find_history ADD COLUMN parent_id TEXT")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_user(username: str, password: str) -> None:
    username = username.strip()
    if not username:
        raise ValueError("username is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")

    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), _iso(_now())),
        )
        user_id = int(cur.lastrowid)

    from quotegif.web.paths import user_output_dir_for_id

    user_output_dir_for_id(user_id)


def user_count() -> int:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"]) if row else 0


def get_user_by_username(username: str) -> sqlite3.Row | None:
    init_db()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()


def _count_failures(
    conn: sqlite3.Connection,
    *,
    username: str | None = None,
    ip_address: str | None = None,
    since: datetime,
) -> int:
    if username is not None:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM login_attempts
            WHERE username = ? COLLATE NOCASE
              AND success = 0
              AND attempted_at >= ?
            """,
            (username, _iso(since)),
        ).fetchone()
        return int(row["c"]) if row else 0

    if ip_address is not None:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM login_attempts
            WHERE ip_address = ?
              AND success = 0
              AND attempted_at >= ?
            """,
            (ip_address, _iso(since)),
        ).fetchone()
        return int(row["c"]) if row else 0

    return 0


def check_login_allowed(username: str, ip_address: str) -> tuple[bool, str | None, int]:
    """
    Return (allowed, message, retry_after_seconds).
    """
    init_db()
    now = _now()
    with _connect() as conn:
        user_since = now - timedelta(minutes=USER_LOCKOUT_MINUTES)
        user_failures = _count_failures(conn, username=username, since=user_since)
        if user_failures >= MAX_USER_FAILURES:
            return (
                False,
                f"Too many failed login attempts for this account. "
                f"Try again in {USER_LOCKOUT_MINUTES} minutes.",
                USER_LOCKOUT_MINUTES * 60,
            )

        ip_since = now - timedelta(minutes=IP_WINDOW_MINUTES)
        ip_failures = _count_failures(conn, ip_address=ip_address, since=ip_since)
        if ip_failures >= MAX_IP_FAILURES:
            return (
                False,
                "Too many failed login attempts from this address. Try again later.",
                IP_WINDOW_MINUTES * 60,
            )

    return True, None, 0


def record_login_attempt(
    username: str | None,
    ip_address: str,
    success: bool,
) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO login_attempts (username, ip_address, success, attempted_at)
            VALUES (?, ?, ?, ?)
            """,
            (username, ip_address, 1 if success else 0, _iso(_now())),
        )


def authenticate(username: str, password: str, ip_address: str) -> tuple[bool, str | None, int]:
    allowed, message, retry_after = check_login_allowed(username, ip_address)
    if not allowed:
        return False, message, retry_after

    user = get_user_by_username(username)
    if user is None or not verify_password(password, user["password_hash"]):
        record_login_attempt(username, ip_address, success=False)
        return False, "Invalid username or password", 0

    record_login_attempt(username, ip_address, success=True)
    return True, None, 0


def bootstrap_user_from_env() -> None:
    """Create the first user from QUOTEGIF_WEB_USERNAME / QUOTEGIF_WEB_PASSWORD if empty."""
    if user_count() > 0:
        return
    username = os.environ.get("QUOTEGIF_WEB_USERNAME", "").strip()
    password = os.environ.get("QUOTEGIF_WEB_PASSWORD", "")
    if username and password:
        create_user(username, password)


def get_user_id(username: str) -> int | None:
    user = get_user_by_username(username)
    return int(user["id"]) if user else None


def create_find_history(
    job_id: str,
    user_id: int,
    quote: str,
    params: dict,
    *,
    parent_id: str | None = None,
) -> None:
    now = _iso(_now())
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO find_history (
                id, user_id, quote, params_json, status,
                created_at, updated_at, parent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, user_id, quote, json.dumps(params), "queued", now, now, parent_id),
        )


def create_completed_edit_history(
    history_id: str,
    user_id: int,
    quote: str,
    params: dict,
    output_path: str,
    output_format: str,
    *,
    parent_id: str,
) -> None:
    now = _iso(_now())
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO find_history (
                id, user_id, quote, params_json, status,
                output_path, output_format, error,
                created_at, updated_at, completed_at, parent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history_id,
                user_id,
                quote,
                json.dumps(params),
                "completed",
                output_path,
                output_format,
                None,
                now,
                now,
                now,
                parent_id,
            ),
        )


def update_find_history(
    job_id: str,
    *,
    status: str,
    output_path: str | None = None,
    output_format: str | None = None,
    error: str | None = None,
) -> None:
    now = _iso(_now())
    completed_at = now if status in ("completed", "failed") else None
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE find_history
            SET status = ?,
                output_path = COALESCE(?, output_path),
                output_format = COALESCE(?, output_format),
                error = ?,
                updated_at = ?,
                completed_at = COALESCE(?, completed_at)
            WHERE id = ?
            """,
            (
                status,
                output_path,
                output_format,
                error,
                now,
                completed_at,
                job_id,
            ),
        )


def list_find_history(user_id: int, *, limit: int = 100) -> list[sqlite3.Row]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, quote, params_json, status, output_path, output_format,
                   error, created_at, updated_at, completed_at, parent_id
            FROM find_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return list(rows)


def get_find_history(job_id: str, user_id: int) -> sqlite3.Row | None:
    init_db()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, quote, params_json, status, output_path, output_format,
                   error, created_at, updated_at, completed_at, parent_id
            FROM find_history
            WHERE id = ? AND user_id = ?
            """,
            (job_id, user_id),
        ).fetchone()
