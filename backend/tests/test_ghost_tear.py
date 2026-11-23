"""在办幽灵与收档半截夹具：证据不得撕裂。

全局口径（以有在办拒收档为准，唯一）要求收档灯必须能由
开闭态 ∪ 指针清空 ∪ 原在办号终态同命运重放，禁止只改收档大字。
本文件在 SQL 探针层复现撕裂态，并锁定：
- 幽灵夹具（窗已关、指针仍钉在办号）下各视图只报真相、不粉刷；
- 真实收档链路（拒收档 / 办结 / 过号 / 收档）永远落不出撕裂态；
- 半截写整笔回滚，号牌办中态与窗指针在同一探针读取中双双不变；
- 收档与叫号并发对撞后，库内无撕裂行，总览办中桶无幽灵。
"""
import sqlite3
import threading
import time

import httpx
import pytest
import uvicorn

import app.db as db_mod
from app.main import app
from app.seed import seed_if_empty
from app.services.core import now
from conftest import get_ticket, get_window


def _probe(db_path: str) -> dict:
    """同库同读：把窗态、指针、号牌办中态一次读出，杜绝视图间时差。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        pinned_on_closed = conn.execute(
            "SELECT id, current_ticket_id FROM windows "
            "WHERE open=0 AND current_ticket_id IS NOT NULL"
        ).fetchall()
        busy_on_closed = conn.execute(
            """SELECT t.id, t.slip_no FROM tickets t
               JOIN windows w ON w.id = t.window_id
               WHERE t.status IN ('叫号中', '办理中') AND w.open=0"""
        ).fetchall()
        ptr_count = conn.execute(
            "SELECT COUNT(*) AS c FROM windows WHERE current_ticket_id IS NOT NULL"
        ).fetchone()["c"]
        busy_count = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE status='办理中'"
        ).fetchone()["c"]
        dangling = conn.execute(
            """SELECT w.id FROM windows w
               LEFT JOIN tickets t ON t.id = w.current_ticket_id
               WHERE w.current_ticket_id IS NOT NULL AND t.id IS NULL"""
        ).fetchall()
        return {
            "关窗钉指针": [dict(r) for r in pinned_on_closed],
            "在办号挂关窗": [dict(r) for r in busy_on_closed],
            "指针数": ptr_count,
            "办中数": busy_count,
            "悬空指针": [dict(r) for r in dangling],
        }
    finally:
        conn.close()


def _assert_no_tear(db_path: str) -> None:
    p = _probe(db_path)
    assert p["关窗钉指针"] == [], f"幽灵：窗已关指针仍钉 {p['关窗钉指针']}"
    assert p["在办号挂关窗"] == [], f"幽灵：号仍办中窗已关 {p['在办号挂关窗']}"
    assert p["悬空指针"] == [], f"指针指向不存在号牌 {p['悬空指针']}"
    assert p["指针数"] == p["办中数"], f"指针与办中桶不同源 {p}"


def _sweep_views(client) -> dict:
    """收档口、窗态、号牌办中态、等候墙、总览、审计一次同刷读取。"""
    return {
        "窗口": client.get("/api/窗口").json(),
        "总览": client.get("/api/总览").json(),
        "等候墙": client.get("/api/等候墙").json(),
        "号牌": client.get("/api/号牌").json(),
        "流水": client.get("/api/叫号流水").json(),
    }


def test_ghost_fixture_closed_window_pinned_pointer_views_report_truth(client):
    """夹具复现「窗已关指针仍钉」：只读视图必须如实呈现，不得粉刷成空闲。"""
    db_path = str(db_mod.DB_PATH)
    pinned = get_window(client, 1)["当前号"]["id"]
    # 手工植入撕裂态（模拟历史半截写），只读链路不许掩盖它
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE windows SET open=0 WHERE id=1")  # 指针仍钉储003
    conn.commit()
    conn.close()

    views = _sweep_views(client)
    w1 = next(w for w in views["窗口"] if w["id"] == 1)
    assert w1["开放"] is False and w1["当前号"]["号牌"] == "储003"
    ov = views["总览"]
    assert ov["办理中"] == 1 and ov["开放窗口"] == 2
    assert next(w for w in ov["窗口"] if w["id"] == 1)["有在办"] is True
    assert [t["号牌"] for t in views["等候墙"]["在办"]] == ["储003"]
    assert any(t["号牌"] == "储003" and t["状态"] == "办理中" for t in views["号牌"])

    # 探针确认夹具本身确实是撕裂态（被测的是修复后系统不再产生它，而非看不见它）
    assert _probe(db_path)["关窗钉指针"] == [{"id": 1, "current_ticket_id": pinned}]

    # 正规收口：开档 → 办结清指针 → 收档，撕裂态被真实愈合
    r = client.post("/api/窗口/1/开档", json={})
    assert r.status_code == 200 and r.json()["结果"] == "已开档"
    t = get_ticket(client, "储003")
    r = client.post(f"/api/号牌/{t['id']}/办结", json={})
    assert r.status_code == 200 and r.json()["状态"] == "已办结"
    r = client.post("/api/窗口/1/收档", json={})
    assert r.status_code == 200 and r.json()["结果"] == "已收档"

    _assert_no_tear(db_path)
    views = _sweep_views(client)
    w1 = next(w for w in views["窗口"] if w["id"] == 1)
    assert w1["开放"] is False and w1["当前号"] is None
    assert views["总览"]["办理中"] == 0
    assert views["等候墙"]["在办"] == []
    actions = [e["动作"] for e in views["流水"]]
    assert "收档" in actions and "办结" in actions
    # 愈合过程中不得有任何一条收档事件发生在指针未清空时
    close_ev = next(e for e in views["流水"] if e["动作"] == "收档")
    assert close_ev["号牌"] is None


def test_reject_close_leaves_zero_drift_across_all_views(client):
    """拒收档：开闭态/指针/号牌办中态同读零漂移，仅多一条拒收档审计。"""
    db_path = str(db_mod.DB_PATH)
    before = _probe(db_path)
    r = client.post("/api/窗口/1/收档", json={})
    assert r.status_code == 200
    rc = r.json()
    assert rc["结果"] == "拒收档" and rc["开放"] is True
    after = _probe(db_path)
    assert after == before  # 同读探针：窗态、指针、办中态一个字都没改

    views = _sweep_views(client)
    w1 = next(w for w in views["窗口"] if w["id"] == 1)
    assert w1["开放"] is True and w1["当前号"]["号牌"] == "储003"
    assert views["总览"]["办理中"] == 1
    assert [t["号牌"] for t in views["等候墙"]["在办"]] == ["储003"]
    ev = views["流水"][0]
    assert ev["动作"] == "拒收档" and ev["号牌"] == "储003"


def test_half_close_rolls_back_pointer_and_ticket_status_together(client):
    """半截夹具：收档写事务中途炸掉，指针与号牌办中态在同一探针读取中双双回滚。"""
    db_path = str(db_mod.DB_PATH)

    class Boom(Exception):
        pass

    def broken_close(conn, wid):
        # 与真实收档成功路径相同的前半步写，随后故障
        conn.execute("UPDATE windows SET open=0 WHERE id=?", (wid,))
        conn.execute(
            "INSERT INTO call_events(ticket_id, window_id, action, created_at, note) "
            "VALUES (NULL,?, '收档', ?, '')",
            (wid, now()),
        )
        raise Boom("收档中途故障")

    import app.db as dbm
    with pytest.raises(Boom):
        with dbm.db(write=True) as conn:
            broken_close(conn, 2)

    p = _probe(db_path)
    # 二号窗半截写被整体撤销；一号窗在办号完全不受牵连
    assert p["关窗钉指针"] == [] and p["悬空指针"] == []
    assert p["指针数"] == 1 and p["办中数"] == 1
    w1 = get_window(client, 1)
    assert w1["开放"] is True and w1["当前号"]["号牌"] == "储003"
    w2 = get_window(client, 2)
    assert w2["开放"] is True and w2["当前号"] is None
    assert all(e["动作"] != "收档" for e in client.get("/api/叫号流水").json())


def test_successful_close_clears_pointer_and_bucket_in_same_txn(client):
    """收档成功：窗态翻转、指针清空、办中桶、等候墙、审计同刷，无撕裂。"""
    db_path = str(db_mod.DB_PATH)
    t = get_ticket(client, "储003")
    client.post(f"/api/号牌/{t['id']}/办结", json={})
    rc = client.post("/api/窗口/1/收档", json={}).json()
    assert rc["结果"] == "已收档" and rc["事件编号"] is not None

    _assert_no_tear(db_path)
    views = _sweep_views(client)
    w1 = next(w for w in views["窗口"] if w["id"] == 1)
    assert w1["开放"] is False and w1["当前号"] is None
    ov = views["总览"]
    assert ov["开放窗口"] == 2 and ov["办理中"] == 0 and ov["已办结"] == 1
    assert next(w for w in ov["窗口"] if w["id"] == 1)["有在办"] is False
    assert views["等候墙"]["在办"] == []
    assert views["流水"][0]["动作"] == "收档"
    # 回执可由事件流重放：事件编号真实存在且窗口一致
    ev = next(e for e in views["流水"] if e["id"] == rc["事件编号"])
    assert ev["动作"] == "收档" and ev["窗口"] == "一号窗"


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "race.db")
    seed_if_empty()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("uvicorn 未能启动")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as c:
        yield c
    srv.should_exit = True
    thread.join(timeout=10)


def _race(fn_a, fn_b):
    barrier = threading.Barrier(2)
    out = [None, None]

    def run(i, fn):
        barrier.wait()
        out[i] = fn()

    ts = [threading.Thread(target=run, args=(0, fn_a)), threading.Thread(target=run, args=(1, fn_b))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def test_concurrent_close_call_leaves_no_ghost_in_db_or_overview(server):
    """收档叫号对撞 N 轮后：库探针无撕裂行，总览办中桶与指针同源、无幽灵。"""
    c = server
    db_path = str(db_mod.DB_PATH)
    t = next(x for x in c.get("/api/号牌").json() if x["号牌"] == "储003")
    c.post(f"/api/号牌/{t['id']}/办结", json={})

    saw_reject = saw_close = False
    for _ in range(8):
        wins = c.get("/api/窗口").json()
        w1 = next(w for w in wins if w["id"] == 1)
        if not w1["开放"]:
            c.post("/api/窗口/1/开档", json={})
        if w1["当前号"]:
            c.post(f"/api/号牌/{w1['当前号']['id']}/办结", json={})
        c.post("/api/取号", json={"种类编号": 1})

        close_r, call_r = _race(
            lambda: c.post("/api/窗口/1/收档", json={}),
            lambda: c.post("/api/叫号", json={"窗口编号": 1}),
        )
        assert close_r.status_code == 200
        assert "locked" not in (call_r.text or "").lower()
        if call_r.status_code == 200:
            saw_reject = True
            assert close_r.json()["结果"] == "拒收档"
        else:
            saw_close = True
            assert close_r.json()["结果"] == "已收档"

        # 每轮并发收敛后立即同读探针 + 总览，均不得有幽灵
        _assert_no_tear(db_path)
        ov = c.get("/api/总览").json()
        for w in ov["窗口"]:
            assert not (w["有在办"] and not w["开放"]), f"总览幽灵 {w}"
        wall = c.get("/api/等候墙").json()
        assert all(x["状态"] == "办理中" for x in wall["在办"])

    assert saw_reject and saw_close  # 两种串行结局都真实发生
    _assert_no_tear(db_path)
