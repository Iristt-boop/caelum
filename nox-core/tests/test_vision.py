"""视觉路由测试：主模型读不了图时，谁替他看、看完怎么进历史。

守三件事：
  1. 能看图的模型别多此一举
  2. 看不了图 + 视觉模型可用 → 图变成文字，**images 必须清空**
     （不清空的话它会留在会话历史里，下一轮又被发出去 —— 2026-08-02 就是这么废掉一个会话的）
  3. 视觉模型不可用 → 保持原样交给 adapter 层如实说看不见，**绝不编一段描述**
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import vision  # noqa: E402
from config import LLMConfig  # noqa: E402
from nox import Nox  # noqa: E402

TINY = "iVBORw0KGgoAAAANSUhEUg=="


class _FakeAdapter:
    def __init__(self, model: str) -> None:
        self.cfg = SimpleNamespace(model=model)


def _nox(model: str = "deepseek-v4-flash", vision_key: str = "k") -> Nox:
    """绕开 __init__ 拼一个只够 _see 用的壳，不连网、不建 adapter。"""
    n = Nox.__new__(Nox)
    n.cfg = SimpleNamespace(
        vision=LLMConfig(
            provider="openai_compat", model="qwen3.5-flash",
            api_key=vision_key, base_url="https://example.invalid/v1",
        ),
        vision_timeout=5.0,
    )
    n.loop = SimpleNamespace(adapter=_FakeAdapter(model))
    n.adapter_for = lambda m: None          # type: ignore[method-assign]
    return n


def test_vision_capable_model_keeps_images():
    """Claude 自己就能看图，不该再多调一次视觉模型。"""
    n = _nox("anthropic/claude-sonnet-5")
    text, imgs = n._see("这是什么", [TINY], None)
    assert text == "这是什么"
    assert imgs == [TINY]


def test_blind_model_converts_images_to_text(monkeypatch):
    monkeypatch.setattr(vision, "describe", lambda *a, **k: "一只黑猫和一只白兔坐在星空下")

    n = _nox("deepseek-v4-flash")
    text, imgs = n._see("你看这个", [TINY], None)

    assert imgs is None                      # ← 最要紧：图不能进历史
    assert "你看这个" in text
    assert "一只黑猫和一只白兔坐在星空下" in text
    assert "转述" in text                     # 得让他知道不是自己看的


def test_blind_model_without_vision_stays_honest(monkeypatch):
    """视觉模型挂了就老实说看不见，绝不编一段描述出来。"""
    monkeypatch.setattr(vision, "describe", lambda *a, **k: None)

    n = _nox("deepseek-v4-flash")
    text, imgs = n._see("你看这个", [TINY], None)

    assert text == "你看这个"                 # 没有凭空多出来的描述
    assert imgs == [TINY]                    # 交给 adapter 层降级成「我看不见」


def test_no_images_is_untouched():
    n = _nox("deepseek-v4-flash")
    assert n._see("纯文字", None, None) == ("纯文字", None)


def test_describe_without_key_returns_none():
    """没配 DASHSCOPE_API_KEY 时直接放弃，不该抛异常。"""
    cfg = LLMConfig(provider="openai_compat", model="qwen3.5-flash", api_key="", base_url="x")
    assert vision.describe([TINY], cfg) is None


def test_wrap_marks_it_as_secondhand():
    out = vision.wrap("图里是一碗面", 1)
    assert "1 张图片" in out
    assert "转述" in out
    assert "图里是一碗面" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
