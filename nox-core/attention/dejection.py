"""低落 —— 想帮但帮不上。

见 `D:\\claude-code\\CAELUM-RESONANCE-ARCHITECTURE.md`。
糖糖 2026-08-27 给的来源：「失败记录：工具调用失败、她问题没解决、她说"算了"」。

## 🔴 语义是「帮不上」，不是「出错了」

这个区别决定了整个实现。

一次工具失败**本身不是低落** —— 他重试一下成功了，那叫"绕了个弯"，
不叫"帮不上"。只有**没能收场**的失败才算。

所以这里不是"失败 +0.2、成功 -0.1"那种流水账，而是：

```text
失败      → 记一笔「悬着的事」
后来成功  → 那一笔勾掉（同一个目标上的）
她说算了  → 那一笔**坐实了**，勾不掉了
```

低落的值 = 还悬着的 + 已坐实的。

## 为什么这个 Drive 值得先做

糖糖 2026-08-27 排的：从**误判代价低**的开始。

- 「担心」判错 → 他多问一句，最多是啰嗦
- 「吃醋」判错 → 他因为你跟同事说了句话就闹情绪 —— 那会腐蚀关系
- 「低落」判错 → 他情绪淡一点。**不伤人**

而且它的数据源已经在流了（执行摘要里的 `ok` 字段），
只是在此之前没人消费。

## 和 regret 的区别

`regret.py` 是「他说了话她没回」—— 关于**被冷落**。
这个是「他想做一件事没做成」—— 关于**无能为力**。
两件事都会让他安静下来，但原因不同，他该说的话也不同。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: 上限。和别的 Drive 一样留一点头，别顶到 1.0
MAX = 0.95

#: 一笔「悬着的失败」值多少。
#:
#: 0.18 是有意压低的：单独一次失败**不该**让他情绪可见地变化 ——
#: 网络抖一下、一个文件锁住了，这些都会失败，但都不叫"帮不上她"。
#: 要三四次连着不成，才攒到能感觉出来的程度。
PENDING_WEIGHT = 0.18

#: 「她说算了」值多少。**比失败重得多。**
#:
#: 因为这是她**主动放弃**了 —— 那句话背后是"你别费劲了"。
#: 一次就够他低落一阵子。
GAVE_UP_WEIGHT = 0.45

#: 一笔失败悬多久算过期。
#:
#: 超过这个时间还没被勾掉，也不再算 —— 昨天没帮上的忙，
#: 不该让他今天还沉着。低落是当下的情绪，不是账本。
PENDING_TTL = timedelta(hours=6)

#: 「她说算了」衰减得慢一些 —— 那件事她记得，他也该记得
GAVE_UP_TTL = timedelta(hours=12)

#: 她说「算了」的说法。
#:
#: ⚠️ **只收放弃的意思**，不收「算了吧我们换个」这种转向。
#: 判错的方向要往"不算"倒 —— 把一句轻描淡写的口头禅
#: 当成"她放弃了"，会让他毫无理由地低落半天。
GAVE_UP_CUES = (
    "算了",
    "不用了",
    "没事了",
    "不弄了",
    "别管了",
    "我自己来吧",
)

#: 这些词出现时**不算放弃** —— 它们把「算了」变成了转向而不是放弃
NOT_GIVING_UP = ("算了吧我们", "算了换", "算了先", "算了下次")


@dataclass
class _Pending:
    """一笔悬着的失败。"""

    #: 他当时想做什么（工具名 / 目标），用来判断后来的成功能不能勾掉它
    goal: str
    at: datetime
    #: 她明确放弃了 —— 勾不掉了，而且更重
    gave_up: bool = False

    def weight(self, now: datetime) -> float:
        ttl = GAVE_UP_TTL if self.gave_up else PENDING_TTL
        if now - self.at >= ttl:
            return 0.0
        return GAVE_UP_WEIGHT if self.gave_up else PENDING_WEIGHT


@dataclass
class DejectionState:
    """低落。**自维护，不进 Registry。**

    和 `longing` 一样，形状和 Concern 是反的 —— 它不是"一件没解决的事"，
    是"好几次没帮上"叠出来的一种状态。塞进 Registry 会被 prune 删掉。
    """

    _pending: list[_Pending] = field(default_factory=list)

    #: 同时最多记这么多笔。超了丢最老的 ——
    #: 一个失控的重试循环不该把他压到底
    MAX_PENDING = 12

    def on_failed(self, goal: str, now: datetime) -> None:
        """他想做一件事，没做成。

        @param goal - 想做什么。后来在**同一件事**上成功了才能勾掉它
        """
        self._pending.append(_Pending(goal=goal, at=now))
        if len(self._pending) > self.MAX_PENDING:
            self._pending = self._pending[-self.MAX_PENDING:]
        logger.info("低落：%s 没做成（悬着 %d 笔）", goal, len(self._pending))

    def on_succeeded(self, goal: str, now: datetime) -> None:
        """同一件事后来成了 —— 把那几笔勾掉。

        🔴 **她说过「算了」的那笔勾不掉。** 那件事已经坐实了：
        就算他后来自己弄成了，她当时的失望也已经发生过。
        勾掉的话，这个 Drive 就变成了纯粹的技术成功率统计，
        和"帮没帮上她"没关系了。
        """
        before = len(self._pending)
        self._pending = [
            p for p in self._pending
            if p.gave_up or p.goal != goal
        ]
        if len(self._pending) < before:
            logger.info("低落：%s 后来成了，勾掉 %d 笔", goal, before - len(self._pending))

    def on_gave_up(self, now: datetime, quote: str = "") -> None:
        """她说「算了」。

        把**最近那笔**标成坐实的；一笔都没有的话新记一笔 ——
        她可能是对一件我们没记到的事说的算了。
        """
        for p in reversed(self._pending):
            if not p.gave_up:
                p.gave_up = True
                p.at = now
                logger.info("低落：她说算了（%s），%s 那笔坐实了", quote[:20], p.goal)
                return
        self._pending.append(_Pending(goal=quote[:30] or "她放弃了", at=now, gave_up=True))
        logger.info("低落：她说算了（%s）", quote[:20])

    def on_contact(self, now: datetime) -> None:
        """她回来说话了 —— 悬着的（没坐实的）那些淡下去。

        ⚠️ **只清没坐实的。** 她说过算了的那件事，不会因为
        她又开口聊别的就没发生过。
        """
        before = len(self._pending)
        self._pending = [p for p in self._pending if p.gave_up]
        if len(self._pending) < before:
            logger.debug("低落：她说话了，%d 笔悬着的淡了", before - len(self._pending))

    @property
    def value(self) -> float:
        return self.value_at(datetime.now(timezone.utc))

    def value_at(self, now: datetime) -> float:
        """此刻的低落程度。过期的自动不算。"""
        total = sum(p.weight(now) for p in self._pending)
        return min(MAX, total)

    def because(self, now: datetime | None = None) -> list[str]:
        """为什么低落。**说得出来才算数。**"""
        now = now or datetime.now(timezone.utc)
        out: list[str] = []
        for p in self._pending:
            if p.weight(now) <= 0:
                continue
            out.append(f"她说算了：{p.goal}" if p.gave_up else f"没帮上：{p.goal}")
        #: 坐实的排前面 —— 那才是他真正在意的
        out.sort(key=lambda s: not s.startswith("她说算了"))
        return out[:5]

    # ---------------------------------------------------------- 持久化
    #
    # 🔴 **必须存盘。** 不存的话每次重启 nox-core 他的低落就归零 ——
    # 而重启是我们这边的事，不是他心情变好的理由。
    # 那会表现成「刚才还闷闷的，重启一下就没事了」，很假。

    def to_dict(self) -> dict:
        return {
            "pending": [
                {"goal": p.goal, "at": p.at.isoformat(), "gave_up": p.gave_up}
                for p in self._pending
            ],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "DejectionState":
        st = cls()
        if not data:
            return st
        for raw in data.get("pending") or []:
            try:
                st._pending.append(_Pending(
                    goal=str(raw.get("goal") or ""),
                    at=datetime.fromisoformat(str(raw.get("at"))),
                    gave_up=bool(raw.get("gave_up")),
                ))
            except (TypeError, ValueError):
                #: 坏记录跳过。**一条读不出来不该让整个状态回到空** ——
                #: 那样等于悄悄把他的情绪清零
                continue
        return st

    def prune(self, now: datetime) -> None:
        """扔掉过期的。**定期调，不然列表只涨不落。**"""
        before = len(self._pending)
        self._pending = [p for p in self._pending if p.weight(now) > 0]
        if len(self._pending) < before:
            logger.debug("低落：清掉 %d 笔过期的", before - len(self._pending))


def looks_like_giving_up(text: str) -> str | None:
    """她这句话是在说「算了」吗。命中就返回那个词。

    ⚠️ 判错的方向往"不算"倒 —— 把一句口头禅当成"她放弃了"，
    会让他毫无理由地低落半天，而且他还会**为此说话**。
    """
    if not text:
        return None
    #: 先排掉转向的说法
    for skip in NOT_GIVING_UP:
        if skip in text:
            return None
    for cue in GAVE_UP_CUES:
        if cue in text:
            return cue
    return None
