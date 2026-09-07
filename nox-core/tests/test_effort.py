"""思考深度的方言层（2026-09-06）。

糖糖：「换模型应该使用应该是一件无感的事情，**不能换一次就要修改一次**。」

这个文件守的就是那句话：**调用方只说中性词，方言全在 effort.py 里**。

## 🔴 实测数据（这些数字是这张表存在的理由）

| | 默认（不传） | `none` | `low` |
|---|---|---|---|
| `deepseek-v4-flash` | reasoning **54%** | **0** ✅ 正文反而更长 | reasoning 更多 |
| `glm-5.3-flash` | reasoning **86-94%** | **拒绝**（始终思考）| **0** ✅ |

**`low` 在两家的含义是反的。** 把这种事泄露给调用方就是「换一次改一次」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import effort  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    effort.reset()
    yield
    effort.reset()


# ---------------------------------------------------------------- 方言翻译


def test_deepseek_hears_none():
    """DeepSeek 的「别想」是 `none` —— 实测 reasoning 归零、正文反而更长。"""
    assert effort.kwargs_for("deepseek-v4-flash", "none") == {"reasoning_effort": "none"}


def test_glm_hears_low_for_none():
    """🔴 GLM **关不掉**思考（原话：「该模型始终思考」），
    所以我们的「别想」只能落到它最省的那档 —— 实测那档 reasoning 真的是 0。"""
    assert effort.kwargs_for("glm-5.3-flash", "none") == {"reasoning_effort": "low"}


def test_low_means_opposite_things():
    """**这条是整个模块存在的理由。**

    同一个中性词 `none`，DeepSeek 收到 "none"、GLM 收到 "low"。
    要是让调用方自己传 `reasoning_effort`，换一次模型就要改一次调用点。
    """
    ds = effort.kwargs_for("deepseek-v4-flash", "none")
    glm = effort.kwargs_for("glm-5.3-flash", "none")
    assert ds != glm
    assert ds["reasoning_effort"] == "none"
    assert glm["reasoning_effort"] == "low"


def test_deepseek_low_is_not_sent():
    """DeepSeek 的 `low` 实测**比默认还慢**（reasoning 178 vs 69），
    所以「稍微想想」在它这儿没有更好的说法 —— 不发，用它自己的默认。"""
    assert effort.kwargs_for("deepseek-v4-flash", "low") == {}


def test_unknown_model_sends_nothing():
    """🔴 不认识的模型 → **什么都不发**（安全默认：行为等于加这层之前）。

    发错的表现是 HTTP 400 整轮挂掉；不发顶多慢一点贵一点。
    """
    assert effort.kwargs_for("some-new-model-2027", "none") == {}
    assert effort.kwargs_for("", "none") == {}


def test_unknown_depth_is_refused_loudly():
    """调用方说了词表外的词 —— 多半是有人把方言直接传进来了。"""
    assert effort.kwargs_for("glm-5.3-flash", "minimal") == {}
    assert effort.kwargs_for("glm-5.3-flash", "reasoning_effort") == {}


def test_no_depth_no_param():
    assert effort.kwargs_for("glm-5.3-flash", None) == {}


def test_vision_model_matched_by_prefix():
    """型号名带后缀（`-vision-exp`）也要认出来 —— 按前缀匹配的理由。"""
    got = effort.kwargs_for("deepseek-v4-flash-vision-exp", "none")
    assert got == {"reasoning_effort": "none"}


# ---------------------------------------------------------------- 被拒之后


def test_rejection_is_remembered():
    """🔴 记下来，别每轮都试一遍。

    `vision.py` 原来是每次都先试带参数、失败再重来 ——
    那等于模型不认这个参数时**每一轮都白打一次请求**。
    """
    m = "glm-5.3-flash"
    assert effort.kwargs_for(m, "none")          # 一开始会发
    effort.note_rejected(m, RuntimeError("400 invalid parameter reasoning_effort"))
    assert effort.kwargs_for(m, "none") == {}    # 之后不再发
    assert m in effort.rejected()


@pytest.mark.parametrize("err", [
    "Connection timeout",
    "429 Too Many Requests",
    "503 Service Unavailable",
    "read timed out",
])
def test_transient_errors_are_not_remembered(err):
    """⚠️ 超时、限流、5xx **不能**记成「不支持」。

    记了的话，一次网络抽风会让我们**永久**退回慢的那条路，
    而且没有任何人会发现 —— 只会觉得「怎么最近变慢了」。
    """
    m = "deepseek-v4-flash"
    effort.note_rejected(m, RuntimeError(err))
    assert m not in effort.rejected()
    assert effort.kwargs_for(m, "none") == {"reasoning_effort": "none"}


# ---------------------------------------------------------------- 词表本身


def test_neutral_vocabulary_has_no_dialect():
    """🔴 中性词里不许出现任何一家的说法。

    `agent/llm.py` 开头那条纪律：loop 不该知道 temperature / thinking /
    effort / budget_tokens 这些词的存在。
    """
    for w in effort.DEPTHS:
        assert w in ("none", "low", "high"), w
    for banned in ("minimal", "adaptive", "disabled", "budget"):
        assert banned not in effort.DEPTHS


def test_adding_a_vendor_is_one_line():
    """加一家新厂商 = 往 `_DIALECT` 加一行，adapter 和调用方都不用动。

    这条测试盯的是**结构**：方言表是数据不是代码分支。
    哪天有人开始在 adapter 里写 `if "glm" in model:`，这条就该红。
    """
    import inspect

    from agent import adapters
    src = inspect.getsource(adapters)
    for vendor in ("glm", "zhipu", "bigmodel"):
        assert vendor not in src.lower(), f"adapter 里出现了厂商特判「{vendor}」"
