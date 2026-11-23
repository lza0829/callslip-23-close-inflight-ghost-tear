"""收档与在办争用：以有在办拒收档为准。

全局口径：
- 窗口仍钉着当前号指针 → 收档整笔失败，开闭态/指针/号牌状态一律不变，
  在办号继续可办结或过号；仅落一条「拒收档」审计事件。
- 无在办 → 开闭态与「收档」事件同一事务落库，半截即回滚。
- 重复收档/开档 → 幂等重放首次结局，不重复落事件。
"""
import pytest

from conftest import get_ticket, get_window


def close(client, wid):
    r = client.post(f"/api/窗口/{wid}/收档", json={})
    assert r.status_code == 200, r.text
    return r.json()


def open_win(client, wid):
    r = client.post(f"/api/窗口/{wid}/开档", json={})
    assert r.status_code == 200, r.text
    return r.json()


def test_close_rejected_while_in_service(client):
    """有在办拒收档：整笔失败，所有视图口径不变，仅多一条拒收档事件。"""
    before_wins = client.get("/api/窗口").json()
    before_tickets = client.get("/api/号牌").json()
    before_events = client.get("/api/叫号流水").json()

    rc = close(client, 1)

    assert rc["结果"] == "拒收档"
    assert rc["开放"] is True
    assert rc["重复"] is False
    assert "储003" in rc["拒因"]
    assert rc["在办号牌"]["号牌"] == "储003"
    assert rc["在办号牌"]["状态"] == "办理中"
    assert rc["事件编号"] is not None

    # 开闭态、指针、号牌状态零漂移
    assert client.get("/api/窗口").json() == before_wins
    assert client.get("/api/号牌").json() == before_tickets
    w1 = get_window(client, 1)
    assert w1["开放"] is True and w1["当前号"]["号牌"] == "储003"

    # 事件流新增且仅新增一条拒收档，挂在在办号上
    after_events = client.get("/api/叫号流水").json()
    assert len(after_events) == len(before_events) + 1
    ev = after_events[0]
    assert ev["id"] == rc["事件编号"]
    assert ev["动作"] == "拒收档" and ev["号牌"] == "储003" and ev["窗口"] == "一号窗"

    # 总览与等候墙口径不变
    ov = client.get("/api/总览").json()
    assert ov["开放窗口"] == 3 and ov["办理中"] == 1
    assert next(w for w in ov["窗口"] if w["id"] == 1)["有在办"] is True
    wall = client.get("/api/等候墙").json()
    assert [t["号牌"] for t in wall["在办"]] == ["储003"]


def test_in_service_ticket_still_finishable_after_reject(client):
    """拒收档后在办号继续可办结；办结清指针后即可收档。"""
    close(client, 1)
    t = get_ticket(client, "储003")
    r = client.post(f"/api/号牌/{t['id']}/办结", json={})
    assert r.status_code == 200
    assert r.json()["状态"] == "已办结"

    rc = close(client, 1)
    assert rc["结果"] == "已收档" and rc["开放"] is False and rc["重复"] is False

    w1 = get_window(client, 1)
    assert w1["开放"] is False and w1["当前号"] is None
    ov = client.get("/api/总览").json()
    assert ov["开放窗口"] == 2 and ov["办理中"] == 0 and ov["已办结"] == 1
    assert next(w for w in ov["窗口"] if w["id"] == 1)["有在办"] is False
    assert client.get("/api/等候墙").json()["在办"] == []
    evs = client.get("/api/叫号流水").json()
    assert evs[0]["动作"] == "收档" and evs[0]["窗口"] == "一号窗" and evs[0]["号牌"] is None
    assert any(e["动作"] == "办结" and e["号牌"] == "储003" for e in evs)


def test_in_service_ticket_still_skippable_then_close(client):
    """拒收档后在办号继续可过号；过号清指针后收档成功，过号回等候墙。"""
    close(client, 1)
    t = get_ticket(client, "储003")
    r = client.post(f"/api/号牌/{t['id']}/过号", json={})
    assert r.status_code == 200
    assert r.json()["状态"] == "过号"

    rc = close(client, 1)
    assert rc["结果"] == "已收档"
    wall = client.get("/api/等候墙").json()
    assert wall["在办"] == []
    block = next(b for b in wall["墙面"] if b["前缀"] == "储")
    assert block["过号数"] == 1


def test_close_replay_is_idempotent(client):
    """收档结局可重放：重复收档返回首次事件，不重复落库。"""
    t = get_ticket(client, "储003")
    client.post(f"/api/号牌/{t['id']}/办结", json={})
    first = close(client, 1)
    assert first["结果"] == "已收档" and first["重复"] is False

    second = close(client, 1)
    assert second["结果"] == "已收档" and second["重复"] is True
    assert second["事件编号"] == first["事件编号"]
    assert second["时刻"] == first["时刻"]
    assert second["开放"] is False

    evs = [e for e in client.get("/api/叫号流水").json() if e["动作"] == "收档"]
    assert len(evs) == 1 and evs[0]["id"] == first["事件编号"]


def test_open_window_and_replay(client):
    """开档落事件且幂等；开档后窗口立即可叫号。"""
    assert close(client, 2)["结果"] == "已收档"
    ov = client.get("/api/总览").json()
    assert ov["开放窗口"] == 2

    first = open_win(client, 2)
    assert first["结果"] == "已开档" and first["开放"] is True and first["重复"] is False
    second = open_win(client, 2)
    assert second["重复"] is True and second["事件编号"] == first["事件编号"]

    evs = [e for e in client.get("/api/叫号流水").json() if e["动作"] == "开档"]
    assert len(evs) == 1
    assert client.get("/api/总览").json()["开放窗口"] == 3
    r = client.post("/api/叫号", json={"窗口编号": 2})
    assert r.status_code == 200  # 重开后立即可叫


def test_close_unknown_window_404_no_side_effects(client):
    """收档入口异常（窗口不存在）：404 且零落库，无半截。"""
    before_events = client.get("/api/叫号流水").json()
    before_wins = client.get("/api/窗口").json()
    r = client.post("/api/窗口/999/收档", json={})
    assert r.status_code == 404
    assert client.get("/api/叫号流水").json() == before_events
    assert client.get("/api/窗口").json() == before_wins


def test_closed_window_cannot_call_or_occupy(client):
    """收档成功后：该窗不得再叫号占办；窗口页/总览/等候墙口径一致。"""
    t = get_ticket(client, "储003")
    client.post(f"/api/号牌/{t['id']}/办结", json={})
    close(client, 1)

    r = client.post("/api/叫号", json={"窗口编号": 1})
    assert r.status_code == 400 and "已关闭" in r.json()["detail"]
    w1 = get_window(client, 1)
    assert w1["开放"] is False and w1["当前号"] is None
    ov = client.get("/api/总览").json()
    assert ov["办理中"] == 0
    assert client.get("/api/等候墙").json()["在办"] == []


def test_write_txn_rolls_back_on_error(client, monkeypatch):
    """半截须回滚：写事务中途抛错，已执行的开闭态修改一并撤销。"""
    import sqlite3
    import app.db as db_mod

    class Boom(Exception):
        pass

    def broken_close(conn, wid):
        conn.execute("UPDATE windows SET open=0 WHERE id=?", (wid,))
        raise Boom("收档中途故障")

    with pytest.raises(Boom):
        with db_mod.db(write=True) as conn:
            broken_close(conn, 2)

    conn = sqlite3.connect(db_mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT open FROM windows WHERE id=2").fetchone()
    conn.close()
    assert row["open"] == 1  # 未被半截写污染
