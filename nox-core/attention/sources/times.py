"""固定时间醒来 Source —— 到了该关心她的时间点，主动醒来看一眼。

## 为什么有这个（2026-08-14 糖糖定的）

原来的 Care（沉默 4 小时自动关心）是死板的：它只知道「她没说话」，
不知道「她为什么没说话」。糖糖要的是：

    固定时间醒来 / 事件触发醒来（午饭时间、从外面回家到家）
      醒来主动发消息 → 回复 → 继续聊天
                       → 没回复 → 留纸条设置下次醒来

本 Source 实现「固定时间醒来」这一半：到了配置的时间点（午饭/晚饭/睡前），
产出一个事件，让 Nox 主动开口。事件触发那一半由现有唤醒链（remind_myself
纸条）承担 —— 那是她说话时他留纸条。

## 和 SleepSource 的区别

SleepSource 是「变化驱动」：只在睡眠状态**变化**时产事件（normal→short），
然后进 Evaluator → Registry（衰减模型，给「持续担忧」用的）。

时间醒来是「时刻驱动」：到点就该开口，不是担忧，不该进 Registry 的
衰减模型 —— 那是给「她连续三天没睡好」这种**持续状态**设计的。
所以时间醒来事件不经过 Evaluator，由 service 直接变成 Intent。

## 时间窗口，不是精确匹配

Attention 心跳是每 15 分钟一次。如果配置 12:00，poll 在 12:00:30 才跑，
精确匹配 `now == 12:00` 会漏掉。所以用**窗口**：现在落在 [目标-前5分钟,
目标+后10分钟] 内就算命中，且当天每个时间点只触发一次（记在 source_state）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from attention.events import ExperienceEvent
from attention.scheduler import LOCAL_TZ

logger = logging.getLogger(__name__)

#: 存「今天哪些时间点已触发」用的 key
STATE_KEY = "source.times"

#: 窗口：目标时间前多少分钟开始算（poll 可能早到几分钟）
WINDOW_BEFORE_MIN = 5
#: 窗口：目标时间后多少分钟内仍算（poll 可能迟到）
WINDOW_AFTER_MIN = 10

#: 配置格式 "HH:MM:主题"，逗号分隔。例："12:00:午饭,18:30:晚饭,22:30:睡前"
DEFAULT_TIME_WAKES = "12:00:午饭,18:30:晚饭,22:30:睡前"


@dataclass(frozen=True)
class TimeWake:
    """一个固定时间醒来的配置项。"""

    hour: int
    minute: int
    subject: str  # 午饭 / 晚饭 / 睡前 —— 这是 intent 的 subject，也是话题冷却的 key

    @classmethod
    def parse(cls, text: str) -> list["TimeWake"]:
        out = []
        for part in (text or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                # 格式 "HH:MM:主题" —— 前两段是时间，最后一段是主题。
                # 主题里可能有冒号（比如 "12:00:午饭:加餐" 的主题是 "午饭:加餐"），
                # 所以从**后面**拆：先按 2 个冒号拆出时间，剩余全是主题。
                segs = part.split(":")
                if len(segs) < 3:
                    logger.warning("时间醒来配置格式不对（需要 HH:MM:主题），跳过：%s", part)
                    continue
                h, m = int(segs[0]), int(segs[1])
                subject = ":".join(segs[2:]).strip()
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    logger.warning("时间醒来配置时间不合法，跳过：%s", part)
                    continue
                if not subject:
                    logger.warning("时间醒来配置缺主题，跳过：%s", part)
                    continue
                out.append(cls(hour=h, minute=m, subject=subject))
            except Exception:  # noqa: BLE001
                logger.warning("时间醒来配置解析失败，跳过：%s", part)
        return out


class _StateStore(Protocol):
    def get_source_state(self, key: str) -> dict[str, Any] | None: ...
    def set_source_state(self, key: str, value: dict[str, Any]) -> None: ...


class TimeWakeSource:
    """到点产出「该开口了」的事件。

        src = TimeWakeSource(config_text, store)
        event = src.poll(now)      # 到点且今天没触发过就产事件
    """

    def __init__(
        self,
        config_text: str,
        store: _StateStore,
        before_min: int = WINDOW_BEFORE_MIN,
        after_min: int = WINDOW_AFTER_MIN,
    ) -> None:
        self.wakes = TimeWake.parse(config_text)
        self.store = store
        self.before = timedelta(minutes=before_min)
        self.after = timedelta(minutes=after_min)

    @property
    def enabled(self) -> bool:
        return bool(self.wakes)

    def poll(self, now: datetime | None = None) -> ExperienceEvent | None:
        """当前落在某个时间窗口内且今天没触发过 → 产事件；否则 None。"""
        if not self.wakes:
            return None
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(LOCAL_TZ)
        today = local.date().isoformat()

        state = self.store.get_source_state(STATE_KEY) or {}
        fired = set(state.get("fired_today") or [])
        # 跨天了清掉昨天的记录
        if state.get("date") != today:
            fired = set()

        for w in self.wakes:
            if w.subject in fired:
                continue
            target = local.replace(hour=w.hour, minute=w.minute, second=0, microsecond=0)
            if target - self.before <= local < target + self.after:
                fired.add(w.subject)
                self.store.set_source_state(STATE_KEY, {
                    "date": today,
                    "fired_today": sorted(fired),
                })
                logger.info("时间醒来触发：%s（%02d:%02d）", w.subject, w.hour, w.minute)
                return ExperienceEvent(
                    source="times",
                    type="time_wake",
                    subtype=w.subject,
                    payload={"wake_at": local.isoformat()},
                    timestamp=now,
                    priority="high",
                )

        return None

    def reset(self) -> None:
        """清掉今天已触发的记录（测试/手工用）。"""
        try:
            self.store.set_source_state(STATE_KEY, {"date": "", "fired_today": []})
        except Exception:  # noqa: BLE001
            pass
