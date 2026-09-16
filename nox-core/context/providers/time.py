"""TimeProvider —— 现在几点、周几、什么时段。

## 为什么它值得单独存在（mood 里不是已经有时段判断了吗）

不一样。`mood.detect_scene()` 输出的是**行为指令**：
「已经凌晨了，她该睡了，语气放轻」。
这里输出的是**客观事实**：8 月 2 日、周日、16:30。

他需要知道今天几号，才能算出「你上周说的那件事」是什么时候；
需要知道是不是周末，才知道该不该问「今天上班吗」。
这些 mood 都不提供。

## 时区固定 UTC+8，不用机器本地时间

跟 `personality/mood.py` 同一个理由，那里写得很清楚：
本地开发在 Windows、线上在 VPS，眼下都是 +8 所以看不出问题。
但哪天服务器重装成 UTC，时段判断就整体偏 8 小时，
而且**不会报错**，只会让他在她上午聊天时说「该睡了」。
所以直接复用 `mood.now_cst()`，不另起一套。

## 没有节假日数据源，就不报节假日

架构文档的 `TimeContext` 里有 `is_holiday`，但中国节假日是国务院每年单独发文
（还有调休），没有表就算不出来。**宁可不提供，也不编一个。**
真要做得先接一份数据源，那是另一件事。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from context.timeline import slot_of
from personality.mood import now_cst

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 时段表从 `context/timeline.py` 取，**这里不再写一份**（2026-09-14）。
# 历史的日内时间标记和这里的【此刻】必须用同一套边界 ——
# 分成两处写，迟早出现「历史标着下午、【此刻】说晚上」这种谁也解释不清的错位。


class TimeProvider(BaseContextProvider):
    """当前时间。零外部依赖，最先做的那个（架构文档 Phase 2）。"""

    name = "time"
    section = "time"
    # 1 分钟。纯本地计算其实零成本，给 TTL 只是为了让同一轮里
    # 多次读到的是同一个时刻，不会一个 Provider 看到 16:59、另一个看到 17:00
    ttl = timedelta(minutes=1)

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        now = turn.now or now_cst()
        hour = now.hour
        slot, slot_cn = slot_of(hour)

        return {
            "datetime": now.isoformat(timespec="minutes"),
            "date": now.strftime("%Y-%m-%d"),
            "clock": now.strftime("%H:%M"),
            "weekday": _WEEKDAYS[now.weekday()],
            "hour": hour,
            "time_of_day": slot,
            "time_of_day_cn": slot_cn,
            "is_weekend": now.weekday() >= 5,
            # is_holiday 故意没有：没有数据源就不报，见模块开头
        }

    def render(self, state: dict[str, Any]) -> str:
        """给模型看的那句 —— **精确到小时，不带分钟**。

        理由是缓存的有效窗口。DeepSeek 的提示词缓存是**自动前缀匹配**
        （没有 Anthropic 那种显式断点）：`dynamic_system` 变一个字，
        它后面的 history 和当轮消息**全部错位**，一起不命中。

        写成 `16:30` 的话，这段每分钟都不一样 —— 缓存的有效期就被压成一分钟，
        隔一分钟再说话就是一次冷启动（实测冷启动只命中静态前缀 7040 / 11257，62.5%；
        命中时是 11136 / 11257，98.9%）。小时级则一小时内稳定。

        她真要问「现在几点几分」，他有 `get_current_time` 工具去拿精确值。
        精确时间也仍然留在 state 里（`clock` / `datetime`），那是给程序用的。

        > 2026-08-02 我一度把一次 62.5% 归咎于这里的分钟数，其实那只是冷启动。
        > 控制变量实验（同一句话连发三次）才证明稳态是 98.9%。
        > **归因之前先做对照实验** —— 少了这一步，改对了也是蒙的。
        """
        if state.get("available") is False:
            return ""
        weekend = "，周末" if state.get("is_weekend") else ""
        return (f"【此刻】{state['date']} {state['weekday']} "
                f"{state['hour']} 点（{state['time_of_day_cn']}{weekend}）")
