# -*- coding: utf-8 -*-
"""眼睛那层的测试。**不打网络、不调 ffmpeg。**

这里验的几乎全是**账本**，因为 2026-09-04 起共影在跑「按量付费一个月
到底花多少」的实测 —— 账记错了整件事就白做了。

最要紧的一条：**算不出钱的时候必须留下痕迹，不许当 0。**
「这个月花了 0 块」和「这个月的账全没记上」在汇总里长得一模一样，
而后者要到月底对账时才会暴露。

跑：`python -m pytest test_vision.py -q`
"""
from __future__ import annotations

import json
import sys

import pytest

import vision as v


# --------------------------------------------------------------- 算钱

def test_按公布价把_token_折成钱():
    # Flash-Lite: 入 $0.30/M、出 $2.50/M
    cost, why = v._cost("gemini-3.5-flash-lite",
                        {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert why == ""
    assert cost == pytest.approx(2.80)


def test_一次真实调用大约一厘钱():
    """8 秒片段约 800 入 + 300 出 —— 这个量级得对，不然预算全是错的。"""
    cost, why = v._cost("gemini-3.5-flash-lite",
                        {"input_tokens": 800, "output_tokens": 300})
    assert why == ""
    assert 0.0005 < cost < 0.002


@pytest.mark.parametrize("usage", [
    {"prompt_token_count": 800, "candidates_token_count": 300},
    {"promptTokenCount": 800, "candidatesTokenCount": 300},
    {"inputTokens": 800, "outputTokens": 300},
])
def test_几种字段写法都要认得(usage):
    """Interactions API 的 usage 字段名没实测过，认错就静默记成 0。"""
    cost, why = v._cost("gemini-3.5-flash-lite", usage)
    assert why == ""
    assert cost > 0


def test_思考和工具的_token_也要算钱():
    """agentic 模式下这两项是大头，漏了就系统性低估。"""
    base, _ = v._cost("gemini-3.5-flash-lite",
                      {"input_tokens": 800, "output_tokens": 300})
    more, _ = v._cost("gemini-3.5-flash-lite",
                      {"input_tokens": 800, "output_tokens": 300,
                       "total_thought_tokens": 500})
    assert more > base


# --------------------------------------------------------------- 算不出来的时候

@pytest.mark.parametrize("model,usage,keyword", [
    ("gemini-3.5-flash-lite", {}, "usage"),
    ("gemini-3.5-flash-lite", {"unknown_key": 5}, "没认出"),
    ("no-such-model", {"input_tokens": 800}, "PRICING"),
])
def test_算不出钱要返回_None_并说明原因(model, usage, keyword):
    """⚠️ 这条是整个文件里最重要的：**不许悄悄返回 0。**"""
    cost, why = v._cost(model, usage)
    assert cost is None
    assert keyword in why


# --------------------------------------------------------------- 账本

@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "usage.jsonl"
    monkeypatch.setattr(v, "LEDGER", str(p))
    return p


def test_写一行读回来(ledger):
    v._log("clip", "gemini-3.5-flash-lite",
           {"input_tokens": 800, "output_tokens": 300}, 0.001, "", 1.5)
    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["kind"] == "clip"
    # usage 原文照存 —— 以后想按新价重算，或者发现字段名认错了，还能回溯
    assert row["usage"] == {"input_tokens": 800, "output_tokens": 300}
    assert row["latency_s"] == 1.5


def test_汇总把钱加起来(ledger):
    for _ in range(3):
        v._log("clip", "gemini-3.5-flash-lite", {"input_tokens": 800}, 0.001, "", 1.0)
    s = v.summary()
    assert s["calls"] == 3
    assert s["cost_usd"] == pytest.approx(0.003)
    assert s["unpriced"] == 0
    assert s["by_kind"] == {"clip": 3}


def test_没算出钱的次数要单独报出来(ledger):
    """总额偏低的时候必须看得见，否则她会以为那就是全部开销。"""
    v._log("clip", "gemini-3.5-flash-lite", {"input_tokens": 800}, 0.001, "", 1.0)
    v._log("clip", "gemini-3.5-flash-lite", {}, None, "这次没拿到 usage", 1.0)
    s = v.summary()
    assert s["calls"] == 2
    assert s["unpriced"] == 1
    assert "1 次没算出钱" in s["note"]


def test_按月筛(ledger):
    ledger.write_text(
        json.dumps({"at": "2026-08-31T23:00:00+0800", "kind": "clip",
                    "cost_usd": 1.0}, ensure_ascii=False) + "\n" +
        json.dumps({"at": "2026-09-01T00:30:00+0800", "kind": "clip",
                    "cost_usd": 2.0}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    assert v.summary("2026-09")["cost_usd"] == pytest.approx(2.0)
    assert v.summary()["cost_usd"] == pytest.approx(3.0)


def test_账本还没建过也别炸(ledger):
    s = v.summary()
    assert s["calls"] == 0 and s["cost_usd"] == 0.0


def test_账本里有坏行也要能汇总(ledger):
    """半行 JSON（进程被 kill 在写一半）不该让整个月的账读不出来。"""
    v._log("clip", "gemini-3.5-flash-lite", {"input_tokens": 800}, 0.001, "", 1.0)
    with open(ledger, "a", encoding="utf-8") as f:
        f.write('{"at": "2026-09-01", "cost_u\n')
    assert v.summary()["calls"] == 1


def test_写账本失败不许影响看片(monkeypatch):
    monkeypatch.setattr(v, "LEDGER", "/nonexistent\x00/bad/usage.jsonl")
    v._log("clip", "m", {}, None, "", 1.0)   # 不抛就算过


# --------------------------------------------------------------- 没配 key

@pytest.mark.parametrize("provider,expected", [
    ("openrouter", "OPENROUTER_API_KEY"),
    ("google", "GEMINI_API_KEY"),
])
def test_没配_key_时说得出是哪把(monkeypatch, ledger, provider, expected):
    """空描述必须带着原因回去 —— 他得知道自己是瞎的，不能装作看见了。

    而且要说清**缺的是哪一把** key，两家的名字不一样。
    """
    monkeypatch.setattr(v, "PROVIDER", provider)
    monkeypatch.setattr(v, "key", lambda: "")
    desc, note = v._ask([{"type": "text", "text": "x"}], "clip")
    assert desc is None
    assert expected in note


# --------------------------------------------------------------- 两家的差异

def test_视频块翻成_openrouter_的形状():
    """Google 叫 video/data，OpenAI 那套叫 video_url/data URL。翻错就 400。"""
    out = v._or_part({"type": "video", "data": "QUJD", "mime_type": "video/mp4"})
    assert out["type"] == "video_url"
    assert out["video_url"]["url"] == "data:video/mp4;base64,QUJD"


def test_图片块翻成_openrouter_的形状():
    out = v._or_part({"type": "image", "data": "QUJD", "mime_type": "image/jpeg"})
    assert out["type"] == "image_url"
    assert out["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("payload,want", [
    # OpenRouter / OpenAI
    ({"choices": [{"message": {"content": "看见了"}}]}, "看见了"),
    # 内容块数组
    ({"choices": [{"message": {"content": [{"text": "看见了"}]}}]}, "看见了"),
    # Google Interactions
    ({"output_text": "看见了"}, "看见了"),
    ({"steps": [{"content": [{"text": "看见了"}]}]}, "看见了"),
])
def test_三种回包形状都要抠得出正文(payload, want):
    assert v._text_of(payload) == want


def test_抠不出正文就返回空串():
    """认不出来必须让上层报「他没说出话来」，不能装成正常结果。"""
    assert v._text_of({"weird": 1}) == ""


def test_有真实扣费就用真实的不用估算():
    """OpenRouter 每次回 `cost` —— 那是真扣的钱，比价目表推的准。"""
    cost, why = v._cost("google/gemini-3.5-flash-lite",
                        {"prompt_tokens": 800, "completion_tokens": 300,
                         "cost": 0.00123})
    assert why == ""
    assert cost == pytest.approx(0.00123)


def test_没有真实扣费时退回价目表估算():
    """直连 Google 没有 cost 字段，得自己算 —— 带厂商前缀也要认得出型号。"""
    cost, why = v._cost("google/gemini-3.5-flash-lite",
                        {"prompt_tokens": 800, "completion_tokens": 300})
    assert why == ""
    assert 0.0005 < cost < 0.002


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
