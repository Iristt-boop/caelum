"""HealthProvider —— 她昨晚睡得怎么样、今天走了多少。

数据来自 health-mcp（`http://127.0.0.1:8101/mcp`），只听本机、不出公网。
上游是 iPhone 快捷指令每天早上自动同步一次 Apple Health。

## ⚠️ 不进每轮名单

和 `memory` 同理：**健康数据一天才更新一次**，每轮都塞进去毫无意义，
只会让 `dynamic_system` 多出一段常量文本白付钱。
它是给 Daily Planner 那种「一天问一次今天怎么样」的场景准备的。

## ttl 到当天结束

架构文档的缓存策略表写「health: 当天」。快捷指令通常早上 8 点传昨天的数据，
所以同一天内拉一次就够。这里用 6 小时 —— 比「到零点」简单，
而且她要是白天手动补传一次，六小时内也能刷到。

## 报数据必须带日期

`get_latest_health` 返回的是**最近一条**，不一定是昨天 ——
她哪天没同步，那条可能是前天的。所以 render 里一定带上日期，
让他按日期说话，别张口就是「你昨晚」。
（这也是昨天把工具从 `get_yesterday_health` 改名的原因。）

## 不做医疗判断

`check_health_warnings` 那几个阈值（静息心率 >80、HRV <25、睡眠 <360 分钟）
是**通用人群**的，不是糖糖的基线。这里只把数字如实摆出来，
让他自己看着说话，**不把「异常」两个字塞给他**。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from personality.mood import now_cst
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)


def _fmt_minutes(m: float | None) -> str:
    """分钟数变成人话。不满一小时就不写「0 小时」——
    深睡 43 分钟显示成「0 小时 43 分」读着别扭，他也会照着念出来。"""
    if not m:
        return ""
    h, mm = int(m) // 60, int(m) % 60
    if h and mm:
        return "%d 小时 %d 分" % (h, mm)
    if h:
        return "%d 小时" % h
    return "%d 分" % mm


class HealthProvider(BaseContextProvider):
    """睡眠 / 步数 / 心率 / 经期。只读。"""

    name = "health"
    section = "user"          # World State 里挂在 user.health 下
    ttl = timedelta(hours=6)

    def __init__(self, client: McpClient, world_ref: Any = None, **kw: Any) -> None:
        #: 取 World Model 的**函数**（不是值）—— 它在 `_build_attention` 里
        #: 才造出来，那时候本 Provider 早就注册完了。
        #: 给 None 就退回读 health-mcp（那条已经停更，只当兜底）
        self._world_ref = world_ref
        super().__init__(**kw)
        self.client = client

    def _cycle(self) -> dict[str, Any]:
        """周期状态。**优先 World Model**（2026-08-19 起的唯一真源）。

        原来这里调 health-mcp 的 `get_menstrual_cycle`，它读 `health.db`
        的 menstrual 表 —— 那张表被快捷指令写坏过（`flow_level` 是一堆换行）
        而且停更。写入侧当天已经切到 World Model，读这边不跟着切的话，
        **她在 App 里记的东西，他在对话里永远看不见**。

        算法只用 `start` 事件，和 App 那张卡片一致：
        周期长度 = 最近两次 start 的间隔。**只有一次就说不知道，不拿 28 顶** ——
        那是「一般女性」的数，不是她的（她实测 26 天）。
        """
        world = self._world_ref() if self._world_ref else None
        if world is None:
            return {}
        try:
            rows = world.query("menstrual", days=400, limit=200)
        except Exception:  # noqa: BLE001
            logger.warning("读经期失败，这轮不给他看", exc_info=True)
            return {}

        starts = sorted(
            (r.raw or {}).get("date") for r in rows
            if (r.raw or {}).get("event") == "start" and (r.raw or {}).get("date")
        )
        if not starts:
            return {}

        today = now_cst().date()
        last = date.fromisoformat(starts[-1])
        out: dict[str, Any] = {"今天周期第几天": (today - last).days + 1}

        if len(starts) >= 2:
            span = (last - date.fromisoformat(starts[-2])).days
            nxt = last + timedelta(days=span)
            out["平均周期"] = f"{span} 天"
            out["预计下次"] = nxt.isoformat()
            out["距离下次"] = f"{(nxt - today).days} 天"
        return out

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        r = self.client.call("get_latest_health", {})
        if not r.ok:
            raise RuntimeError(f"读健康数据失败: {r.error}")

        text = (r.text or "").strip()
        if not text or text.startswith("暂无"):
            return {"has_data": False, "note": text or "暂无数据"}

        try:
            d = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"健康数据不是合法 JSON: {exc}") from exc

        mc = self._cycle()
        d["_menstrual"] = mc

        return {
            "has_data": True,
            # 一条记录里两个日期语义，别混（见模块开头）：
            #   date       活动数据的自然日（昨天）
            #   sleep_date 这一觉的归属日（Apple 按醒来那天算，通常是今天）
            "date": d.get("date"),
            "sleep_date": d.get("sleep_date"),
            "steps": d.get("steps"),
            "distance_km": d.get("distance_km"),
            "sleep_min": d.get("sleep_duration_min"),
            "deep_sleep_min": d.get("deep_sleep_min"),
            "rem_sleep_min": d.get("rem_sleep_min"),
            "awake_min": d.get("awake_min"),
            "resting_hr": d.get("resting_heart_rate"),
            "hrv_ms": d.get("hrv_ms"),
            "_menstrual": mc,
        }

    def render(self, state: dict[str, Any]) -> str:
        """睡眠和活动**分两句说**，各带各的日期。

        它们差一天是正常的：活动是昨天一整天的，睡眠是昨晚（Apple 归到醒来那天）。
        揉成一句挂同一个日期，他就会说「你 8-2 睡了 X」而那其实是 8-1 晚上那觉。
        """
        if state.get("available") is False or not state.get("has_data"):
            return ""

        stale = "（缓存）" if state.get("stale") else ""
        lines: list[str] = []

        # ---- 睡眠：按 sleep_date 说 ----
        sleep = _fmt_minutes(state.get("sleep_min"))
        if sleep:
            extra = []
            deep = _fmt_minutes(state.get("deep_sleep_min"))
            awake = _fmt_minutes(state.get("awake_min"))
            if deep:
                extra.append(f"深睡 {deep}")
            if awake:
                extra.append(f"中间醒了 {awake}")
            tail = "（" + "，".join(extra) + "）" if extra else ""
            sd = state.get("sleep_date")
            if sd:
                lines.append(f"【睡眠｜{sd} 早上醒的那觉{stale}】{sleep}{tail}。")
            else:
                # 老数据没有 sleep_date。**明说不确定**，别让他当成昨晚的讲出去
                lines.append(
                    f"【睡眠｜{state.get('date') or '日期不明'} 之前的某一觉{stale}】"
                    f"{sleep}{tail}。这条老数据没记归属日，不确定是不是昨晚。"
                )

        # ---- 活动：按 date 说 ----
        acts: list[str] = []
        if state.get("steps") is not None:
            acts.append(f"走了 {state['steps']} 步")
        if state.get("distance_km"):
            acts.append(f"{state['distance_km']:.1f} 公里")
        if state.get("resting_hr"):
            acts.append(f"静息心率 {state['resting_hr']:.0f}")
        if state.get("hrv_ms"):
            acts.append(f"HRV {state['hrv_ms']:.0f} ms")
        if acts:
            lines.append(f"【活动｜{state.get('date') or '日期不明'} 一整天】{'，'.join(acts)}。")

        # ---- 经期：从 World Model 拿（见 _cycle）----
        mc = state.get("_menstrual", {})
        day = mc.get("今天周期第几天")
        if day is not None:
            # ⚠️ 后半截**只有算得出来才拼**。只有一次 start 时 `平均周期` /
            # `预计下次` 都不在 dict 里，无脑拼进去会渲染成
            # 「平均 ｜预计下次 （ 后）」—— 他会照着这段空壳说话
            tail = ""
            if mc.get("平均周期"):
                tail = (f"｜平均 {mc['平均周期']}｜预计下次 {mc['预计下次']}"
                        f"（还有 {mc['距离下次']}）")
            else:
                tail = "｜只有一次记录，还算不出周期长度"
            lines.append(f"【经期｜周期第 {day} 天{tail}】")

        # 只摆数字，不下判断 —— 阈值是通用人群的，不是她的基线
        return "\n".join(lines)
