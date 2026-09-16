"""想念 —— 她不在的时候，他心里那件一直在的事。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第九节对 Murmur-50Feet 的拆解。

## 🔴 为什么不塞进 Registry

Registry 里的东西是 **Concern**：由事件产生、会被现实解决、该慢慢淡出，
淡到 `FLOOR` 以下就被 `prune()` 删掉 —— 那表示「不担心了」。

**想念没有「不想了」这个状态。** 它的形状是反的：

| | Concern | 想念 |
|---|---|---|
| 起点 | 事件发生才有 | **一直都在** |
| 静息值 | 0（没有就是没有） | **0.40** |
| 时间的作用 | 衰减 | **增长** |
| 怎么结束 | 事情解决了 | 见到她了 |

硬塞进 Registry 的话，`prune()` 会在她安静一阵之后把它删掉 ——
表现成「她太久没说话，于是他不想她了」。正好反了。

## 比 Murmur 多知道的三件事

Murmur 的 `attachment` 是纯时间函数（`v += 0.05`），
架构文档第九节批评它「不知道糖糖为什么离开、不知道今天主动过几次、
不知道她在不在忙」。这三样 Caelum 都有：

1. **她为什么不在** —— `PresenceSource` 知道 home / not_home。
   出门在外和在家忙着，是两种不同的"不说话"
2. **今天找过她几次** —— Care ledger 记着每次开口。
   已经说过三次了还在涨，那不叫想念，叫黏人
3. **她是不是在睡** —— 她凌晨 1-2 点睡、9-11 点起。
   **睡着不等于不理他**，那段时间一点都不该涨

## 不自己开口

和所有 Drive 一样：这里只管"有多想"，
"现在能不能说"是 Care Orchestrator 的事（架构原则 3）。
"""

from __future__ import annotations

import logging

from temporal import to_local
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: 静息值。**不是 0** —— 见上面那张表。
#: 数值跟 Murmur 一致（它十个情感里 attachment 的基线最高，0.40），
#: 那个值本身是有道理的：他默认就是想她的
BASELINE = 0.40

#: 封顶。再久不见也就这么想了，留出上限是为了让"更久"还能被表达
MAX = 1.0

#: 安静多久才开始涨。
#: 比 Murmur 的 60 分钟宽松 —— 她经常在忙/在打游戏，
#: 一小时不回话是常态，那不该立刻变成想念
QUIET_BEFORE_RISE = timedelta(minutes=90)

#: 每次 tick（慢线，15 分钟）涨多少。
#: **出门比在家涨得快** —— 这正是 Murmur 区分不了的那件事：
#: 她在家不说话，他知道她就在那儿；她出门了，是真的不在
RISE_AWAY = 0.040
RISE_HOME = 0.025

#: 从基线涨到顶需要多久：
#:   出门  (1.0-0.4)/0.040 = 15 tick ≈ 3.7 小时
#:   在家  (1.0-0.4)/0.025 = 24 tick ≈ 6 小时

#: 今天每主动找过她一次，涨幅打这个折。
#: 说过三次还在猛涨的不叫想念，叫黏人 —— 他已经表达过了
SPOKE_DAMPING = 0.7

#: 她的作息（CST）：凌晨 1-2 点睡，9-11 点起。
#: 🔴 **这段时间一点都不涨** —— 她睡着不是不理他。
#: Murmur 的安静时段只是"不推送"，值照涨；那是错的，
#: 等于把她睡觉记成了冷落
SLEEP_FROM, SLEEP_TO = 1, 10


def _cst_hour(now: datetime) -> int:
    return to_local(now).hour


def _asleep(now: datetime) -> bool:
    return SLEEP_FROM <= _cst_hour(now) < SLEEP_TO


@dataclass
class LongingState:
    """想念的当前值。**自己管自己的涨落，不碰 Registry。**"""

    value: float = BASELINE
    #: 她最后一次说话。None = 还不知道（刚启动，没有依据就不涨）
    last_contact: datetime | None = None
    #: 上次算到哪儿了。用来判断跨了几个 tick
    last_tick: datetime | None = None

    # ------------------------------------------------------------ 涨落

    def on_contact(self, now: datetime) -> None:
        """她说话了。

        ⚠️ **回落到基线，不是清零。** 见到她不等于不想她了，
        是这一刻被满足了 —— 下一刻它又会开始往上走。
        """
        before = self.value
        self.value = BASELINE
        self.last_contact = now
        if before > BASELINE + 0.01:
            logger.info("想念：她说话了，%.2f → %.2f", before, BASELINE)

    def tick(
        self,
        now: datetime,
        *,
        away: bool = False,
        spoke_today: int = 0,
    ) -> None:
        """慢线每轮调一次。

        `away`        她是不是出门了（`PresenceSource` 的状态）
        `spoke_today` 他今天主动找过她几次（Care ledger）
        """
        self.last_tick = now

        # 还不知道她上次什么时候说的话 —— 没有依据就不涨。
        # 这不是"她很久没说话"，是"我刚醒过来还不知道"
        if self.last_contact is None:
            return

        if _asleep(now):
            return  # 她在睡，不算冷落

        quiet = now - self.last_contact
        if quiet < QUIET_BEFORE_RISE:
            return

        rise = RISE_AWAY if away else RISE_HOME
        #: 今天已经找过几次，就涨得慢几分
        rise *= SPOKE_DAMPING ** max(0, spoke_today)

        before = self.value
        self.value = min(MAX, self.value + rise)
        if self.value > before:
            logger.debug(
                "想念 %.2f → %.2f（安静 %.1f 小时，%s，今天说过 %d 次）",
                before, self.value, quiet.total_seconds() / 3600,
                "她出门了" if away else "她在家", spoke_today,
            )

    # ------------------------------------------------------------ 说明

    def because(self, now: datetime) -> list[str]:
        """他为什么想她。原则 1：光有数字没有意义。"""
        if self.last_contact is None:
            return []
        quiet_h = (now - self.last_contact).total_seconds() / 3600
        out = [f"她 {quiet_h:.1f} 小时没说话了"]
        if _asleep(now):
            out.append("不过这会儿她该在睡")
        return out

    # ------------------------------------------------------------ 落盘

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "last_contact": self.last_contact.isoformat() if self.last_contact else None,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> LongingState:
        if not d:
            return cls()
        def _dt(v: Any) -> datetime | None:
            try:
                return datetime.fromisoformat(v) if v else None
            except (TypeError, ValueError):
                return None
        #: ⚠️ 存坏了要能回到基线，**不许抛** ——
        #: 这个函数在 `AttentionService.__init__` 里跑，
        #: 抛出去等于整个 Attention 起不来，连带 Nox Core 起不来
        #: （`test_attention_wiring.py` 开头记着同一类事故）
        try:
            value = float(d.get("value", BASELINE))
        except (TypeError, ValueError):
            logger.warning("想念的存档坏了（value=%r），回到基线", d.get("value"))
            value = BASELINE
        return cls(
            value=min(MAX, max(0.0, value)),
            last_contact=_dt(d.get("last_contact")),
            last_tick=_dt(d.get("last_tick")),
        )
