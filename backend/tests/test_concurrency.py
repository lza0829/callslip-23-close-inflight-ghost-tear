"""并发收档与叫号：不得半截。

每轮把一号窗复位到「开放且空闲」，取一个等候号后，
收档与叫号在同一栅栏后并发发出。两种合法结局：
- 叫号先落库 → 收档被拒（拒收档），窗口开、指针在；
- 收档先落库 → 叫号被「窗口已关闭」拒绝，窗口收、指针空。
绝不合法：窗已收档却仍钉着在办号（收档后仍占办）。
"""
import threading
import time

import httpx
import pytest
import uvicorn

import app.db as db_mod
from app.main import app
from app.seed import seed_if_empty

ROUNDS = 25


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
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("uvicorn 未能启动")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as c:
        yield c
    srv.should_exit = True
    t.join(timeout=10)


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


def _reset_window1(c):
    """把一号窗复位为开放且空闲，并保证有等候号可叫。"""
    wins = c.get("/api/窗口").json()
    w1 = next(w for w in wins if w["id"] == 1)
    if not w1["开放"]:
        r = c.post("/api/窗口/1/开档", json={})
        assert r.status_code == 200
    if w1["当前号"]:
        r = c.post(f"/api/号牌/{w1['当前号']['id']}/办结", json={})
        assert r.status_code == 200
    r = c.post("/api/取号", json={"种类编号": 1})  # 一号窗可办个人储蓄
    assert r.status_code == 200


def test_concurrent_close_and_call_never_half_done(server):
    c = server
    # 清掉种子在办号，让一号窗从空闲起跑
    t = next(x for x in c.get("/api/号牌").json() if x["号牌"] == "储003")
    c.post(f"/api/号牌/{t['id']}/办结", json={})

    outcomes = {"叫号胜": 0, "收档胜": 0}
    for _ in range(ROUNDS):
        _reset_window1(c)
        close_r, call_r = _race(
            lambda: c.post("/api/窗口/1/收档", json={}),
            lambda: c.post("/api/叫号", json={"窗口编号": 1}),
        )
        assert close_r.status_code == 200, close_r.text  # 收档入口永远给回执
        assert call_r.status_code in (200, 400), call_r.text
        assert "locked" not in call_r.text.lower()

        w1 = next(w for w in c.get("/api/窗口").json() if w["id"] == 1)
        # 核心不变式：窗已收档 ⇒ 指针必空；指针在 ⇒ 窗必开
        assert not (not w1["开放"] and w1["当前号"]), f"半截：收档后仍占办 {w1}"
        rc = close_r.json()
        if call_r.status_code == 200:
            outcomes["叫号胜"] += 1
            assert rc["结果"] == "拒收档" and w1["开放"] and w1["当前号"]
        else:
            outcomes["收档胜"] += 1
            assert "已关闭" in call_r.json()["detail"]
            assert rc["结果"] == "已收档" and not w1["开放"] and w1["当前号"] is None

    # 终态全视图一致：总览有在办 ⇒ 窗口开放；等候墙在办与指针同源
    ov = c.get("/api/总览").json()
    for w in ov["窗口"]:
        assert not (w["有在办"] and not w["开放"])
    wall = c.get("/api/等候墙").json()
    assert all(t["状态"] in ("叫号中", "办理中") for t in wall["在办"])
    # 两种结局都应真实出现过（并发确实在交替胜负）
    assert outcomes["叫号胜"] > 0 and outcomes["收档胜"] > 0, outcomes
