"""知识小课堂的 Care 源 —— 每天一张卡，他在下午自己挑时刻讲给她。

链路（糖糖 2026-09-07 拍板的 v2）：

    06:00 后   第一轮 poll 触发生成（utility flash，见 planner/daily_card.py；
               source_state 按 date 幂等，失败明天再来）
    14:00–21:00 交付窗口内第一次 poll 提交念头，not_before 押到窗口里
               **随机挑的时刻** —— 每天整点交卡，两天就是闹钟
    到点       Orchestrator 交付（service._speak_card），说出口后
               mark_delivered，当天收工

🔴 **独立通道**：两道闸都不吃（policy 见 service.py 的 "card" 条目）——
不占每日 3 条关心额度、不吃一小时新链冷却。她 09-06 问
「会不会抵消掉其他的主动开口」——不会；账本照记（source="card"），
晨检和周报自动带上它。节奏全在自己身上：一天一张、随机时刻。

⚠️ 被看片拦截吃掉的卡**当天不补发** —— 她看片时冒出一张知识卡本来就
违和，而且拦截在 Orchestrator 里是全源公共的，不为我们开特例。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care.signal import COMPANY, CareSignal
from planner.daily_card import (
    STATE_KEY,
    generate_daily_card,
    get_today_card,
)

logger = logging.getLogger(__name__)

#: 交付窗口（本地 CST 小时）。生成可以一大早就绪，「讲」只发生在这里
WINDOW_START = 14
#: 留半小时余量：最晚 20:30 前出嘴，21 点往后就是夜间了
WINDOW_END = 21

#: 生成时刻（CST 小时）：06:00，她醒来之前卡就备好了
GENERATE_HOUR = 6

#: 急迫度。比「到点追待办」温和，比普通惦记具体 —— 同一轮撞车时让位给真事
URGENCY = 0.55

_CST = timezone(timedelta(hours=8))


class DailyCardSource:
    """每天一张知识卡的 Care 源：生成 + 择时提交，全部幂等。"""

    name = "card"

    def __init__(self, store: Any, utility: Any = None,
                 bridge: Any = None, world: Any = None,
                 gen_hour: int = GENERATE_HOUR,
                 window: tuple[int, int] = (WINDOW_START, WINDOW_END)) -> None:
        self.store = store
        self.utility = utility
        self.bridge = bridge
        self.world = world
        self.gen_hour = gen_hour
        self.window = window
        #: 今天提交过念头了吗（进程内）。重启就忘 —— 忘了的代价只是
        #: 未交付的卡换个随机时刻重新提交，行为依然正确
        self._submit_date = ""

    # ------------------------------------------------------------ poll

    def poll(self, now: datetime) -> list[CareSignal]:
        local = now.astimezone(_CST)
        today = local.date().isoformat()

        # 1) 今天的卡还没生成就生成（06:00 后首轮 poll 触发，同天幂等）。
        #    生成只到窗口上界为止 —— 夜里不为一张卡去打 utility，
        #    真缺了就明早补（窗口外本来也不交）
        card = get_today_card(self.store, now)
        if (card is None
                and self.gen_hour <= local.hour < self.window[1]):
            card = generate_daily_card(
                self.store, self.utility, self.bridge, self.world, now)
        if not card or card.get("date") != today:
            return []

        # 2) 交付窗口内、今天还没讲过 → 提交念头
        if not (self.window[0] <= local.hour < self.window[1]):
            return []
        if card.get("delivered_at"):
            return []
        if self._submit_date == today:
            return []

        # 3) 择时：窗口内随机挑个时刻（14:00 + 0~389 分钟 → 最晚 20:29），
        #    押到那个点再说。生成晚了的话 chosen 已过 —— max 取当前时刻，
        #    窗口尾部照讲不误
        self._submit_date = today
        chosen = (local.replace(hour=self.window[0], minute=0,
                                second=0, microsecond=0)
                  + timedelta(minutes=random.randint(0, 389)))
        not_before = max(chosen, local)
        if not_before > local:
            logger.info("知识小课堂定在 %02d:%02d 讲：%s",
                        not_before.hour, not_before.minute, card.get("title", ""))

        return [CareSignal(
            source=self.name,
            subject=f"知识小课堂：{card.get('title', '')}",
            thread_kind=COMPANY,
            urgency=URGENCY,
            not_before=not_before,
            payload={
                "subject_key": card.get("subject_key") or "",
                "subject": card.get("subject") or "",
                "title": card.get("title") or "",
                "body": card.get("body") or "",
                "hook": card.get("hook") or "",
            },
        )]

    # ------------------------------------------------------------ 交付回调

    def mark_delivered(self, now: datetime) -> None:
        """讲完了。写回 source_state，今天这条线收工。"""
        card = get_today_card(self.store, now)
        if card is None:
            return
        card["delivered_at"] = now.isoformat()
        self.store.set_source_state(STATE_KEY, card)
