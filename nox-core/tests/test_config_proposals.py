"""配置层 P5：我提议、她点头（2026-10-13；设计稿第十一节）。

钉住的：
  · 🔴 工具只出卡，什么配置都不改；她点「同意」才由确认端点执行；点两下不会执行两次
  · 提议不成立当场挡回去，并告诉我能选什么（不出一张注定失败的卡）
  · need_key：key 只在她同意那一步出现 —— 不进提议、不进响应、不进日志、不进库
  · 测试会话不许出卡
  · 🔴 哨兵：工具文件碰不到任何会写配置的东西；`proposals.apply` 只在确认端点里被调
  · 工具在 config 包里（聊到模型 / key 才递给我），排在工具串最后
"""

from __future__ import annotations

import json
import logging
import re
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.llm import Turn, Usage
from settings_store import probe, proposals, slots
from tools import config_propose
from tools import context as tool_context

from tests.settings_helpers import MARKERS

KEY = "KEY-MARK-PROPOSAL-1"


@pytest.fixture
def app(monkeypatch, tmp_path):
    import nox as nox_mod
    from tests.test_settings_views import _client

    c, core = _client(monkeypatch, tmp_path)
    picked = []
    core.user_model = types.MethodType(nox_mod.Nox.user_model, core)
    core.set_chat_model = lambda k: picked.append(k) or True
    core.chat_model_key = lambda: picked[-1] if picked else None
    core.picked = picked
    return c, core


def propose(core, sid="s-1", **args):
    with tool_context.scope() as ctx:
        ctx.session_id = sid
        out = config_propose._propose(args, core_ref=lambda: core, session_id_ref=lambda: sid)
        return out, ctx.attachments


def _choice(core, slot):
    c = slots.choice(core.settings, slot)
    return c and c["model"]["model_name"]


def test_红线_只出卡不改配置_她同意了才换(app):
    c, core = app
    out, atts = propose(core, kind="slot", slot="utility", provider_id="deepseek", model_name="deepseek-flash",
                        reason="杂活用 DeepSeek 更便宜")
    assert "她点了「同意」才会生效" in out
    assert [a["type"] for a in atts] == ["config"]
    card = atts[0]["card"]
    assert card["summary"] == "把杂活（Utility）从 glm-5.3-flash 换成 DeepSeek V4.1 Flash"
    assert _choice(core, "chat.utility") is None, "出卡时就改了 —— 越过了她"
    pid = atts[0]["proposal_id"]
    r = c.post(f"/api/nox/config/proposals/{pid}/confirm", json={})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert _choice(core, "chat.utility") == "deepseek-flash"
    again = c.post(f"/api/nox/config/proposals/{pid}/confirm", json={})
    assert again.status_code == 409, "点两下执行了两次"


def test_她不要_就不换_之后也同意不了(app):
    c, core = app
    _, atts = propose(core, kind="slot", slot="vision", provider_id="deepseek", model_name="deepseek-flash", reason="x")
    pid = atts[0]["proposal_id"]
    assert c.post(f"/api/nox/config/proposals/{pid}/reject").json()["status"] == "rejected"
    assert c.post(f"/api/nox/config/proposals/{pid}/confirm", json={}).status_code == 409
    assert _choice(core, "vision") is None
    assert c.get(f"/api/nox/config/proposals/{pid}").json()["status"] == "rejected"


def test_不成立的提议当场挡回去_告诉我能选什么_不出卡(app):
    _, core = app
    with pytest.raises(RuntimeError, match="能选") as e:
        propose(core, kind="slot", slot="vision", provider_id="deepseek", model_name="deepseek-v4-pro", reason="x")
    assert "deepseek/deepseek-flash" in str(e.value)
    with pytest.raises(RuntimeError, match="slot 只能是"):
        propose(core, kind="slot", slot="brain", provider_id="deepseek", model_name="deepseek-flash", reason="x")
    with pytest.raises(RuntimeError, match="已经有"):
        propose(core, kind="add_model", provider_id="deepseek", model_name="deepseek-flash", reason="x")
    proposals._ensure(core.settings)
    with core.settings._lock:
        n = core.settings._conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    assert n == 0


def test_聊天模型也能提议_同意时存到服务器那一份(app):
    c, core = app
    _, atts = propose(core, kind="slot", slot="primary", provider_id="deepseek", model_name="deepseek-flash", reason="x")
    c.post(f"/api/nox/config/proposals/{atts[0]['proposal_id']}/confirm", json={})
    assert core.picked == ["v4-flash"]


def test_红线_need_key_key只在同意那一步出现_哪儿都留不下(app, caplog):
    c, core = app
    _, atts = propose(core, kind="need_key", provider_id="openrouter", reason="Claude 那条 401 了")
    pid = atts[0]["proposal_id"]
    assert c.post(f"/api/nox/config/proposals/{pid}/confirm", json={}).status_code == 400
    assert c.get(f"/api/nox/config/proposals/{pid}").json()["status"] == "proposed", "没填 key 不许把提议占掉"
    with caplog.at_level(logging.DEBUG):
        r = c.post(f"/api/nox/config/proposals/{pid}/confirm", json={"key": KEY})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert core.config_keys.get("openrouter") == KEY
    from tests.settings_helpers import scan_bytes
    for blob in (r.text, caplog.text, scan_bytes(Path(core.cfg.db_path).parent / "settings.db").decode("utf-8", "ignore"),
                 json.dumps(atts, ensure_ascii=False)):
        assert KEY not in blob


def test_加型号_同意时真测_没过就记失败(app, monkeypatch):
    c, core = app
    monkeypatch.setattr(probe, "_default_make", lambda llm: SimpleNamespace(
        cfg=llm, complete=lambda *a, **k: Turn(stop_reason="end_turn", text="", usage=Usage(output_tokens=2048))))
    _, atts = propose(core, kind="add_model", provider_id="deepseek", model_name="deepseek-v5", reason="新出的")
    r = c.post(f"/api/nox/config/proposals/{atts[0]['proposal_id']}/confirm", json={})
    assert r.status_code == 400 and "没有保存" in r.json()["detail"]
    p = c.get(f"/api/nox/config/proposals/{atts[0]['proposal_id']}").json()
    assert p["status"] == "failed" and "正文是空的" in p["result"]
    assert not any(m["model_name"] == "deepseek-v5" for m in core.settings.models())


def test_测试会话不出卡(app):
    _, core = app
    with pytest.raises(RuntimeError, match="测试会话"):
        propose(core, sid="test-abc", kind="need_key", provider_id="deepseek", reason="x")


def test_工具声明_只写提议_在config包里_接进了create_app(app):
    from agent import toolpacks

    _, core = app
    assert config_propose.PROPOSE.side_effect == "write" and config_propose.PROPOSE.confirm_via
    assert "propose_config_change" in core.loop.registered, "create_app 没注册"
    owner = [p.name for p in toolpacks.PACKS if p.owns("propose_config_change")]
    assert owner == ["config"]
    assert re.search(next(p for p in toolpacks.PACKS if p.name == "config").triggers, "换个杂活模型吧")


def test_红线_哨兵_工具碰不到会写配置的东西_apply只在确认端点():
    root = Path(__file__).resolve().parents[1]
    src = (root / "tools" / "config_propose.py").read_text(encoding="utf-8")
    src = re.sub(r'"""[\s\S]*?"""', "", src)                 # 只扫代码：文档字符串里写着「不许碰 X」不算碰
    src = re.sub(r"#[^\n]*", "", src)
    assert "_propose" in src and "proposals.create" in src, "扫的不是那个文件（扫描本身坏了）"
    for bad in ("settings_store.keys", "settings_store.edit", "settings_store.migrate", "set_choice",
                "proposals.apply", "add_models", "config_keys.set"):
        assert bad not in src, f"工具文件里出现了 {bad} —— 我能绕过她直接改配置"
    callers = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if rel.startswith((".venv/", "tests/", "settings_store/")):
            continue
        if "proposals.apply(" in py.read_text(encoding="utf-8", errors="ignore"):
            callers.append(rel)
    assert callers == ["api/server.py"], callers
