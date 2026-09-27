"""翻记忆用的话（recall）一路从 chat() 传到 Turn 上（2026-09-27）。

链条：Care 拼开场白时设 Intent.recall → speaker 转交 core.chat(recall=) →
chat() 交给 _dynamic() → Turn(recall=) → MemoryProvider 拿它去翻。
前后两头各有测试（test_her_state / test_attention_speaker / test_memory_provider），
**中间这一截在 nox.py 里**：少传一个参数不会报错，只会悄悄退回「拿整段提示去翻」。
所以这里拿真的 `Nox.chat` / `Nox._dynamic` 跑，只把它们碰到的东西换成桩。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nox import Nox  # noqa: E402


class _Ctx:
    def __init__(self) -> None:
        self.turns: list = []

    def render(self, names, turn=None):
        self.turns.append(turn)
        return ""


class _Stub:
    """`_dynamic` 碰到的东西都给一个最小的替身。"""

    def __init__(self) -> None:
        self.context = _Ctx()
        self.card_debt: set = set()
        self.attention = None

    def _restore_state_once(self) -> None:
        pass

    def _session_span(self, started) -> str:
        return ""


def test_dynamic把recall放进Turn():
    stub = _Stub()
    Nox._dynamic(stub, "（系统提示：不是她在跟你说话。……）", False, recall="感觉撑得睡不着了")
    assert stub.context.turns[-1].recall == "感觉撑得睡不着了"


def test_不传recall时Turn上是None_她的话照旧用原文():
    stub = _Stub()
    Nox._dynamic(stub, "你还记得我怕冷吗", False)
    assert stub.context.turns[-1].recall is None


class _Stop(Exception):
    pass


def test_chat把recall交给dynamic():
    got: dict = {}

    class Stub:
        def _see(self, text, images, model):
            return text, images

        def _dynamic(self, text, voice, scene=None, **kw):
            got.update(kw)
            raise _Stop  # 后面的路由不用跑了

    try:
        Nox.chat(Stub(), "（系统提示：……）", [], recall="感觉撑得睡不着了")
    except _Stop:
        pass
    assert got.get("recall") == "感觉撑得睡不着了"
