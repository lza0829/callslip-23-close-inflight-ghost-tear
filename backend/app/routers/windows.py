from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services import windows as win_svc

router = APIRouter(tags=["窗口"])


@router.get("/窗口")
def list_windows():
    with db() as conn:
        return win_svc.list_windows(conn)


@router.post("/窗口/{window_id}/收档")
def close_window(window_id: int):
    # 以有在办拒收档为准：窗口仍钉着当前号指针则整笔失败，开闭态不变；
    # 成功/重复/被拒均回收据，结局落入事件流可重放。
    with db(write=True) as conn:
        return win_svc.close_window(conn, window_id)


@router.post("/窗口/{window_id}/开档")
def open_window(window_id: int):
    with db(write=True) as conn:
        return win_svc.open_window(conn, window_id)

