"""Haiku 5.5 在模型清单里的样子（2026-10-09，她要试聊）。

守三件事：型号名对（OpenRouter 的写法是 `claude-haiku-5.5`，带点）；走 OpenRouter 的路
（缓存断点那套只在这条路上有）；价格有（bridge 那张表另有 pricing.test.js 守）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.adapters import supports_vision  # noqa: E402
from config import Config  # noqa: E402


def test_在清单里_走_openrouter():
    c = Config().models["haiku-5-5"]
    assert c.model == "anthropic/claude-haiku-5.5"
    assert c.backend == "openrouter"
    assert c.label == "Haiku 5.5"


def test_能看图():
    assert supports_vision("anthropic/claude-haiku-5.5")


def test_价格按官方口径_元每百万():
    p = Config.PRICING_CNY["anthropic/claude-haiku-5.5"]
    #: $0.10 / $0.01 / $0.50 × 7.2
    assert (p["in"], p["hit"], p["out"]) == (0.72, 0.072, 3.6)
    assert p["hit"] < p["in"] < p["out"]


def test_不是默认主线():
    """她说先试聊再定 —— 加进清单不等于换掉主模型。"""
    cfg = Config()
    assert getattr(cfg.primary, "model", "") != "anthropic/claude-haiku-5.5"
