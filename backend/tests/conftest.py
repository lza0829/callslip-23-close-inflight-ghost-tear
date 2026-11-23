import os
import sys
import tempfile
from pathlib import Path

# 必须在任何 app 模块导入前指向独立临时库，避免污染仓库 data/
os.environ["CALLSLIP_DB"] = str(Path(tempfile.mkdtemp()) / "bootstrap.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.db as db_mod
from app.main import app
from app.seed import seed_if_empty


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每用例一座全新种子库：三窗开放，一号窗钉着在办号储003。"""
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    seed_if_empty()
    with TestClient(app) as c:
        yield c


def get_ticket(client, slip):
    rows = client.get("/api/号牌").json()
    return next(t for t in rows if t["号牌"] == slip)


def get_window(client, wid):
    return next(w for w in client.get("/api/窗口").json() if w["id"] == wid)
