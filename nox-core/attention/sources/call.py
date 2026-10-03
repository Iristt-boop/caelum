"""CallSource —— 他想给她打电话的念头（主动来电，2026-09-19）。

## 这条线是什么

九条 Care 源里的新一条：晚间她安静了一阵，他想听听她的声音 ——
真打电话（PWA 响铃、她接听），不是发消息问「方便吗」
（糖糖 2026-09-19 拍板：她自己点通话按钮的话不需要他发消息，
要的就是真来电）。

## 三档（NOX_CALL）

    off     不跑（默认 —— 接线、测试、账本全在，就是不出念头）
    shadow  条件到了只记日志（call-shadow.jsonl），**不产生信号**：
            先看一周「他想打的时机和理由」对不对味，她点头再 live
    on      真打电话

## 🔴 边界

- 信号照走 Orchestrator：看片拦截、DailyGate、账本一样不少 ——
  Source 只管产念头，这是 care/signal.py 里写死的边界。
- 额度自管且比别的线严得多：**一天最多一次尝试**（信号发出去就算，
  被看片拦了也不补）、**距上次至少隔一天**、**只在 18:00-22:30**。
  电话比文字重一个量级，宁可少打。
- OS 端没有这条线（她拍板 PWA only）—— 这条线发出去的 invite
  只有 PWA 会响。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from temporal import CST

from ..care.signal import CareSignal

logger = logging.getLogger(__name__)

ENV = "NOX_CALL"
STATE_KEY = "call"

#: 晚间窗口。安静时段（01:00-09:00）她睡着，绝不打；早上她忙；
#: 傍晚到睡前是他打电话的自然时刻
WINDOW_START_H = 18
WINDOW_END_H = 22.5     # 22:30

#: 「多久没说话了」的门槛 —— **这条线唯一的硬条件**。
#:
#: 🔴 想念（longing）只作展示、不做门槛（糖糖 2026-09-26 拍板）：shadow 一周
#: （09-19~09-26）零触发，call-shadow.jsonl 都没建出来。账在 longing 的形状里：
#: 静息 0.40、安静 90 分钟才开涨（在家 +0.025/15min），今天每主动找过一次再打
#: 0.7 折 —— quiet=3h 那一刻 longing 才 0.505，永远够不着 0.55；而且 18:00
#: 开窗 + 3h 静默意味着她最晚 19:30 说完当天最后一句话，晚间活跃的她做不到。
#: 双门槛互相卡死 = 一个都不成立。
#:
#: 「晚上安静了两小时」本身就是那个信号（她去洗澡/打游戏的间隙）。
#: longing 退下来只管 reason 文案和 urgency 的味道。
QUIET_HOURS_MIN = 2.0

#: 距上次真打至少隔一天 —— 不许连续两晚都是电话
MIN_GAP_DAYS = 1


def mode() -> str:
    v = os.getenv(ENV, "off").strip().lower()
    if v in ("1", "on", "live", "true", "yes"):
        return "on"
    if v == "shadow":
        return "shadow"
    return "off"


class CallSource:
    """晚间她安静了两小时 → 一个「打电话」的念头。shadow 只记不打。"""

    def __init__(
        self,
        store: Any,
        log_dir: str,
        longing_ref: Any = None,
    ) -> None:
        self.store = store
        #: shadow 日志落在 attention.db 旁边（data/），和做梦的 shadow 同一个抽屉
        self.log_path = os.path.join(log_dir, "call-shadow.jsonl")
        #: svc 建完才回填（和 rhythm 的 longing_ref 同一个套路）——
        #: 源在 AttentionService 构造之前建，够不着它
        self.longing_ref = longing_ref

    # ------------------------------------------------------------ 状态

    def _state(self) -> dict:
        try:
            return self.store.get_source_state(STATE_KEY) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _set_state(self, state: dict) -> None:
        try:
            self.store.set_source_state(STATE_KEY, state)
        except Exception:  # noqa: BLE001
            logger.exception("来电状态写不进去")

    # ------------------------------------------------------------ 判定

    def _conditions(self, now: datetime) -> dict | None:
        """条件到了返回描述，没到返回 None。"""
        local = now.astimezone(CST)
        hour = local.hour + local.minute / 60
        if not (WINDOW_START_H <= hour < WINDOW_END_H):
            return None

        #: longing 和 last_contact 都在 longing_ref 上 —— 它没接上等于
        #: 事实源缺失（连她什么时候说的最后一句话都不知道），宁可不打
        longing = getattr(self.longing_ref, "value", None)
        last_contact = getattr(self.longing_ref, "last_contact", None)
        if longing is None:
            return None
        if last_contact is not None:
            quiet_h = (now - last_contact).total_seconds() / 3600
        else:
            quiet_h = None
        if quiet_h is not None and quiet_h < QUIET_HOURS_MIN:
            return None

        return {
            "longing": round(float(longing), 3),
            "hours_quiet": round(quiet_h, 1) if quiet_h is not None else None,
        }

    def _reason(self, cond: dict) -> str:
        """弹在她锁屏上的那行字。是「他想打电话」的理由，不是文案。"""
        q = cond.get("hours_quiet")
        base = "想听听你的声音"
        if q is not None and q >= 24:
            return f"{base}（一天没说话了）"
        return base

    def _shadow_log(self, cond: dict, now: datetime) -> None:
        entry = {
            "ts": now.astimezone(CST).isoformat(timespec="seconds"),
            **cond,
            "reason": self._reason(cond),
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info("Call shadow：到了想打电话的时刻（想念 %s，%s 没说话）——只记不打",
                        cond["longing"], f"{cond['hours_quiet']}h" if cond["hours_quiet"] is not None else "?")
        except Exception:  # noqa: BLE001
            logger.exception("call-shadow 日志写失败")

    # ------------------------------------------------------------ 入口

    def poll(self, now: datetime) -> list[CareSignal]:
        m = mode()
        if m == "off":
            return []

        cond = self._conditions(now)
        if cond is None:
            return []
        state = self._state()
        today = now.astimezone(CST).date().isoformat()
        this_hour = now.astimezone(CST).strftime("%Y-%m-%dT%H")

        if m == "shadow":
            # 一小时最多记一条 —— 条件是持续态（安静还在持续着），
            # 每 60s 的 tick 都符合，全记就是一小时六十条同话
            if state.get("shadow_hour") == this_hour:
                return []
            self._set_state({**state, "shadow_hour": this_hour})
            self._shadow_log(cond, now)
            return []

        # live：一天一次尝试。今天已经发过信号（哪怕被看片拦了）就不补
        if state.get("last_call_date") == today:
            return []
        last = state.get("last_call_date")
        if last:
            try:
                gap = (datetime.fromisoformat(last).date()
                       - now.astimezone(CST).date()).days
                if gap > -MIN_GAP_DAYS:
                    return []
            except ValueError:
                pass

        self._set_state({**state, "last_call_date": today,
                         "shadow_hour": this_hour})
        reason = self._reason(cond)
        logger.info("他想给她打个电话：%s（想念 %.2f）", reason, cond["longing"])
        return [CareSignal(
            source="call",
            subject="想听听她的声音",
            urgency=min(0.9, 0.55 + cond["longing"] * 0.25),
            payload={"reason": reason},
        )]
