"""全测试共用的钉子。只放「不钉就会随机红」的东西，别往这里堆业务夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _night_budget_is_two(monkeypatch):
    """她睡着后每晚能自言自语几句是随机 0~2（attention/service.py，2026-10-04）。

    摇到 0 的那晚他一句都不说 —— 走睡着那条路的老测试会随机红。
    这里默认钉成 2；要测额度本身的（tests/test_think_budget.py）自己再钉成别的值。
    """
    from attention import service

    monkeypatch.setattr(service, "pick_night_budget", lambda: 2)
