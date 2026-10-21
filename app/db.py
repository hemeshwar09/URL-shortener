import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .ids import encode
from .useragent import parse as parse_ua


def _path() -> str:
    return os.getenv("DB_PATH", "snip.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@contextmanager
def get_conn():
    conn = sqlite3.connect(_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(c) -> None:
    """Lightweight migration so databases created by older versions keep working."""
    def cols(table):
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}

    if "delete_token_hash" not in cols("urls"):
        c.execute("ALTER TABLE urls ADD COLUMN delete_token_hash TEXT")
    for col in ("referrer", "browser", "device"):
        if col not in cols("clicks"):
            c.execute(f"ALTER TABLE clicks ADD COLUMN {col} TEXT")


def init_db() -> None:
    with get_conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                long_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                delete_token_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                ts TEXT NOT NULL,
                user_agent TEXT,
                referrer TEXT,
                browser TEXT,
                device TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_clicks_code_ts ON clicks(code, ts);
            """
        )
        _ensure_columns(c)


class AliasTaken(Exception):
    pass


def create_url(long_url: str, alias: str | None, ttl_seconds: int | None) -> dict:
    expires = (_now() + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds else None
    token = secrets.token_urlsafe(16)  # shown once; only its hash is stored
    with get_conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO urls (code, long_url, created_at, expires_at, delete_token_hash)"
                " VALUES (?,?,?,?,?)",
                (alias, long_url, _now().isoformat(), expires, _hash(token)),
            )
        except sqlite3.IntegrityError:
            raise AliasTaken(alias)
        code = alias or encode(cur.lastrowid)
        if not alias:
            c.execute("UPDATE urls SET code=? WHERE id=?", (code, cur.lastrowid))
    return {"code": code, "long_url": long_url, "expires_at": expires, "delete_token": token}


def get_url(code: str) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM urls WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def is_expired(row: dict) -> bool:
    return bool(row["expires_at"]) and datetime.fromisoformat(row["expires_at"]) < _now()


def delete_url(code: str, token: str) -> str:
    """Returns 'ok', 'notfound' or 'forbidden'."""
    row = get_url(code)
    if not row:
        return "notfound"
    stored = row.get("delete_token_hash")
    if not stored or not hmac.compare_digest(stored, _hash(token)):
        return "forbidden"
    with get_conn() as c:
        c.execute("DELETE FROM clicks WHERE code=?", (code,))
        c.execute("DELETE FROM urls WHERE code=?", (code,))
    return "ok"


def record_click(code: str, user_agent: str | None, referrer: str | None = None) -> None:
    browser, device = parse_ua(user_agent)
    ref = (urlparse(referrer).netloc if referrer else "") or "Direct"
    with get_conn() as c:
        c.execute(
            "INSERT INTO clicks (code, ts, user_agent, referrer, browser, device) VALUES (?,?,?,?,?,?)",
            (code, _now().isoformat(), user_agent, ref, browser, device),
        )


def _top(c, code: str, col: str) -> list[dict]:  # col is always an internal literal
    rows = c.execute(
        f"SELECT COALESCE({col},'Unknown') AS name, COUNT(*) AS n FROM clicks "
        "WHERE code=? GROUP BY name ORDER BY n DESC LIMIT 5",
        (code,),
    ).fetchall()
    return [{"name": r["name"], "count": r["n"]} for r in rows]


def get_stats(code: str) -> dict | None:
    row = get_url(code)
    if not row:
        return None
    today = _now().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    since24 = (_now() - timedelta(hours=24)).isoformat()
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM clicks WHERE code=?", (code,)).fetchone()[0]
        recent = c.execute(
            "SELECT COUNT(*) FROM clicks WHERE code=? AND ts>=?", (code, since24)
        ).fetchone()[0]
        per_day = {
            r["d"]: r["n"]
            for r in c.execute(
                "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM clicks "
                "WHERE code=? AND ts>=? GROUP BY d",
                (code, days[0]),
            )
        }
        browsers, devices, referrers = (_top(c, code, k) for k in ("browser", "device", "referrer"))
    return {
        "code": code,
        "long_url": row["long_url"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "total_clicks": total,
        "clicks_last_24h": recent,
        "by_day": [{"date": d, "clicks": per_day.get(d, 0)} for d in days],
        "browsers": browsers,
        "devices": devices,
        "referrers": referrers,
    }
