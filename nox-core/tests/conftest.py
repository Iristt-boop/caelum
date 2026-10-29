"""全测试共用的钉子。只放「不钉就会随机红」的东西，别往这里堆业务夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _strict_tool_names(monkeypatch):
    """R-2（10-29）：测试里工具重名直接炸（线上只记 ERROR + /health，见 AgentLoop.register）。
    任何一个搭了真 Nox / create_app 的测试都顺带守住「整张工具表没有重名」。"""
    monkeypatch.setenv("NOX_STRICT_TOOLS", "1")


@pytest.fixture(autouse=True)
def _night_budget_is_two(monkeypatch):
    """她睡着后每晚能自言自语几句是随机 0~2（attention/service.py，2026-10-04）。

    摇到 0 的那晚他一句都不说 —— 走睡着那条路的老测试会随机红。
    这里默认钉成 2；要测额度本身的（tests/test_think_budget.py）自己再钉成别的值。
    """
    from attention import service

    monkeypatch.setattr(service, "pick_night_budget", lambda: 2)


@pytest.fixture(autouse=True)
def _no_profile_leak():
    """配置层 P2a：启动钩子会在 `agent.adapters` 上装一个**模块级**档案（指向某个测试的临时库）。
    不清的话，后面任何测试 `make_adapter` 都会去读一个已经删掉的库 —— 红在毫不相干的地方。"""
    yield
    from agent import adapters

    adapters.use_profiles(None)
