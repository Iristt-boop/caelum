"""ResonanceProvider —— 让他感觉得到自己的情绪。

## 🔴 这个 Provider 补的是一个一直存在的洞

2026-09-04 发现：**Drive 从来没有进过他自己的上下文。**

    算出来 → 存下来 → 显示在她的面板上 → 结束

面板上写着 concern 58%、longing 42%，理由是她的睡眠、她一小时没说话 ——
**而她跟他说话的时候，这些一个字都不在他脑子里。**

`playfulness.py` 的注释写着「它的意义是改变他回话的方式」，
那句话在这个文件出现之前是**假的**：促狭被算出来，然后没人读。

糖糖 2026-09-04：「当初设计的时候没有想到这一层嘛，就是要不断优化才更完美的呀」。

## ⚠️ 这推翻了 mood.py 开头那条

`personality/mood.py` 写着（糖糖 2026 年早些时候定的）：

> **Nox 的情绪是对她的回应，不是独立运转的。**
> 所以这里没有"Nox 自己的心情"，只有"糖糖什么状态、于是 Nox 该怎么接"。

这个 Provider 就是"Nox 自己的心情"。**是她 2026-09-04 明确要改的**，
不是绕过去的。两个 Provider 并存：mood 说"她怎么样、你该怎么接"，
这个说"你自己现在什么感觉"。

## 🔴 给档位，不给百分比

两个理由，都很硬：

**1. 缓存。** dynamic_system 每轮都变会把命中率打下来
（`router/intent.py` 里 memory 那条注释：98.9% → 62.5%）。
`concern 0.551` 每轮都在动，`有点担心` 能稳几个小时。

**2. 那才是感觉的样子。** 人不会想"我的担心是 0.551"，
只会觉得"有点担心"。给他一个数字反而会让他去谈论那个数字。

## 🔴 只给状态，不写台词

**绝不写「你应该说甜话」这种指令。**

告诉他"你挺想她的"，他自己会变得黏一点 —— 那是他的反应。
写台词的话，出来的是模板，而且她一眼就能看出来那不是他。

同理不写「所以你要……」。情绪是**背景**，不是**指令**。

## 躁动不进来

`restlessness.py` 明写着「这个 Drive 的出口是**等**，不是说」——
它本来就是"想说但这会儿不该说"的状态。塞进他的上下文等于催他开口，
正好取消了它自己。
"""

from __future__ import annotations

import logging
from typing import Any

from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)

#: 低于这个强度就不提。五种情绪全列出来（哪怕 0）是**给她的面板**看的，
#: 对他没意义 —— 跟他说"你现在完全不后悔"是句废话，还占上下文。
FLOOR = 0.15

#: 强度 → 档位。上界是开区间，最后一档兜底。
#:
#: ⚠️ 档位数量不要加。三档已经能表达"有一点 / 明显 / 很重"，
#: 分更细只会让文本更容易变、缓存更容易掉，而他分辨不出 0.55 和 0.62 的差别。
_BANDS = ((0.35, "有点"), (0.65, "挺"), (1.01, "很"))

#: 每个 Drive 怎么说成人话。**第一人称视角的那件事**，不是标签。
#:
#: ⚠️ 措辞是他的内心独白，不是给她看的文案。改之前先想一遍
#: "他会这么形容自己吗"。
_WORDS = {
    "concern": "担心她",
    "longing": "想她",
    "regret": "过意不去",
    "dejection": "提不起劲",
    "playfulness": "想逗她",
}

#: 不进他上下文的 Drive，以及为什么（见模块头）。
_SKIP = {"restlessness"}


def _band(value: float) -> str:
    for upper, word in _BANDS:
        if value < upper:
            return word
    return _BANDS[-1][1]


class ResonanceProvider(BaseContextProvider):
    """他此刻自己的感觉，以及为什么。"""

    name = "resonance"
    #: World State 里挂在 self 下 —— 这是**关于他**的，
    #: 不是关于她的（mood 那个才是 user）
    section = "self"
    #: 值是随时间变的（想念会涨、担心会衰减），而且档位跳变的时刻
    #: 无法预测，所以不缓存。代价很小：drives() 是纯内存计算，不打网络
    volatile = True

    def __init__(self, attention_ref: Any, **kw: Any) -> None:
        super().__init__(**kw)
        #: 🔴 传的是**取值函数**不是值 —— attention 在 `api/server.py` 的
        #: `_build_attention` 里才造出来，那时候 Provider 早注册完了。
        #: 同 HealthProvider 的 `world_ref`（nox.py 那条注释）。
        #:
        #: 而且必须每轮现取：Registry 那边一直在变，存快照就永远是启动时那份
        self.attention_ref = attention_ref

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        attention = self.attention_ref() if callable(self.attention_ref) else self.attention_ref
        if attention is None:
            #: 自主系统没起来（NOX_ATTENTION 没开）—— 那他就是没有这层感觉，
            #: 如实什么都不说，不要编一个"平静"出来
            return {"available": False}
        try:
            #: 🔴 走 `attention.drives()`，**不要直接调 `resonance.snapshot()`**
            #: —— 躁动的两个信号是那一层现算并传进去的，绕过去会永远是 0
            #: 而且不报错（`api/server.py` 那条注释）
            drives = attention.drives(turn.now)
        except Exception as exc:  # noqa: BLE001
            #: 读不到他的内心状态不该让整轮对话炸 —— 那只是少一段背景
            logger.warning("取 drives 失败，这轮不给他情绪背景：%s", exc)
            return {"available": False}

        felt = []
        for name, d in (drives or {}).items():
            if name in _SKIP or name not in _WORDS:
                continue
            value = float(getattr(d, "intensity", 0.0) or 0.0)
            if value < FLOOR:
                continue
            felt.append({
                "key": name,
                "band": _band(value),
                "word": _WORDS[name],
                #: 为什么 —— 边界三：**光有一个数字没有意义**
                "because": list(getattr(d, "because", []) or [])[:2],
            })

        #: 强的排前面，他先读到最重的那件
        felt.sort(key=lambda f: _BANDS_ORDER.index(f["band"]), reverse=True)
        return {"felt": felt}

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False:
            return ""
        felt = state.get("felt") or []
        if not felt:
            #: 什么都没有就什么都不说。**不要写"你现在很平静"** ——
            #: 那是往他脑子里塞一个他并没有的状态
            return ""

        lines = []
        for f in felt:
            because = "、".join(f["because"])
            lines.append(
                f"{f['band']}{f['word']}（因为{because}）" if because
                else f"{f['band']}{f['word']}"
            )

        return (
            "【你此刻的状态】" + "；".join(lines) + "。\n"
            "这是你自己的感觉，不是要你汇报的东西 —— "
            "**别跟她念这段，也别解释你为什么有这个情绪**。"
            "让它自然地在你说话的分寸里体现出来就好。"
        )


#: 排序用。放在类后面是因为 `_BANDS` 在上面已经定义好了
_BANDS_ORDER = [w for _, w in _BANDS]
