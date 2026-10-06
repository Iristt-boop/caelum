"""把「今天不去，明天再去」变成一个待办决策（审计 F8）。

## 它要拆开的那件事

糖糖 2026-09-14：

> 「没有完成」≠「继续现在这个提醒策略」

现在 `todo_due.py` 把这两件揉在一起了。它开头那句
「她说一句『在忙』并不代表运动做了」说的是**不能标完成** ——
它没说不能改下次介入时间。正确的形状是：

    Todo：          状态 = 未完成        ← 不动
    Intervention：  下一次介入 = 明天     ← 只改这个

## 这个模块是**纯函数**

输入 `(Resolution, 在追的待办, today)`，输出一个决策。
没有 IO、没有模型、不产生副作用 —— 所以能穷举着测。
真正去调 bridge 的是调用方，而且第一版**根本不调**（shadow）。

## 第一版会发现的事

现有 bridge 只有 `/api/todo/fired`（「今天追过了」，跨天自动失效）。
它刚好够表达「推到明天」，**再远的推迟没有地方放** ——
`deferred_until` 是 bridge 那边要加的字段。
所以决策里会区分这两种 mechanism，让 shadow 日志直接告诉我们
「真接上的话，有多少比例是现有机制够用的」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from temporal.resolver import Resolution

logger = logging.getLogger(__name__)

#: 现有机制够用：`/api/todo/fired` 标一下今天追过了，跨天自动失效
MECH_FIRED = "mark_fired"
#: 需要 bridge 加 `deferred_until` 字段才做得到
MECH_NEEDS_FIELD = "needs_deferred_until"


@dataclass(frozen=True)
class DeferDecision:
    """要不要推迟、推到哪天、用什么机制。

    ⚠️ **`should_defer=False` 时 `why_not` 必填** —— 同 `TemporalResult`
    那条：说不清为什么没做的决策，在日志里等于没记。
    """

    should_defer: bool
    todo_id: str | None = None
    until: date | None = None
    mechanism: str | None = None
    why_not: str | None = None

    def __post_init__(self) -> None:
        if not self.should_defer and not self.why_not:
            raise ValueError("不推迟就要说明为什么")
        if self.should_defer and not (self.todo_id and self.until and self.mechanism):
            raise ValueError("要推迟就得说清楚：哪条、推到哪天、用什么机制")


def _no(reason: str) -> DeferDecision:
    return DeferDecision(should_defer=False, why_not=reason)


def decide(resolution: Resolution, *, todo_id: str | None, today: date) -> DeferDecision:
    """她这句话该不该改这条待办的下次介入时间。

    **不改状态。** 返回值里没有任何「标完成」的可能 —— 那是结构性的：
    这个模块产出的东西压根表达不了「完成」。
    """
    if not todo_id:
        #: 她说了个时间，但当时没有在追的待办 —— 常态，不是错。
        #: 记下来是为了知道「真接上的话，有多少次是能对上号的」
        return _no("当时没有在追的待办")

    #: 🔴 判据是 `ok`，不是 `unresolved_reason is None`（空集不是通过）
    if not resolution.ok:
        return _no(f"时间没解析出来：{resolution.unresolved_reason}")

    if resolution.date is None:
        #: `duration`（两小时后）和 `deadline`（周五之前）都落在这里。
        #: 待办是按**天**追的，给它一个时刻或一个上界都对不上 ——
        #: 宁可不动，也不要把「两小时后」硬取整成一天
        return _no(f"精度对不上（{resolution.precision}）—— 待办按天追，需要 date")

    until = resolution.date
    if until <= today:
        #: 「今天再说」「昨天就该做了」—— 那不是推迟
        return _no("没有往后推（还是今天或更早）")

    #: 推到明天：现有的 `/api/todo/fired` 就够 —— 它标「今天追过了」，
    #: 跨天自动失效，明天那条链会重开
    #: 再远的：`fired` 表达不了，需要 bridge 加 `deferred_until`
    mech = MECH_FIRED if until == today + timedelta(days=1) else MECH_NEEDS_FIELD

    return DeferDecision(should_defer=True, todo_id=todo_id,
                         until=until, mechanism=mech)


# ================================================================ 接线（P4，2026-10-06 她：上线接 P4）
#
# 上面是纯函数；下面两步有 IO：问小模型「指的是哪条待办」、调 bridge 推迟。
# 两种 mechanism 现在都走同一个口子 —— bridge 加了 `deferred_until`（`/api/todo/defer`），
# 「推到明天」和「推到周五」都写它；`/api/todo/due` 跳过还没到日子的。
# 🔴 全程**碰不到完成**：bridge 那个接口不动 done，这里也没有任何「标完成」的路。

#: 只有「她打算做 / 让他做」的事才可能是在说待办；「已经发生的」（report）不推
DEFER_ACTS = frozenset({"plan", "request"})

_MATCH_PROMPT = """她说：「{text}」
其中「{expression}」那件事是：{event}

下面是她还没完成的待办（id：内容）：
{todos}

这件事是不是在说其中某一条？**只有明显是同一件事时才选**
（「练腿」就是「臀腿训练」，「背单词」就是「背英语单词」，「鱼油明天吃」就是「补充鱼油和维D」）。
只输出那条的 id。对不上、或者拿不准，就输出 none。不要输出任何别的字。"""


def match_todo(ask: Any, text: str, expression: str, event: str,
               todos: list[dict]) -> tuple[str | None, str]:
    """她这件事指的是哪条待办。返回 (todo_id, todo_match_status)。

    `ask(prompt) -> str` 是小模型。回答必须**恰好是列表里的一个 id 或 none** ——
    别的一律当拿不准（ambiguous），不猜。没有待办就不问（no_candidate），省一次调用。
    """
    if not todos:
        return None, "no_candidate"
    ids = {str(t["id"]) for t in todos if t.get("id")}
    listing = "\n".join(f"- {t['id']}：{t.get('text', '')}" for t in todos if t.get("id"))
    try:
        raw = (ask(_MATCH_PROMPT.format(text=text[:200], expression=expression,
                                        event=event, todos=listing)) or "").strip().strip("`'\"「」 ")
    except Exception:  # noqa: BLE001
        logger.warning("认待办的那次调用挂了，这次不推", exc_info=True)
        return None, "ambiguous"
    if raw.lower() == "none":
        return None, "no_candidate"
    if raw in ids:
        return raw, "matched"
    logger.warning("认待办回了表外的东西 %r，当拿不准处理", raw[:60])
    return None, "ambiguous"


@dataclass(frozen=True)
class Applied:
    """一个事件接 Todo 的结局，原样填进 TemporalResult。"""

    applied: bool
    todo_match_status: str
    todo_id: str | None = None
    applied_to: str | None = None
    why_not: str | None = None


def apply(*, act: str, expression: str, event: str, text: str, resolution: Resolution,
          today: date, bridge: Any, ask: Any) -> Applied:
    """她这件事该不该推迟某条待办 —— 该的话真的推。每一步没走下去都说清为什么。"""
    if act not in DEFER_ACTS:
        return Applied(False, "not_attempted", why_not=f"act={act}，不是她要做的事")
    if not resolution.ok or resolution.date is None or resolution.date <= today:
        #: 和 decide() 里那几条同一个判据，先挡掉 —— 不往后推的话没必要去问模型
        d = decide(resolution, todo_id="(未匹配)", today=today)
        if not d.should_defer:
            return Applied(False, "not_attempted", why_not=d.why_not)
    r = bridge.get("/api/todo/list")
    if not getattr(r, "ok", False):
        return Applied(False, "not_attempted", why_not=f"读不到待办清单：{getattr(r, 'error', '?')}")
    todos = [t for t in ((getattr(r, "data", None) or {}).get("items") or []) if t.get("id")]
    todo_id, status = match_todo(ask, text, expression, event, todos)
    if not todo_id:
        return Applied(False, status, why_not="没有对得上的待办" if status == "no_candidate" else "对不上唯一一条待办")
    d = decide(resolution, todo_id=todo_id, today=today)
    if not d.should_defer:
        return Applied(False, "matched", todo_id=todo_id, why_not=d.why_not)
    until = d.until.isoformat()
    w = bridge.post("/api/todo/defer", {"id": todo_id, "until": until})
    if not getattr(w, "ok", False):
        return Applied(False, "matched", todo_id=todo_id,
                       why_not=f"bridge 没推成：{getattr(w, 'error', '?')}")
    title = next((t.get("text", "") for t in todos if str(t["id"]) == todo_id), todo_id)
    return Applied(True, "matched", todo_id=todo_id, applied_to=f"待办「{title}」推到 {until} 再追")
