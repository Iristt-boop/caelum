"""ResonanceProvider —— 让他感觉得到自己的情绪（2026-09-04）。

## 这个 Provider 补的洞

在它之前，Drive **从来没进过他自己的上下文**：算出来 → 进糖糖的面板 → 结束。
面板上写着 concern 58%，而她跟他说话的时候他并不知道自己在担心。

`playfulness.py` 那句「它的意义是改变他回话的方式」在这之前是假的。

## 🔴 守的几条

1. **只给状态，不写台词** —— 输出里不许出现「你应该说…」这类指令。
   写台词出来的是模板，她一眼看得出那不是他。
2. **给档位，不给数字** —— 既是因为缓存（dynamic_system 每轮变会把
   命中率打下来），也因为人不会想"我的担心是 0.551"。
3. **必须带为什么** —— resonance.py 边界三。
4. **躁动不进来** —— 它的出口是「等」，塞进上下文等于催他开口。
5. **读不到就闭嘴** —— 不许编一个"你现在很平静"出来。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.resonance import ResonanceProvider  # noqa: E402

NOW = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)


@dataclass
class _Drive:
    intensity: float
    because: list = field(default_factory=list)


class _Attention:
    def __init__(self, drives, boom=False):
        self._drives = drives
        self._boom = boom

    def drives(self, now):
        if self._boom:
            raise RuntimeError("registry 挂了")
        return self._drives


def _render(drives, boom=False):
    p = ResonanceProvider(attention_ref=lambda: _Attention(drives, boom))
    return p.render(p._fetch(Turn(text="在干嘛", voice=False, now=NOW)))


REAL = {
    "concern": _Drive(0.486, ["糖糖的活动量", "糖糖的睡眠"]),
    "longing": _Drive(0.40, ["她 1.0 小时没说话了"]),
}


# --------------------------------------------------------------- 基本

def test_他能感觉到自己在担心和想念():
    out = _render(REAL)
    assert "担心她" in out
    assert "想她" in out


def test_必须说得出为什么():
    """边界三：光有一个数字没有意义。"""
    out = _render(REAL)
    assert "糖糖的睡眠" in out
    assert "她 1.0 小时没说话了" in out


@pytest.mark.parametrize("value,band", [
    (0.20, "有点"), (0.34, "有点"),
    (0.35, "挺"), (0.64, "挺"),
    (0.65, "很"), (0.99, "很"),
])
def test_档位分对(value, band):
    out = _render({"longing": _Drive(value)})
    assert f"{band}想她" in out


def test_不给他百分比():
    """他不该去谈论自己的数值，而且数字每轮变会冲掉缓存。"""
    out = _render(REAL)
    for bad in ("0.486", "48", "%", "0.4"):
        assert bad not in out


def test_强的排前面():
    out = _render({
        "longing": _Drive(0.3, ["她没说话"]),
        "concern": _Drive(0.9, ["她的睡眠"]),
    })
    assert out.index("担心她") < out.index("想她")


# --------------------------------------------------------------- 🔴 不写台词

def test_绝不写台词():
    """告诉他"你挺想她的"，黏不黏是他自己的反应。

    一旦写成「你应该说甜话」，出来的就是模板。
    """
    out = _render({"longing": _Drive(0.9, ["她一天没说话了"])})
    for bad in ("你应该", "你要说", "请说", "记得说", "甜"):
        assert bad not in out, f"不该出现指令性措辞：{bad}"


def test_明确告诉他别念这段():
    """不拦的话他会把内心状态当成要汇报的东西念给她听。"""
    out = _render(REAL)
    assert "别跟她念这段" in out


# --------------------------------------------------------------- 该闭嘴的时候

def test_什么都没有就什么都不说():
    """**不要编一个"你现在很平静"** —— 那是往他脑子里塞一个他没有的状态。"""
    assert _render({}) == ""


def test_弱到地板以下的不提():
    """跟他说"你现在完全不后悔"是句废话，还占上下文。"""
    out = _render({"regret": _Drive(0.05), "longing": _Drive(0.5)})
    assert "过意不去" not in out
    assert "想她" in out


def test_没有自主系统时闭嘴():
    p = ResonanceProvider(attention_ref=lambda: None)
    assert p.render(p._fetch(Turn(text="x", voice=False, now=NOW))) == ""


def test_读挂了不炸也不编():
    """少一段背景可以，让整轮对话崩不行。"""
    assert _render(None, boom=True) == ""


# --------------------------------------------------------------- 躁动

def test_躁动不进他的上下文():
    """`restlessness.py`：这个 Drive 的出口是**等**，不是说。

    塞进上下文等于催他开口，正好取消了它自己。
    """
    out = _render({
        "restlessness": _Drive(0.5, ["她在打游戏"]),
        "longing": _Drive(0.5, ["她没说话"]),
    })
    assert "躁动" not in out
    assert "她在打游戏" not in out
    assert "想她" in out


def test_不认识的_drive_跳过():
    """后端加了新 kind 而这边还没给措辞时，跳过而不是把 key 名字裸露给他。"""
    out = _render({"curiosity": _Drive(0.8, ["arXiv 那篇"]), "longing": _Drive(0.5)})
    assert "curiosity" not in out
    assert "想她" in out


# --------------------------------------------------------------- 缓存友好

def test_强度小幅变化时文本不变():
    """dynamic_system 每轮都变会把缓存命中率打下来。

    档位的意义就在这儿：0.40 和 0.45 该渲染成同一段话。
    """
    a = _render({"longing": _Drive(0.40, ["她没说话"])})
    b = _render({"longing": _Drive(0.45, ["她没说话"])})
    assert a == b


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
