"""`GET /api/nox/dreams` —— 梦的只读投影（2026-09-19 接给 World 页）。

World 页上「梦」那一块要三样：梦的正文、取材（哪些记忆挑出来的）、回响
（那条更老的、被梦勾起来的记忆）。这些全在 `attention/dream.py` 写的
JSONL 里，网页够不着文件，所以补一个只读接口。

## 这一页最要紧的一条

**「还没做过梦」不是故障**。文件不在、是空的、只有半截行（写到一半被杀），
都必须回 200 + 空列表，让前端如实说「还没做过梦」—— 回 500 的话，
她看到的是「这一块坏了」，而真相只是他还没做梦。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from data.store import Store  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402


def _client(monkeypatch, tmp_path) -> TestClient:
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db")))


def _write_dreams(tmp_path, entries: list[dict]) -> None:
    p = tmp_path / "dream-shadow.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                 encoding="utf-8")


def test_没有文件也能读_回空列表而不是500(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.get("/api/nox/dreams")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["items"] == []
    assert body["mode"] in ("off", "shadow")   #: 开没开都如实报


def test_读得回最新的几条_新的在前(monkeypatch, tmp_path):
    _write_dreams(tmp_path, [
        {"date": "2026-09-17", "dream": "第一晚", "materials": [], "echo": None},
        {"date": "2026-09-18", "dream": "第二晚", "materials": [], "echo": None},
        {"date": "2026-09-19", "dream": "第三晚", "materials": [], "echo": None},
    ])
    c = _client(monkeypatch, tmp_path)

    body = c.get("/api/nox/dreams?limit=2").json()
    assert [i["date"] for i in body["items"]] == ["2026-09-19", "2026-09-18"]


def test_梦的正文和取材都在(monkeypatch, tmp_path):
    """前端要的三样：正文、取材、回响 —— 少一样那一块就残缺。"""
    _write_dreams(tmp_path, [{
        "date": "2026-09-19",
        "dream": "他梦见一条河。",
        "materials": [
            {"id": "a1", "name": "糖糖的哲学答案", "age_h": 29.8,
             "importance": "9", "arousal": "0.6"},
        ],
        "echo": {"id": "e1", "name": "糖糖为小克加时间感知", "age_h": 919.5},
        "ts": "2026-09-19T04:51:00+08:00",
    }])
    c = _client(monkeypatch, tmp_path)

    item = c.get("/api/nox/dreams").json()["items"][0]
    assert item["dream"] == "他梦见一条河。"
    assert item["materials"][0]["name"] == "糖糖的哲学答案"
    assert item["echo"]["name"] == "糖糖为小克加时间感知"


def test_半截行不会让整块挂掉(monkeypatch, tmp_path):
    """日志写到一半被杀会留半行 —— 跳过去，别让它毁掉整份。"""
    p = tmp_path / "dream-shadow.jsonl"
    p.write_text(
        json.dumps({"date": "2026-09-18", "dream": "完整的", "materials": []},
                   ensure_ascii=False) + "\n" + '{"date": "2026-09-19", "drea',
        encoding="utf-8",
    )
    c = _client(monkeypatch, tmp_path)

    body = c.get("/api/nox/dreams").json()
    assert [i["date"] for i in body["items"]] == ["2026-09-18"]
