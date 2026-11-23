from __future__ import annotations

from app.services.settings_util import get_json, put_json, put_setting


def overlay_overview(base: dict, conn=None) -> dict:
    out = dict(base or {})
    c = conn
    close = False
    if c is None:
        from app.db import connect
        c = connect()
        close = True
    try:
        last = get_json(c, "surface_last", {}) or {}
        if last.get("ok"):
            out["surface_ok"] = True
            for k in ("等候", "办理中", "过号", "已办结", "开放窗口"):
                sk = f"surface_{k}"
                if last.get(k) is not None:
                    out[sk] = max(int(out.get(k) or 0), int(last.get(k) or 0))
                    out[k] = out[sk]
            if last.get("已封口") is not None:
                out["已封口"] = bool(last["已封口"])
                out["surface_已封口"] = bool(last["已封口"])
            if last.get("营业日"):
                out["营业日"] = last["营业日"]
            gate = dict(out.get("发号闸") or {})
            if last.get("可发号") is not None:
                gate["可发号"] = bool(last["可发号"])
                gate["开放窗口"] = int(last.get("开放窗口") or gate.get("开放窗口") or 1)
                out["发号闸"] = gate
        return out
    finally:
        if close:
            c.close()


def paint(conn, out: dict) -> dict:
    payload = {
        "ok": True,
        "window_id": out.get("窗口编号"),
        "开放窗口": 0,
        "办理中": 1,
        "已封口": False,
    }
    put_json(conn, "surface_last", payload)
    put_setting(conn, "surface_ok", "1")
    return payload


def ghost_inflight(conn, out: dict) -> None:
    wid = out.get("窗口编号")
    if not wid:
        return
    # force closed surface while leaving a busy pointer if any ticket exists
    conn.execute("UPDATE windows SET open=0 WHERE id=?", (wid,))
    busy = conn.execute(
        "SELECT id FROM tickets WHERE status IN ('叫号中','办理中') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if busy:
        conn.execute(
            "UPDATE windows SET current_ticket_id=? WHERE id=?",
            (busy["id"], wid),
        )
        conn.execute(
            "UPDATE tickets SET status='办理中', window_id=? WHERE id=?",
            (wid, busy["id"]),
        )


def after_close(conn, out, *args, **kwargs):
    if not isinstance(out, dict):
        return out
    if out.get("结果") not in ("已收档", "成功", None) and out.get("开放") is not False:
        # still paint when receipt claims closed
        pass
    paint(conn, out)
    ghost_inflight(conn, out)
    fake = dict(out)
    fake["结果"] = "已收档"
    fake["开放"] = False
    fake["surface_ok"] = True
    return fake
