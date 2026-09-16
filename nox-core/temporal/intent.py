"""Temporal Intent —— 「她说的时间关系是什么」。

契约见 `CAELUM-Temporal-Intent-Contract.md`。核心原则（糖糖 2026-09-14）：

> **Temporal Intent 描述「她说的时间关系是什么」；
> Temporal Resolver 决定「这个关系在当前时间轴上具体落在哪里」。**

所以这个文件里**没有任何一行算日期**。它只认形状。

## 这一层就是「封闭集合」本身

`KINDS` 是白名单，`__post_init__` 拒绝表外的东西。
不这么写的话，parser 里迟早长出第七、第八个分支，
而**加一个表外分支不会让任何现有测试红**（契约第八节点名的两条假绿之一）。

## LLM 填这里，但它填不出日期

这个结构里**没有任何 `resolved_*` 字段**，所以就算模型想顺手算一个日期，
它也没地方放。这比在提示词里写「别算日期」硬。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 🔴 第一版的**封闭集合**。改这里等于改契约，改之前先更新设计页。
#:
#: `slot` 不在里面 —— 它是**修饰符**，挂在别的 kind 上（见 `Intent.slot`）。
KINDS = frozenset({
    "day_offset",      # 今天(0) / 明天(+1) / 昨天(-1) / 前天(-2) / 后天(+2)
    "weekday_next",    # **下**周三 —— 下一日历周的那天
    "weekday_bare",    # 光秃秃的「周三」 —— 识别得出，但**解析不了**，见下
    "month_end",       # 月底
    "duration",        # 两个小时后 / 十分钟后
    "deadline",        # 「……之前」，包住另一个 intent
})

#: 时段修饰符。边界在 `temporal.SLOTS`（第一层），这里只认名字。
SLOT_NAMES = frozenset({"morning", "afternoon", "evening", "late_night"})

#: 每个 kind 允许带哪些字段。**多一个少一个都拒绝** ——
#: 这张表是「封闭集合」在字段层面的那一半。
_FIELDS: dict[str, frozenset[str]] = {
    "day_offset":   frozenset({"n"}),
    "weekday_next": frozenset({"weekday"}),
    "weekday_bare": frozenset({"weekday"}),
    "month_end":    frozenset(),
    "duration":     frozenset({"days", "hours", "minutes"}),   # 至少一个
    "deadline":     frozenset({"before"}),
}


@dataclass(frozen=True)
class Intent:
    """一个时间关系。**不含任何绝对时间。**

    ⚠️ `weekday_bare` 为什么是一个 kind 而不是"干脆不产出"：
    契约第五节那条兜底要求把「没有时间表达」和「有时间表达但解析不了」
    分开 —— 前者根本不产出 intent，后者要产出 intent + 原因。
    她说了「周三」，那是一个**识别得出来的时间关系**，只是系统
    无从知道她指本周还是下周（今天周四时指明天、今天周六时多半指下周）。
    所以它有资格成为 intent，由 Resolver 判成 `none` + `weekday_ambiguous`。

    > 🔶 这一条**是写 Resolver 时反推出来的，契约里还没有** ——
    > 糖糖说的「如果不够 → 改 Contract」的第一个实例。等她过目。
    """

    kind: str
    #: day_offset：正数未来、负数过去
    n: int | None = None
    #: weekday_next / weekday_bare：1=周一 … 7=周日
    weekday: int | None = None
    #: duration：至少给一个
    days: int | None = None
    hours: int | None = None
    minutes: int | None = None
    #: deadline：里面装另一个 intent
    before: "Intent | None" = None
    #: 修饰符，不是 kind。只能挂在算得出 date 的 kind 上
    slot: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"表外的 kind：{self.kind!r}（封闭集合是 {sorted(KINDS)}）")

        allowed = _FIELDS[self.kind]
        given = {f for f in ("n", "weekday", "days", "hours", "minutes", "before")
                 if getattr(self, f) is not None}
        extra = given - allowed
        if extra:
            raise ValueError(f"{self.kind} 不该带这些字段：{sorted(extra)}")
        # month_end 没有必填字段；其余至少要给一个
        if allowed and not given:
            raise ValueError(f"{self.kind} 缺字段，需要 {sorted(allowed)} 里的至少一个")

        if self.weekday is not None and not (1 <= self.weekday <= 7):
            raise ValueError(f"weekday 只能是 1-7（1=周一），拿到 {self.weekday!r}")

        if self.slot is not None:
            if self.slot not in SLOT_NAMES:
                raise ValueError(f"不认识的时段：{self.slot!r}")
            # slot 是「那天的哪一段」，挂在本身就带时刻的 kind 上没有意义
            if self.kind in ("duration", "deadline"):
                raise ValueError(f"{self.kind} 不能带 slot —— 它本身就落在一个时刻上")

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        """只导出有值的字段，省得一堆 null 进日志。"""
        d: dict[str, Any] = {"kind": self.kind}
        for f in ("n", "weekday", "days", "hours", "minutes", "slot"):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        if self.before is not None:
            d["before"] = self.before.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Intent":
        """从模型输出（或日志）读回来。**坏数据直接抛** ——

        这是封闭集合的入口，在这里放行的东西下游全会当真。
        """
        if not isinstance(d, dict):
            raise ValueError(f"intent 必须是对象，拿到 {type(d).__name__}")
        known = {"kind", "n", "weekday", "days", "hours", "minutes", "slot", "before"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"intent 里有不认识的字段：{sorted(unknown)}")
        before = d.get("before")
        return cls(
            kind=str(d.get("kind", "")),
            n=d.get("n"), weekday=d.get("weekday"),
            days=d.get("days"), hours=d.get("hours"), minutes=d.get("minutes"),
            slot=d.get("slot"),
            before=cls.from_dict(before) if before is not None else None,
        )
