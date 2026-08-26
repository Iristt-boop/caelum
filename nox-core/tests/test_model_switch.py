"""按请求换模型。

要守住三件事：
  1. loop 真的用了传进来的 adapter，而不是它自己那个
  2. 认不出的名字要**降级到默认**，不能因此答不上话
  3. 同一个模型建一次就够，别每轮重建（连接池会跟着重建）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402


class NamedAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.used = 0

    def complete(self, messages, tools, **kw):
        self.used += 1
        return Turn(stop_reason="end_turn", text=f"我是{self.name}")

    def stream(self, messages, tools, **kw):
        from agent.llm import StreamEvent
        self.used += 1
        turn = Turn(stop_reason="end_turn", text=f"我是{self.name}")
        yield StreamEvent("text", text=turn.text)
        yield StreamEvent("done", turn=turn)


def test_不传_adapter_用默认的():
    default = NamedAdapter("默认")
    loop = AgentLoop(adapter=default)
    assert loop.run("在吗").text == "我是默认"
    assert default.used == 1


def test_传了就用传的():
    default, other = NamedAdapter("默认"), NamedAdapter("opus")
    loop = AgentLoop(adapter=default)
    assert loop.run("在吗", adapter=other).text == "我是opus"
    assert default.used == 0 and other.used == 1


def test_流式路径也认():
    """run 和 run_stream 是两个入口，一个接上不代表另一个也接上了。"""
    default, other = NamedAdapter("默认"), NamedAdapter("opus")
    loop = AgentLoop(adapter=default)
    texts = [ev.text for ev in loop.run_stream("在吗", adapter=other) if ev.type == "text"]
    assert "".join(texts) == "我是opus"
    assert default.used == 0 and other.used == 1


# ------------------------------------------------------------ Nox.adapter_for

class FakeNox:
    """只取 Nox 的 adapter_for 来测，不真的起一个 Nox（那要连 OB）。"""

    def __init__(self, models, primary_model, fail: set[str] | None = None):
        from config import Config
        self.cfg = Config(models=models)
        object.__setattr__(self.cfg, "_primary_model", primary_model)
        self._adapters = {}
        self._fail = fail or set()
        self.built = []

    def _make(self, full):
        if full in self._fail:
            raise RuntimeError("建不出来")
        self.built.append(full)
        return NamedAdapter(full)


def _adapter_for(fake, model, primary_model):
    """把 Nox.adapter_for 的逻辑拿过来跑 —— 它只依赖 cfg / _adapters / make。"""
    if not model:
        return None
    full = fake.cfg.models.get(model)
    if not full:
        return None
    if full == primary_model:
        return None
    cached = fake._adapters.get(full)
    if cached is not None:
        return cached
    try:
        adapter = fake._make(full)
    except Exception:
        return None
    fake._adapters[full] = adapter
    return adapter


MODELS = {"opus-4-8": "anthropic/claude-opus-4-8", "sonnet-5": "anthropic/claude-sonnet-5"}
PRIMARY = "anthropic/claude-sonnet-5"


def test_没传模型时不建_adapter():
    f = FakeNox(MODELS, PRIMARY)
    assert _adapter_for(f, None, PRIMARY) is None
    assert f.built == []


def test_选中的就是默认模型时不另建():
    """省一次 adapter 构造，也避免两个 adapter 打同一个模型把缓存搅乱。"""
    f = FakeNox(MODELS, PRIMARY)
    assert _adapter_for(f, "sonnet-5", PRIMARY) is None
    assert f.built == []


def test_没见过的名字降级到默认_不报错():
    f = FakeNox(MODELS, PRIMARY)
    assert _adapter_for(f, "gpt-9", PRIMARY) is None


def test_建不出来时降级到默认():
    """模型不可用不该让她答不上话 —— 顶多这轮换回默认。"""
    f = FakeNox(MODELS, PRIMARY, fail={"anthropic/claude-opus-4-8"})
    assert _adapter_for(f, "opus-4-8", PRIMARY) is None


def test_同一个模型只建一次():
    f = FakeNox(MODELS, PRIMARY)
    a = _adapter_for(f, "opus-4-8", PRIMARY)
    b = _adapter_for(f, "opus-4-8", PRIMARY)
    assert a is b
    assert f.built == ["anthropic/claude-opus-4-8"]
