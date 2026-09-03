"""图片（多模态）测试。

两家的格式差得比文本大，而且方向相反 —— 一个要拆开、一个要拼起来。
这里就是守着这个差异不被改回去。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.adapters import AnthropicAdapter, OpenAICompatAdapter, supports_vision  # noqa: E402
from agent.llm import Message, split_data_uri  # noqa: E402
from config import LLMConfig  # noqa: E402
from router.intent import Intent, classify  # noqa: E402

TINY = "iVBORw0KGgoAAAANSUhEUg=="


def _oa(model: str = "gpt-4o"):
    """OpenAI 兼容 adapter 的空壳，只用来调 _to_native。

    `_to_native` 不再是 staticmethod —— 它要看 cfg.model 才知道这个模型能不能读图。
    """
    a = OpenAICompatAdapter.__new__(OpenAICompatAdapter)
    a.cfg = LLMConfig(provider="openai_compat", model=model, base_url="", api_key="")
    return a


# ------------------------------------------------------------ data URI 拆分

def test_split_data_uri():
    media, data = split_data_uri("data:image/png;base64,ABC123")
    assert media == "image/png" and data == "ABC123"


def test_bare_base64_defaults_to_jpeg():
    """手机拍的照片绝大多数是 jpeg，猜错只会看不清，不会报错。"""
    media, data = split_data_uri("ABC123")
    assert media == "image/jpeg" and data == "ABC123"


def test_split_handles_extra_params():
    media, _ = split_data_uri("data:image/webp;charset=utf-8;base64,XX")
    assert media == "image/webp"


# ------------------------------------------------------------ 两家格式

def test_anthropic_splits_media_and_data():
    m = Message(role="user", text="这是什么", images=[f"data:image/png;base64,{TINY}"])
    native = AnthropicAdapter._to_native(m)

    blocks = native["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == TINY
    # 图片在前、文本在后 —— 官方建议，模型先看图再读问题
    assert blocks[1]["type"] == "text"


def test_openai_joins_into_data_uri():
    """这一侧要拼成完整 data URI，跟 Anthropic 正好相反。"""
    m = Message(role="user", text="这是什么", images=[TINY])
    native = _oa()._to_native(m)[0]

    parts = native["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"] == f"data:image/jpeg;base64,{TINY}"
    assert parts[1]["type"] == "text"


def test_multiple_images():
    m = Message(role="user", text="比一比", images=[TINY, TINY, TINY])
    assert len([b for b in AnthropicAdapter._to_native(m)["content"] if b["type"] == "image"]) == 3


def test_no_images_stays_plain():
    """没图片时不该被包成数组 —— 那会平白多一层，也影响缓存前缀。"""
    m = Message(role="user", text="纯文本")
    assert AnthropicAdapter._to_native(m)["content"] == "纯文本"
    assert _oa()._to_native(m)[0]["content"] == "纯文本"


# --------------------------------------------- 模型读不了图时要降级，不能整段炸掉

def test_deepseek_has_no_vision():
    """实测原话：unknown variant `image_url`, expected `text`。两个常规型号都一样。"""
    assert not supports_vision("deepseek-v4-flash")
    assert not supports_vision("deepseek-v4-pro")
    # 视觉版例外：describe 从 2026-08-21 起就在生产里给它发 image_url
    assert supports_vision("deepseek-v4-flash-vision-exp")
    assert supports_vision("anthropic/claude-sonnet-5")
    assert supports_vision("gpt-4o")


def test_no_vision_model_degrades_image_to_text():
    """看不了图的模型必须收到纯字符串，不能收到 image_url —— 那会被拒成 400。"""
    m = Message(role="user", text="看看这个", images=[TINY])
    native = _oa("deepseek-v4-flash")._to_native(m)[0]

    assert isinstance(native["content"], str)      # 不是 parts 数组
    assert "image_url" not in native["content"]
    assert "看看这个" in native["content"]
    assert "看不了图" in native["content"]          # 如实说，不许假装看见了


def test_no_vision_degrades_history_images_too():
    """这条是这次故障的核心：历史里的图不降级，会话就永久废了。

    2026-08-02 糖糖发完 logo，**下一句纯文字照样 400** ——
    因为那张图还留在历史里，每一轮都被重新发出去、每一轮都被拒。
    """
    history = [
        Message(role="user", text="我做了我们的logo", images=[TINY]),   # 上一轮的图
        Message(role="assistant", text="嗯"),
        Message(role="user", text="你觉得怎么样"),                      # 这一轮没图
    ]
    a = _oa("deepseek-v4-flash")
    native = [x for m in history for x in a._to_native(m)]

    # 整段历史里不能有任何 parts 数组，否则 DeepSeek 照样 400
    assert all(isinstance(n["content"], str) for n in native)


def test_image_without_text():
    """只发图不说话也要能处理。"""
    m = Message(role="user", images=[TINY])
    blocks = AnthropicAdapter._to_native(m)["content"]
    assert len(blocks) == 1 and blocks[0]["type"] == "image"


# ------------------------------------------------------------ 路由

def test_images_force_full_path():
    """带图必须走完整路径 —— 轻量路径没工具也没核心准则，
    而发照片多半是要他做点什么（算热量、看看这是啥）。"""
    d = classify("在吗", has_images=True)
    assert d.intent is Intent.FULL
    assert "图片" in d.reason


def test_same_text_without_images_stays_light():
    assert classify("在吗", has_images=False).intent is Intent.SMALL_TALK


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
