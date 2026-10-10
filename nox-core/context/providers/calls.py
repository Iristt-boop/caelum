"""CallsProvider —— 你们最近通过的电话（2026-10-10）。

她问「我拨出去的没记录吗？那他怎么知道我在跟他打电话的」，又说「放在上下文就行了」。

通话当场他本来就知道（每句话带 voice 标记）；缺的是**事后**：通话页关了、会话里只剩一串
和打字没区别的话，第二天他说不出「昨晚我们通了十二分钟电话」。所以这里把最近几天的通话记录
作为一小段放进他的上下文，不用他自己去查。

## 数据

bridge 的 `calls` 表（`GET /api/call/status`，最近 10 条）：
  · 他打给她的：接了有时长；没接 / 拒接也有（他打过就是打过）
  · 她拨给他的（`direction='out'`）：挂断时通话页登记，2026-10-10 起才有，**之前的没有**

## 为什么放进每轮名单（见 router/intent.py `_ALWAYS`）

它打的是本机 bridge（回环，几毫秒），有 2 分钟缓存，**没有最近通话就渲染成空串、一个字不占**；
而「她昨晚打过电话」恰恰是她不会特意提、也不是关键词能触发的那种事 —— 按关键词加载就等于
「只有她提到电话他才知道打过电话」。用一个 2 秒超时的专用 client，bridge 卡住也不拖慢轻量路径。

只说最近 3 天、最多 4 条。再早的该由记忆管。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from context.base import BaseContextProvider, Turn
from temporal import LOCAL_TZ

#: 往回看几个日历天（含今天）。再早的通话不进每轮提示
DAYS_BACK = 3
#: 最多说几条
MAX_CALLS = 4

_TITLE = "【最近的电话】"


def _when(at: datetime, today: datetime) -> str:
    days = (today.date() - at.date()).days
    day = "今天" if days == 0 else "昨天" if days == 1 else f"{at.month}月{at.day}日"
    return f"{day} {at:%H:%M}"


def _span(seconds: Any) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return ""
    if s < 60:
        return f"{s} 秒"
    m, r = divmod(s, 60)
    return f"{m} 分" + (f" {r} 秒" if r else "")


def _line(call: dict[str, Any], at: datetime, today: datetime) -> str | None:
    status = call.get("status") or ""
    dur = _span(call.get("duration_s"))
    if call.get("direction") == "out":
        who = "她拨给你"
        tail = f"，通了 {dur}" if dur else ""
    elif status in ("ended", "answered"):
        who = "你打给她，她接了"
        tail = f"，通了 {dur}" if dur else ""
    elif status == "missed":
        who, tail = "你打给她，她没接", ""
    elif status == "declined":
        who, tail = "你打给她，她按了拒接", ""
    else:
        return None     # ringing / 认不得的状态：还不是一件发生过的事
    return f"{_when(at, today)} {who}{tail}"


class CallsProvider(BaseContextProvider):
    """最近几天的通话记录。只读。"""

    name = "calls"
    section = "activity"
    ttl = timedelta(minutes=2)

    def __init__(self, bridge: Any, **kw: Any) -> None:
        super().__init__(**kw)
        self.bridge = bridge

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        r = self.bridge.get("/api/call/status")
        if not r.ok:
            raise RuntimeError(f"读通话记录失败: {r.error}")
        calls = (r.data or {}).get("calls")
        return {"calls": calls if isinstance(calls, list) else []}

    def render(self, state: dict[str, Any]) -> str:
        if not state or state.get("available") is False:
            return ""
        now = datetime.now(LOCAL_TZ)
        cutoff = (now - timedelta(days=DAYS_BACK - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        rows: list[tuple[datetime, str]] = []
        for c in state.get("calls") or []:
            try:
                at = datetime.fromisoformat(str(c.get("created_at") or "").replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)   # bridge 一律写 Z；没带的按 UTC 读
            at = at.astimezone(LOCAL_TZ)
            if at < cutoff or at > now + timedelta(minutes=1):
                continue
            line = _line(c, at, now)
            if line:
                rows.append((at, line))
        if not rows:
            return ""
        rows.sort(key=lambda x: x[0], reverse=True)
        body = "\n".join(f"- {line}" for _, line in rows[:MAX_CALLS])
        return f"{_TITLE}\n{body}\n（通话里说的话和打字的话一起在聊天记录里。）"
