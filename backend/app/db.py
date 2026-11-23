from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS business_kinds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL UNIQUE,
  seq INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind_ids TEXT NOT NULL,
  open INTEGER NOT NULL DEFAULT 1,
  current_ticket_id INTEGER
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slip_no TEXT NOT NULL UNIQUE,
  kind_id INTEGER NOT NULL,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL,
  window_id INTEGER,
  queue_pos INTEGER NOT NULL,
  taken_at TEXT NOT NULL,
  called_at TEXT,
  finished_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(kind_id) REFERENCES business_kinds(id),
  FOREIGN KEY(window_id) REFERENCES windows(id)
);
CREATE TABLE IF NOT EXISTS call_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER,
  window_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(ticket_id) REFERENCES tickets(id),
  FOREIGN KEY(window_id) REFERENCES windows(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS day_seals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL UNIQUE,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
'''


def connect() -> sqlite3.Connection:
    # isolation_level=None：事务完全由 db() 显式开启/提交/回滚
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db(write: bool = False):
    """请求级事务。write=True 时 BEGIN IMMEDIATE：进事务即持写锁，
    收档/叫号等「先查后写」链路在锁内完成判定与落库，并发只串行不半截。"""
    conn = connect()
    begun = False
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        begun = True
        yield conn
        conn.execute("COMMIT")
    except Exception:
        if begun:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
        raise
    finally:
        conn.close()


def _migrate_call_events(conn: sqlite3.Connection) -> None:
    """旧库 call_events.ticket_id 为 NOT NULL，收档/开档事件无号牌可挂，
    需重建为可空；新库 SCHEMA 已可空则跳过。"""
    cols = conn.execute("PRAGMA table_info(call_events)").fetchall()
    tk = next((c for c in cols if c["name"] == "ticket_id"), None)
    if not tk or not tk["notnull"]:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE call_events RENAME TO call_events_old")
        conn.execute(
            """CREATE TABLE call_events (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ticket_id INTEGER,
                 window_id INTEGER NOT NULL,
                 action TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 note TEXT NOT NULL DEFAULT '',
                 FOREIGN KEY(ticket_id) REFERENCES tickets(id),
                 FOREIGN KEY(window_id) REFERENCES windows(id)
               )"""
        )
        conn.execute(
            """INSERT INTO call_events(id, ticket_id, window_id, action, created_at, note)
               SELECT id, ticket_id, window_id, action, created_at, note FROM call_events_old"""
        )
        conn.execute("DROP TABLE call_events_old")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate_call_events(conn)
    finally:
        conn.close()
