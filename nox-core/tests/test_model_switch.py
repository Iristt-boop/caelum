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


# ---------------------------------------------------------------- key 跟着 backend 走
#
# 2026-09-08 事故：把 NOX_UTILITY_BACKEND 切成 zhipu 之后，utility 连着
# **三十多个小时**都在 401（一天 200 次：压缩没在压、理解层没在推断、
# 话题池没在筛），而主聊天一切正常 —— 所以表面上完全看不出来。
#
# 病根是 config.py 里 utility 有一行 primary 没有的：
#     key_override=_env("DEEPSEEK_API_KEY") or _env("OPENROUTER_API_KEY")
# 它**无视 NOX_UTILITY_BACKEND，永远拿 DeepSeek 的 key**。


def _utility_of(monkeypatch, **env):
    """按给定环境变量算出 utility 的 LLMConfig。

    ⚠️ **不 reload config 模块**：`Config` 的字段是 `field(default_factory=lambda…)`，
    那些 lambda 在类定义时就闭包捕获了当时的 `_env`，reload 之后拿到的
    仍是旧的一份 —— 第一版测试就栽在这儿，症状是 key 对了但 base_url 不对。
    直接调 `_build_llm` 走同一条路，干净得多。
    """
    #: 🔴 先清掉可能盖过 backend 的两个 override —— 它们优先级最高。
    #: 本机 .env 里就躺着一行陈的 `NOX_UTILITY_BASE_URL=openrouter`，
    #: 第一版测试被它坑了半天：key 跟着 backend 走对了，地址却纹丝不动。
    #: 测试不能靠「这台机器碰巧干净」。
    for k in ("NOX_UTILITY_BASE_URL", "NOX_UTILITY_PROVIDER", "NOX_UTILITY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from config import _build_llm, _env
    return _build_llm(
        backend=_env("NOX_UTILITY_BACKEND", "deepseek"),
        model=_env("NOX_UTILITY_MODEL", "deepseek-v4-flash"),
        provider_override=_env("NOX_UTILITY_PROVIDER"),
        base_override=_env("NOX_UTILITY_BASE_URL"),
        key_override=_env("NOX_UTILITY_API_KEY"),
        max_tokens=4000,
    )


def test_utility_key_follows_its_backend(monkeypatch):
    """🔴 切了 backend，key 必须跟着切 —— 不许再硬塞某一家的。"""
    u = _utility_of(
        monkeypatch,
        NOX_UTILITY_BACKEND="zhipu", NOX_UTILITY_MODEL="glm-5.3-flash",
        ZHIPU_API_KEY="zhipu-key-xxx", DEEPSEEK_API_KEY="deepseek-key-yyy",
    )
    assert u.api_key == "zhipu-key-xxx", (
        "utility 拿着别家的 key 去打智谱 —— 这就是 09-08 那次 401 的原因"
    )
    assert "bigmodel" in u.base_url, "key 跟上了但地址没跟上，一样打不通"


def test_config_py_no_longer_hardcodes_a_vendor_key():
    """守住病根本身：utility 的 key_override 里不许再出现某一家的名字。

    原来那行是 `key_override=_env("DEEPSEEK_API_KEY") or _env("OPENROUTER_API_KEY")`，
    等于**无视 NOX_UTILITY_BACKEND**。而 primary 没有这一行 ——
    不一致正是这次事故三十多小时没被发现的原因：**主链路好好的**，
    只有那条没人看的 utility 在闷声 401。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "config.py").read_text(encoding="utf-8")
    block = src.split("utility: LLMConfig")[1].split("vision:")[0]
    #: 只看那一行**代码**，不看注释 —— 注释里会提到这几个名字（讲事故经过）
    line = next((ln for ln in block.splitlines()
                 if "key_override=" in ln and not ln.strip().startswith("#")), "")
    assert line, "utility 的 key_override 那行不见了"
    for vendor in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "ZHIPU_API_KEY"):
        assert vendor not in line, (
            f"utility 又把 {vendor} 写死了 —— key 该跟着 backend 走。\n"
            f"当前那行：{line.strip()}"
        )


def test_explicit_override_still_wins(monkeypatch):
    """要手动指定仍然可以 —— 但得显式写 NOX_UTILITY_API_KEY，不是偷偷来。"""
    u = _utility_of(
        monkeypatch,
        NOX_UTILITY_BACKEND="zhipu", NOX_UTILITY_MODEL="glm-5.3-flash",
        ZHIPU_API_KEY="zhipu-key-xxx", NOX_UTILITY_API_KEY="my-own-key",
    )
    assert u.api_key == "my-own-key"
