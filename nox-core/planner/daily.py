"""Daily Planner —— 把「今天」聚合成一份简报。

## 它和每轮对话的 Context 不是一回事

`classify_context()` 靠关键词猜这轮要加载谁，为的是**省**：闲聊只拉
time+mood（都是本地计算），提到家电才打 ha-mcp。那条路一天走几百次，
每多拉一个 Provider 都要按未命中价付费。

早报反过来 —— **一天只跑一两次，所以不猜，全都要**。
她说「早安」的时候，不该因为这两个字里没有「睡」字，
他就不知道她昨晚只睡了五小时。

## 为什么默认不带 memory

MemoryProvider 一次检索约 7 秒（nox.py:175 那段注释）。早报要的是
「今天什么情况」，不是「我们上周聊过什么」。真需要回忆，模型手上有
`recall_memory` 工具，它自己会调。

想要就传 `include_memory=True` —— 定时推送那条路可以开，
因为没人在等着看回复。

## 拿旧数据也要说出来

`available: False` 和 `stale` 都会原样带进 `DailyBrief`，
渲染文本里也会标。他可以说「我这儿的天气是半小时前的」，
但不能把半小时前的天气当现在的说给她听（base.py 第三条硬约束）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from context.base import Turn
from context.registry import ContextProviderRegistry

logger = logging.getLogger(__name__)

#: 早报要看的东西，按重要性排 —— 超预算时截断从后往前。
#:
#: 顺序理由：先交代此刻（time），再说她的身体（health），
#: 然后是今天要做的事（todo），最后才是环境（weather/home）。
#: 「你昨晚睡得不好」比「今天多云」重要。
DEFAULT_SECTIONS = ["time", "health", "todo", "weather", "home"]

#: 早报的字符预算比每轮对话宽（registry 默认 800）。
#: 它一天只发一两次，不像每轮 Context 那样天天按未命中价付费，
#: 所以可以让他多看一点。但仍然要有上限 —— Provider 一多就会悄悄膨胀。
DAILY_BUDGET = 1200


@dataclass
class DailyBrief:
    """一份今日简报。

    `text` 是给模型看的；其余字段是给程序看的（接口返回、测试断言、
    以及判断「这份简报是不是残废的」）。
    """

    date: str
    weekday: str
    clock: str
    text: str
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Router 要了但根本没注册的（配置问题，比如没配 bridge / NOX_ERYU_URL）
    missing: list[str] = field(default_factory=list)
    #: 注册了但这次没拿到数据的（服务挂了 / 网络问题）
    unavailable: list[str] = field(default_factory=list)
    #: 拿到的是过期缓存的
    stale: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """这份简报是否不完整。接口和日志用它决定要不要报警。"""
        return bool(self.missing or self.unavailable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "weekday": self.weekday,
            "clock": self.clock,
            "text": self.text,
            "states": self.states,
            "missing": self.missing,
            "unavailable": self.unavailable,
            "stale": self.stale,
            "degraded": self.degraded,
        }


def build_brief(
    registry: ContextProviderRegistry,
    *,
    sections: list[str] | None = None,
    include_memory: bool = False,
    force_refresh: bool = True,
    budget: int = DAILY_BUDGET,
    now: datetime | None = None,
    turn: Turn | None = None,
) -> DailyBrief:
    """拉一遍 Provider，聚合成简报。

    `force_refresh` 默认开：早报要的是此刻的真实情况，不是半小时前缓存的。
    health 的 TTL 是「当天」—— 不强制刷新的话，早上 10 点推送很可能拿到的
    还是昨天那条睡眠记录，而那正是这份简报最不该说错的一件事。

    一个 Provider 挂掉不影响其余的（registry 已经逐个兜了异常），
    但会记进 `unavailable`，让调用方知道这份简报是残的。
    """
    names = list(sections if sections is not None else DEFAULT_SECTIONS)
    if include_memory and "memory" not in names:
        names.append("memory")

    known, missing = registry.resolve(names)
    if missing:
        # 不是错误 —— 没配 bridge 就是没有待办这一栏，
        # 但要说出来，不然「他为什么不知道我今天要干嘛」没法排查
        logger.info("早报里这几项没注册，跳过: %s", "、".join(missing))

    turn = turn or Turn(text="今天怎么样", now=now)
    states = registry.get_context_set(known, turn=turn, force_refresh=force_refresh)

    unavailable = [n for n, s in states.items() if s.get("available") is False]
    stale = [n for n, s in states.items() if s.get("stale")]
    if unavailable:
        logger.warning("早报缺了这几项: %s", "、".join(unavailable))

    # 复用 registry 的渲染 —— 每个 Provider 怎么措辞是它自己的事，
    # planner 不该在这里第二次决定「睡眠该怎么念」
    text = registry.render(known, turn=turn, force_refresh=False, budget=budget)

    t = _time_facts(states.get("time") or {}, now)
    return DailyBrief(
        date=t["date"], weekday=t["weekday"], clock=t["clock"],
        text=text, states=states,
        missing=missing, unavailable=unavailable, stale=stale,
    )


def _time_facts(time_state: dict[str, Any], now: datetime | None) -> dict[str, str]:
    """日期/星期/钟点。优先用 TimeProvider 的结果，它没在场就自己算。

    自己算这条路会走到 —— TimeProvider 理论上不会挂（纯本地计算），
    但 `sections` 是可传的，调用方完全可以要一份不含 time 的简报。
    """
    date = time_state.get("date")
    weekday = time_state.get("weekday")
    clock = time_state.get("clock")
    if date and weekday and clock:
        return {"date": str(date), "weekday": str(weekday), "clock": str(clock)}

    from personality.mood import now_cst
    dt = now or now_cst()
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": "周" + "一二三四五六日"[dt.weekday()],
        "clock": dt.strftime("%H:%M"),
    }
