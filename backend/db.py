"""SQLite storage layer. Single file DB, no server, no config."""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("CRA_DB", os.path.join(os.path.dirname(__file__), "..", "data", "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS onboarding (id INTEGER PRIMARY KEY CHECK (id = 1), step TEXT, answers TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, status TEXT DEFAULT 'active', goal TEXT, steps TEXT, tone TEXT,
    facts TEXT, autonomy TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER, external_ref TEXT UNIQUE, subject TEXT,
    contact_email TEXT, contact_name TEXT, do_not_contact INTEGER DEFAULT 0, created_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER, direction TEXT, body TEXT,
    classification TEXT, class_reason TEXT, confidence REAL, quote TEXT,
    external_id TEXT UNIQUE, created_at REAL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER, message_id INTEGER, kind TEXT, status TEXT,
    body TEXT, reason TEXT, created_at REAL
);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO onboarding (id, step, answers, done) VALUES (1, 'business', '{}', 0)")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('kill_switch', 'off')")
        for stmt in (
            "ALTER TABLE threads ADD COLUMN gmail_thread_id TEXT",
            "ALTER TABLE actions ADD COLUMN send_state TEXT",
            "ALTER TABLE actions ADD COLUMN gmail_message_id TEXT",
            "ALTER TABLE onboarding ADD COLUMN campaign_id INTEGER",
            "ALTER TABLE messages ADD COLUMN event_state TEXT DEFAULT 'done'",
            "ALTER TABLE messages ADD COLUMN lease_at REAL",
            "ALTER TABLE messages ADD COLUMN source TEXT DEFAULT 'simulator'",
            "ALTER TABLE messages ADD COLUMN result TEXT",
            "ALTER TABLE actions ADD COLUMN outbound_id TEXT",
            "ALTER TABLE actions ADD COLUMN attempt_at REAL",
            "ALTER TABLE actions ADD COLUMN decision_at REAL",
            "ALTER TABLE actions ADD COLUMN reviewed_body TEXT",
            "ALTER TABLE actions ADD COLUMN campaign_snapshot TEXT",
            "ALTER TABLE threads ADD COLUMN reply_to_id TEXT",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("CREATE TABLE IF NOT EXISTS suppressions (email TEXT PRIMARY KEY, created_at REAL)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS action_per_message ON actions(message_id) WHERE message_id IS NOT NULL")
        conn.execute("INSERT OR IGNORE INTO suppressions SELECT lower(contact_email),created_at FROM threads WHERE do_not_contact=1")
        if not conn.execute("SELECT 1 FROM settings WHERE key='repair_v2_migrated'").fetchone():
            conn.execute("UPDATE messages SET event_state='pending',source='legacy_recovery',lease_at=NULL WHERE direction='in' AND classification='processing'")
            conn.execute("UPDATE actions SET status='uncertain',send_state='uncertain' WHERE status='pending' AND send_state='failed'")
            conn.execute("INSERT INTO settings VALUES ('repair_v2_migrated','1')")
        # Old free-text permissions were unsafe. Re-confirm them in Settings.
        for row in conn.execute("SELECT id, autonomy FROM campaigns").fetchall():
            rules = jload(row["autonomy"], {}) or {}
            if rules.get("version") != 2 and rules.get("auto_price"):
                rules["auto_price"] = False
                rules["migration_note"] = "Automatic price replies need explicit confirmation in Settings."
                conn.execute("UPDATE campaigns SET autonomy=? WHERE id=?", (jdump(rules), row["id"]))
    conn.close()


def q(sql, args=(), one=False):
    conn = connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    return rows[0] if one and rows else (None if one else rows)


def run(sql, args=()):
    conn = connect()
    with conn:
        cur = conn.execute(sql, args)
        last = cur.lastrowid
    conn.close()
    return last


def run_rowcount(sql, args=()):
    conn = connect()
    with conn:
        cur = conn.execute(sql, args)
        n = cur.rowcount
    conn.close()
    return n


def get_setting(key, default=None):
    row = q("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return row["value"] if row else default


def set_setting(key, value):
    run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def jdump(v):
    return json.dumps(v, ensure_ascii=False)


def jload(s, default=None):
    try:
        return json.loads(s)
    except Exception:
        return default

from contextlib import contextmanager


@contextmanager
def transaction():
    """Serialize claims and decision gates across API and worker processes."""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
