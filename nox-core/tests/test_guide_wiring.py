"""地图的装配（guide/wiring.py）+ 两个仓库新增的只读计数。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent.loop import AgentLoop
from agent import tasks as task_mod
from config import BACKENDS, LLMConfig
from guide.wiring import make_deps
from guide.world_map import build_map
from orders import store as order_store


def _llm(base_url: str, model="m", key="K-SECRET-999", provider="openai_compat"):
    return LLMConfig(provider=provider, model=model, api_key=key, base_url=base_url)


def _nox(**over):
    cfg = SimpleNamespace(
        primary=_llm(BACKENDS["deepseek"].base_url, "main-model"),
        utility=_llm(BACKENDS["zhipu"].base_url, "util-model", key=""),
        vision=_llm("https://example.invalid/v1", "eye-model"),
    )
    base = dict(cfg=cfg, loop=AgentLoop(adapter=None), context=None, orders=None, tasks=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_模型段按base_url认厂商_且不含key():
    out = build_map(make_deps(_nox()), "models")
    assert "主线（和她聊天）：deepseek / main-model，key 已配置" in out
    assert "杂活（意图、总结、记忆整理）：zhipu / util-model，key 未配置" in out
    assert "example.invalid / eye-model" in out
    assert "K-SECRET-999" not in out


def test_anthropic按provider认():
    nox = _nox()
    nox.cfg.primary = _llm("", "claude-x", provider="anthropic")
    assert "anthropic / claude-x" in build_map(make_deps(nox), "models")


def test_仓库没建好_待确认段老实说取不到_不报0(caplog):
    with caplog.at_level(logging.WARNING, logger="guide.world_map"):
        out = build_map(make_deps(_nox()), "pending")
    assert "（取不到：LookupError）" in out
    assert "点单 0" not in out


def test_待确认段读真仓库(tmp_path):
    orders = order_store.OrderStore(tmp_path / "o.db")
    tasks = task_mod.TaskStore(tmp_path / "t.db")
    orders.create(session_id="s", merchant="m", snapshot={}, fingerprint="f")
    tasks.create(session_id="s", goal="g")
    nox = _nox(orders=orders, tasks=tasks)
    assert "点单 1、任务 1" in build_map(make_deps(nox), "pending")


def test_健康段不依赖api_server():
    out = build_map(make_deps(_nox()), "health")
    assert out.startswith("健康：正常；主模型 main-model；停摆的后台任务：")


def test_os页面未接入_且提示别凭记忆答():
    assert "未接入，页面名不要凭记忆答" in build_map(make_deps(_nox()), "os")


# ---------------------------------------------------------------- 仓库计数


def test_订单计数_只数没过期的待确认(tmp_path):
    s = order_store.OrderStore(tmp_path / "o.db")
    now = datetime.now(timezone.utc)
    a = s.create(session_id="s", merchant="m", snapshot={}, fingerprint="a", now=now)
    s.create(session_id="s", merchant="m", snapshot={}, fingerprint="b", now=now)
    assert s.count_pending(now) == 2
    s.set_state(a, order_store.CONFIRMED, now=now)
    assert s.count_pending(now) == 1
    # 过了期但还没被 expire_stale 收掉的不算
    assert s.count_pending(now + order_store.TTL + timedelta(seconds=1)) == 0


def test_任务计数_提议和中断都算_确认后不算(tmp_path):
    s = task_mod.TaskStore(tmp_path / "t.db")
    assert s.count_waiting_for_her() == 0
    t1 = s.create(session_id="s", goal="一")
    s.create(session_id="s", goal="二")
    assert s.count_waiting_for_her() == 2
    s.claim(t1)
    assert s.count_waiting_for_her() == 1
    s.start_next()           # t1 → running
    s.interrupt_running()    # → interrupted，等她点「接着跑」
    assert s.count_waiting_for_her() == 2
