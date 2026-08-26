"""WeatherProvider —— 外面什么天，今天要不要带伞。

## 复用现成的 key，不新申请

和风的 key 和专属域名早就有了，在 `xiaozhi-server` 的 `get_weather` 插件里
（Stack-chan 用它回答「今天天气怎么样」）。这边复用同一把 ——
架构文档第十一节：「WeatherProvider 直接复用这个 host + key 即可，不用重新申请。」

⚠️ host 是 `xxxxx.re.qweatherapi.com` 这种**专属 API 域名**，不是公共域名。
两者限流分开算，架构文档里记的「公共 key 限流过」说的是换到这个之前的事。

## 额度

免费额度 **50000 次/月**（2026-08-03 糖糖确认）。我们的上界：
30 分钟 TTL × 每次刷新调 2 个接口 = **最多 96 次/天 ≈ 2900 次/月**，
再加 Stack-chan 那边几次/天。**余量约 17 倍**，不用为省调用做妥协。

## 两个接口一起调

| 接口 | 给什么 | 为什么需要 |
|---|---|---|
| `/v7/weather/now` | 此刻多少度、什么天、体感 | 「现在外面热不热」|
| `/v7/weather/3d`（取第一天）| 今天最高/最低温、白天天气、降水量 | 「今天穿什么」「要带伞吗」 |

只调 now 的话答不了「今天会不会下雨」——那才是她真正会问的。

## 不进每轮名单

和 home / health 一样，只在 Router 判定「提到天气/出门/穿什么」时才加载。
天气对闲聊没用，每轮塞一段常量文本只是白付钱。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from tools.http import RestClient

logger = logging.getLogger(__name__)


class WeatherProvider(BaseContextProvider):
    """和风天气。只读。"""

    name = "weather"
    section = "environment"       # World State 里挂 environment.weather
    ttl = timedelta(minutes=30)   # 天气不会分钟级变化（架构文档的缓存策略表）

    def __init__(self, host: str, key: str, location: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.key = key
        self.location = location
        # 和风要求 https，host 只给域名
        self.client = RestClient(
            base=f"https://{host.strip().rstrip('/')}",
            timeout=12.0,
            auth_hint="和风 key 无效或额度用尽，去 dev.qweather.com 看用量",
        )

    def _get(self, path: str) -> dict:
        r = self.client.get(path, {"location": self.location, "key": self.key})
        if not r.ok:
            raise RuntimeError(f"和风 {path} 失败: {r.error}")
        d = r.data or {}
        # 和风的错误藏在 body 的 code 里，HTTP 照样 200 ——
        # 只看状态码会把「无效 key」当成拿到了数据（同第二十节：200 ≠ 成功）
        if str(d.get("code")) != "200":
            raise RuntimeError(f"和风返回 code={d.get('code')}（不是 200）")
        return d

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        now = self._get("/v7/weather/now").get("now", {})
        # 预报单独一次调用。失败不致命 —— 有实时的也能说话，
        # 所以这里吞掉异常但记一笔，跟「整个 Provider 挂了」区别对待
        today: dict[str, Any] = {}
        try:
            days = self._get("/v7/weather/3d").get("daily", [])
            today = days[0] if days else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("拿不到今日预报（实时的还在）: %s", exc)

        def num(d: dict, k: str) -> float | None:
            v = d.get(k)
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        return {
            "text": now.get("text"),
            "temp": num(now, "temp"),
            "feels_like": num(now, "feelsLike"),
            "humidity": num(now, "humidity"),
            "wind": (now.get("windDir") or "") + (now.get("windScale") or "") + "级"
                    if now.get("windDir") else None,
            "temp_max": num(today, "tempMax"),
            "temp_min": num(today, "tempMin"),
            "text_day": today.get("textDay"),
            "precip": num(today, "precip"),
            "has_forecast": bool(today),
        }

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False or not state.get("text"):
            return ""

        head = "【外面】"
        if state.get("stale"):
            head = f"【外面（{int(state.get('stale_age_s', 0) // 60)} 分钟前）】"

        bits = [f"{state['text']}"]
        if state.get("temp") is not None:
            t = f"{state['temp']:.0f}°"
            fl = state.get("feels_like")
            # 体感和实测差 2 度以上才提，否则是噪音
            if fl is not None and abs(fl - state["temp"]) >= 2:
                t += f"（体感 {fl:.0f}°）"
            bits.append(t)
        if state.get("temp_min") is not None and state.get("temp_max") is not None:
            bits.append(f"今天 {state['temp_min']:.0f}~{state['temp_max']:.0f}°")
        if state.get("wind"):
            bits.append(state["wind"])

        line = f"{head}{'，'.join(bits)}。"

        # 下雨单独说一句 —— 这是她真正会据此做决定的信息
        precip = state.get("precip")
        day = state.get("text_day") or ""
        if (precip is not None and precip > 0) or any(
                w in day for w in ("雨", "雪", "雷")):
            line += f"今天有{day or '降水'}，出门记得带伞。"

        return line
