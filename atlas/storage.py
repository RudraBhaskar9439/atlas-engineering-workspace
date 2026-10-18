"""Non-graph account records, encrypted credentials, and conservative API budget reservations."""

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from .config import API_CAP_INR, DATA, secret_file

BOX = Fernet(secret_file("credentials.key", lambda: Fernet.generate_key().decode()).encode())
SESSION_SECRET = secret_file("session.key", lambda: secrets.token_urlsafe(48))


@contextmanager
def connection():
    db = sqlite3.connect(DATA / "accounts.sqlite", timeout=20)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def setup():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(uid TEXT PRIMARY KEY,email TEXT,name TEXT,picture TEXT);
        CREATE TABLE IF NOT EXISTS credentials(uid TEXT,provider TEXT,secret BLOB,PRIMARY KEY(uid,provider));
        CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,uid TEXT,operation TEXT,inr REAL,
          status TEXT,created_at TEXT,detail TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,uid TEXT,kind TEXT,status TEXT,
          detail TEXT,created_at TEXT);
        """)


def now():
    return datetime.now(UTC).isoformat()


def save_key(uid: str, provider: str, value: str):
    with connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO credentials VALUES(?,?,?)", (uid, provider, BOX.encrypt(value.encode()))
        )


def get_key(uid: str, provider: str):
    with connection() as db:
        row = db.execute(
            "SELECT secret FROM credentials WHERE uid=? AND provider=?", (uid, provider)
        ).fetchone()
    return BOX.decrypt(row[0]).decode() if row else None


def delete_key(uid: str, provider: str):
    with connection() as db:
        db.execute("DELETE FROM credentials WHERE uid=? AND provider=?", (uid, provider))


def budget():
    with connection() as db:
        spent = db.execute("SELECT COALESCE(SUM(inr),0) FROM usage").fetchone()[0]
        rows = db.execute(
            "SELECT id,operation,inr,status,created_at,detail FROM usage ORDER BY id DESC LIMIT 30"
        ).fetchall()
    return {
        "cap_inr": API_CAP_INR,
        "reserved_inr": round(spent, 2),
        "remaining_inr": round(max(0, API_CAP_INR - spent), 2),
        "operations": [dict(r) for r in rows],
    }


def reserve(uid: str, operation: str, amount: float):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        spent = db.execute("SELECT COALESCE(SUM(inr),0) FROM usage").fetchone()[0]
        if spent + amount > API_CAP_INR:
            raise ValueError("API testing budget exhausted. Increase the approved cap before more calls.")
        row = db.execute(
            "INSERT INTO usage(uid,operation,inr,status,created_at) VALUES(?,?,?,?,?)",
            (uid, operation, amount, "reserved", now()),
        )
        return row.lastrowid


def finish_reservation(reservation_id: int, succeeded: bool, detail: str = ""):
    # Keep reservations after failures: a timed-out upstream request may still have been billed.
    with connection() as db:
        db.execute(
            "UPDATE usage SET status=?,detail=? WHERE id=?",
            ("completed" if succeeded else "uncertain", detail[:300], reservation_id),
        )


def job(uid, kind, status="running", detail=None, job_id=None):
    job_id = job_id or secrets.token_hex(12)
    with connection() as db:
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,detail=excluded.detail",
            (job_id, uid, kind, status, json.dumps(detail or {}), now()),
        )
    return job_id


def jobs(uid):
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM jobs WHERE uid=? ORDER BY created_at DESC LIMIT 20", (uid,)
        ).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
