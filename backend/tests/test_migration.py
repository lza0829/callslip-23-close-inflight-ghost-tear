"""旧库迁移：call_events.ticket_id 由 NOT NULL 重建为可空，收档事件方可落库。"""
import sqlite3

import app.db as db_mod

OLD_SCHEMA = """
CREATE TABLE business_kinds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL UNIQUE,
  seq INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind_ids TEXT NOT NULL,
  open INTEGER NOT NULL DEFAULT 1,
  current_ticket_id INTEGER
);
CREATE TABLE tickets (
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
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE call_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  window_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""


def test_old_db_migrates_to_nullable_ticket_id(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO business_kinds(name, prefix) VALUES ('个人储蓄', '储')")
    conn.execute("INSERT INTO windows(name, kind_ids) VALUES ('一号窗', '1')")
    conn.execute(
        "INSERT INTO tickets(slip_no, kind_id, business_day, status, queue_pos, taken_at)"
        " VALUES ('储001', 1, '2026-11-23', '等候', 1, '2026-11-23T09:00:00')"
    )
    conn.execute(
        "INSERT INTO call_events(ticket_id, window_id, action, created_at)"
        " VALUES (1, 1, '叫号', '2026-11-23T09:01:00')"
    )
    conn.commit()
    conn.close()

    db_mod.init_db()  # 触发迁移

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cols = conn.execute("PRAGMA table_info(call_events)").fetchall()
    tk = next(c for c in cols if c["name"] == "ticket_id")
    assert tk["notnull"] == 0, "迁移后 ticket_id 应可空"
    rows = conn.execute("SELECT * FROM call_events ORDER BY id").fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "叫号"  # 旧事件保留
    # 收档事件（无号牌）可落库
    conn.execute(
        "INSERT INTO call_events(ticket_id, window_id, action, created_at, note)"
        " VALUES (NULL, 1, '收档', '2026-11-23T21:00:00', '窗口收档')"
    )
    conn.commit()
    conn.close()

    db_mod.init_db()  # 二次初始化幂等，不再重建
    conn = sqlite3.connect(db_file)
    n = conn.execute("SELECT COUNT(*) AS c FROM call_events").fetchone()[0]
    conn.close()
    assert n == 2
