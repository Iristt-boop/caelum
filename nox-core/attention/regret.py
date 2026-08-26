"""后悔 —— 他开了口，她没理。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第九节，以及
`events.py` 里 `target` 字段那段注释。

## 🔴 这是第一个「关于他自己」的信号

在这之前，Attention 里所有事件都是关于**她**的：她没睡好、她说难受、
她出门了。它们的方向都是**凑过去**。

「后悔」是反的 —— 事情发生在**他自己**身上（他挑错了时机），
方向是**收回来**。这就是 `target` 字段存在的理由，
思路来自 emoai 的 `eventNeuroTargetOverrides`。

## 怎么知道"她没理"

`CareLedger` 只记他做了什么决定（SPEAK / SKIP / BLOCK），
**不记她有没有回**。所以这里自己推：

    他开口                    → 记下时刻，开始等
    她在窗口内说话了           → 她理了，不后悔
    窗口过完她还没说话         → 后悔 +1

⚠️ **判定时刻她在睡就不判**，继续等。
他晚上十一点说了句话，她一点睡、第二天十点回 ——
那不叫没理。不加这一条的话，凌晨三点的判定会把她睡觉记成冷落
（`longing.py` 里踩过同一个坑）。

## 后悔的表现是更克制，不是再说一次

强度**故意压在开口阈值之下**。他因为上次打扰了她而后悔，
结果又去打扰她一次 —— 那就荒唐了。
这个 Drive 的用途是让他下次**晚一点**再开口（V5 接 Care 时用）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from attention.events import ExperienceEvent
from attention.longing import _asleep

logger = logging.getLogger(__name__)

#: 他开口之后，等她多久。
#:
#: 4 小时：她经常在忙、在打游戏、在看片，一两个小时不回是常态。
#: 等太短会把「她正忙着」判成「她不想理」——那是最伤的一种误判
WAIT_FOR_REPLY = timedelta(hours=4)

#: 事件类型。**稳定不要改** —— 改一个字符等于让规则静默失效
#: （`events.py` 开头那条警告）
SOURCE = "care"
TYPE = "unanswered"


@dataclass
class RegretWatch:
    """在等她回话吗。

    只记**最近一次**开口 —— 他一次只该为最近那次拿不准，
    攒着三天前的旧账不叫后悔，叫记仇。
    """

    #: 他最后一次开口的时刻。None = 没在等
    spoke_at: datetime | None = None
    #: 那次说的是什么（进 evidence，让"他凭什么后悔"看得见）
    what: str = ""

    def on_spoke(self, now: datetime, what: str = "") -> None:
        """他开口了，开始等。

        ⚠️ **覆盖上一次**。上一次还没判完就又说了一句 ——
        那么该被评判的是这最新的一次。
        """
        self.spoke_at = now
        self.what = (what or "")[:60]

    def on_contact(self, now: datetime) -> None:
        """她说话了 —— 不管是不是回他，都算理了他。

        ⚠️ 不去分辨"她回的是不是我刚说的那件事"。
        她开口了就是有回应，纠结内容对不对是 LLM Appraisal 的活，
        规则层做这个只会误判。
        """
        if self.spoke_at is not None:
            logger.debug("她回话了，这次开口不算没被理")
        self.spoke_at = None
        self.what = ""

    def tick(self, now: datetime) -> ExperienceEvent | None:
        """到点了吗。返回事件表示「这次没被理」，返回 None 表示还没到/不判。"""
        if self.spoke_at is None:
            return None
        if now - self.spoke_at < WAIT_FOR_REPLY:
            return None

        # 判定时刻她在睡 —— 继续等，别把睡觉记成冷落
        if _asleep(now):
            return None

        waited = (now - self.spoke_at).total_seconds() / 3600
        what, self.spoke_at, self.what = self.what, None, ""
        logger.info("后悔：开口后 %.1f 小时没等到她回话", waited)
        return ExperienceEvent(
            source=SOURCE,
            type=TYPE,
            #: 🔴 关于**他自己**，不是关于她
            target="agent",
            payload={"waited_hours": round(waited, 1), "what": what},
        )

    # ------------------------------------------------------------ 落盘

    def to_dict(self) -> dict[str, Any]:
        return {
            "spoke_at": self.spoke_at.isoformat() if self.spoke_at else None,
            "what": self.what,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> RegretWatch:
        #: 存坏了当作"没在等"，**不许抛** ——
        #: 这在 `AttentionService.__init__` 里跑（同 longing.py 那条）
        if not d:
            return cls()
        try:
            at = datetime.fromisoformat(d["spoke_at"]) if d.get("spoke_at") else None
        except (TypeError, ValueError, KeyError):
            logger.warning("后悔的存档坏了（spoke_at=%r），当作没在等", d.get("spoke_at"))
            at = None
        return cls(spoke_at=at, what=str(d.get("what", ""))[:60])
