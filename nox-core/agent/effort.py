"""思考深度：我们说一句话，各家听懂各家的。

## 为什么要有这一层

糖糖 2026-09-06：「换模型应该使用应该是一件无感的事情，**不能换一次就要修改一次**。」

在这之前我们有的不是机制，是两处各自为政的特例：

    agent/vision.py:93   给视觉模型写死 `reasoning_effort="none"`，带 try/except 兜底
    agent/adapters.py    openai_compat 那一侧**直接把 depth 丢掉**
                         （注释写「这一侧没有 effort 概念」）

那条注释对 DeepSeek 成立，但它被当成了「所有 OpenAI 兼容的都没有」。

## 🔴 实测：两家的方言是反的（2026-09-06）

| | 默认（不传） | `none` | `low` |
|---|---|---|---|
| `deepseek-v4-flash` | reasoning **54%** | **0** ✅ 正文反而更长 | reasoning 更多（比默认还慢）|
| `glm-5.3-flash` | reasoning **86-94%** | **拒绝**（该模型始终思考）| **0** ✅ |

两个结论：

1. **`low` 在两家的含义是反的** —— DeepSeek 的 low 比默认更能想，GLM 的 low 等于不想
2. **我们主聊天路径一直在烧 54% 的输出 token 想事情**，只有共影那条路关掉了

所以「关掉思考」这句话，DeepSeek 听 `none`，GLM 听 `low`。
把这种事泄露给调用方，就是她说的「换一次改一次」。

## 怎么加一个新厂商

往 `_DIALECT` 里加一行。**不用动 adapter，不用动任何调用方。**
表里没有的模型 → **不发这个参数**（安全默认：行为和今天完全一样）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: 我们的中性词。**调用方只准说这几个**，不许说 `reasoning_effort`、
#: 不许说 `thinking` —— 那些是方言（`agent/llm.py` 开头那条纪律）。
#:
#:   none    别想，直接说。聊天要的是语气，不是解题
#:   low     稍微想想
#:   high    仔细想
#:
#: 故意不做 `medium` —— 三档已经够表达，而且中间档在两家的实测差别都不明显，
#: 多一档只会让方言表更难对齐。
DEPTHS = ("none", "low", "high")

#: 方言表：**模型名前缀** → (参数名, {我们的词: 它的取值})
#:
#: ⚠️ 按前缀匹配而不是全名，因为型号名会带日期后缀、会小改版
#: （`glm-5.3-flash` / `deepseek-v4-flash-vision-exp`）。
#: 长前缀优先 —— 视觉版和聊天版可能不是同一套脾气。
#:
#: 值里**没有的档就是不发这个参数**（用它自己的默认）。
#: 例如 DeepSeek 的 low/high 都不发：实测它的 `low` 比默认还慢，
#: 而它的默认本来就在思考，所以「想多点」= 什么都不传。
_DIALECT: dict[str, tuple[str, dict[str, str]]] = {
    # DeepSeek：只有「关掉」这一档说得通
    "deepseek": ("reasoning_effort", {"none": "none"}),
    # 智谱 GLM 5 系：**关不掉**，只能调档
    # （原话：「该模型始终思考，不支持关闭思考，请使用 low、high 或 max」）
    # 所以我们的 none 只能落到它最省的 low —— 实测那一档 reasoning 真的是 0
    "glm-5": ("reasoning_effort", {"none": "low", "low": "low", "high": "high"}),
}

#: 被拒绝过的模型。**记下来，别每轮都试一遍。**
#:
#: `vision.py` 原来的写法是每次都先试带参数、失败再重来 ——
#: 那等于模型不认这个参数时**每一轮都白打一次请求**。
_rejected: set[str] = set()
_lock = threading.Lock()


def _dialect(model: str) -> tuple[str, dict[str, str]] | None:
    """这个模型说哪种方言。不认识就 None。"""
    m = (model or "").lower()
    #: 长前缀优先：`deepseek-v4-flash-vision-exp` 该匹配更具体的那条（如果有）
    for prefix in sorted(_DIALECT, key=len, reverse=True):
        if prefix in m:
            return _DIALECT[prefix]
    return None


def kwargs_for(model: str, depth: str | None) -> dict[str, Any]:
    """把我们的 depth 翻成这个模型认识的话。

    翻不出来就返回 `{}` —— **不发总比发错好**：
    发错的表现是 HTTP 400，整轮对话挂掉；不发的表现是它用自己的默认，
    顶多慢一点贵一点。
    """
    if not depth or not model:
        return {}
    if depth not in DEPTHS:
        #: 调用方说了我们词表里没有的词。要吵出来 —— 这多半是有人
        #: 把方言（"minimal" / "none_"）直接传进来了
        logger.warning("不认识的 depth=%r（只认 %s），这轮不发", depth, DEPTHS)
        return {}
    with _lock:
        if model in _rejected:
            return {}
    d = _dialect(model)
    if d is None:
        return {}
    param, table = d
    value = table.get(depth)
    return {param: value} if value else {}


def note_rejected(model: str, exc: BaseException) -> None:
    """这个模型不认这个参数 —— 记下来，以后不再发。

    只在**看起来像参数问题**时才记（400 / invalid / unsupported）。
    网络抖动、超时、限流都不能记 —— 那样一次抽风会让我们永久
    退回慢的那条路，而且没人会发现。
    """
    msg = str(exc).lower()
    looks_like_param = any(
        k in msg for k in ("400", "invalid", "unsupported", "unrecognized",
                           "not support", "unexpected keyword")
    )
    if not looks_like_param:
        return
    with _lock:
        if model in _rejected:
            return
        _rejected.add(model)
    logger.warning(
        "%s 不接受思考深度参数，之后不再发（退回它的默认，可能更慢更贵）：%.120s",
        model, exc,
    )


def rejected() -> set[str]:
    """给测试和 /api/nox/state 看的。"""
    with _lock:
        return set(_rejected)


def reset() -> None:
    """只给测试用。"""
    with _lock:
        _rejected.clear()
