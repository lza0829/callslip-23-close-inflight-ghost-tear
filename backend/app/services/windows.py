from __future__ import annotations
from fastapi import HTTPException
from app.services.core import now, parse_kind_ids, ticket_dict, ticket_row, window_row

# 窗口开闭事件动作名（写入 call_events.action，与叫号/过号/重叫/办结同流）
ACT_CLOSE = "收档"
ACT_OPEN = "开档"
ACT_CLOSE_REJECT = "拒收档"


def list_windows(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM windows ORDER BY id").fetchall()
    out = []
    for r in rows:
        kids = parse_kind_ids(r["kind_ids"])
        kinds = []
        for kid in kids:
            k = conn.execute(
                "SELECT id, name, prefix FROM business_kinds WHERE id=?", (kid,)
            ).fetchone()
            if k:
                kinds.append({"id": k["id"], "名称": k["name"], "前缀": k["prefix"]})
        cur = None
        if r["current_ticket_id"]:
            cur = ticket_dict(conn, ticket_row(conn, r["current_ticket_id"]))
        out.append(
            {
                "id": r["id"],
                "名称": r["name"],
                "开放": bool(r["open"]),
                "可办种类": kinds,
                "当前号": cur,
            }
        )
    return out


def _receipt(win_id: int, win_name: str, action: str, result: str, open_: bool,
             ts: str, *, repeated: bool = False, reason: str | None = None,
             ticket: dict | None = None, event_id: int | None = None) -> dict:
    """收档/开档回执：无论成败都返回同一结构，结局可凭事件编号重放核对。"""
    return {
        "窗口编号": win_id,
        "窗口": win_name,
        "动作": action,
        "结果": result,
        "开放": open_,
        "重复": repeated,
        "拒因": reason,
        "在办号牌": ticket,
        "事件编号": event_id,
        "时刻": ts,
    }


def _latest_event(conn, window_id: int, action: str):
    return conn.execute(
        """SELECT id, created_at FROM call_events
           WHERE window_id=? AND action=? ORDER BY id DESC LIMIT 1""",
        (window_id, action),
    ).fetchone()


def close_window(conn, window_id: int) -> dict:
    """收档（以有在办拒收档为准）：
    - 窗口当前号指针仍钉着在办号 → 整笔失败：开闭态、指针、号牌状态一律不动，
      仅落一条「拒收档」事件备查，回执给出拒因；在办号继续可办结或过号。
    - 无在办 → 同一事务内落开闭态与「收档」事件，半截即整体回滚。
    - 对已收档窗口重复收档 → 幂等重放：不改状态、不重复落事件，
      回执指向首次收档事件。
    """
    win = window_row(conn, window_id)
    if not win["open"]:
        ev = _latest_event(conn, window_id, ACT_CLOSE)
        return _receipt(
            win["id"], win["name"], ACT_CLOSE, "已收档", False,
            ev["created_at"] if ev else now(),
            repeated=True, event_id=ev["id"] if ev else None,
        )
    cur_id = win["current_ticket_id"]
    if cur_id:
        t = ticket_row(conn, cur_id)
        reason = f"窗口仍有在办号{t['slip_no']}，须先办结或过号，收档整笔失败"
        ts = now()
        cur = conn.execute(
            "INSERT INTO call_events(ticket_id, window_id, action, created_at, note) VALUES (?,?,?,?,?)",
            (cur_id, window_id, ACT_CLOSE_REJECT, ts, reason),
        )
        return _receipt(
            win["id"], win["name"], ACT_CLOSE, "拒收档", True, ts,
            reason=reason, ticket=ticket_dict(conn, t), event_id=cur.lastrowid,
        )
    ts = now()
    conn.execute("UPDATE windows SET open=0 WHERE id=?", (window_id,))
    cur = conn.execute(
        "INSERT INTO call_events(ticket_id, window_id, action, created_at, note) VALUES (NULL,?,?,?,?)",
        (window_id, ACT_CLOSE, ts, "窗口收档，收档时无在办号"),
    )
    return _receipt(win["id"], win["name"], ACT_CLOSE, "已收档", False, ts,
                    event_id=cur.lastrowid)


def open_window(conn, window_id: int) -> dict:
    """开档：收档的逆操作。已开放时幂等重放，不重复落事件。"""
    win = window_row(conn, window_id)
    if win["open"]:
        ev = _latest_event(conn, window_id, ACT_OPEN)
        return _receipt(
            win["id"], win["name"], ACT_OPEN, "已开档", True,
            ev["created_at"] if ev else now(),
            repeated=True, event_id=ev["id"] if ev else None,
        )
    ts = now()
    conn.execute("UPDATE windows SET open=1 WHERE id=?", (window_id,))
    cur = conn.execute(
        "INSERT INTO call_events(ticket_id, window_id, action, created_at, note) VALUES (NULL,?,?,?,?)",
        (window_id, ACT_OPEN, ts, "窗口开档"),
    )
    return _receipt(win["id"], win["name"], ACT_OPEN, "已开档", True, ts,
                    event_id=cur.lastrowid)


def list_kinds(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM business_kinds ORDER BY id").fetchall()
    return [
        {
            "id": r["id"],
            "名称": r["name"],
            "前缀": r["prefix"],
            "序号": r["seq"],
            "启用": bool(r["active"]),
        }
        for r in rows
    ]


def set_kind_active(conn, kind_id: int, active: bool) -> dict:
    row = conn.execute("SELECT * FROM business_kinds WHERE id=?", (kind_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="业务种类不存在")
    conn.execute(
        "UPDATE business_kinds SET active=? WHERE id=?",
        (1 if active else 0, kind_id),
    )
    return next(k for k in list_kinds(conn) if k["id"] == kind_id)

