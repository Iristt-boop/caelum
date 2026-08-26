"""MusicProvider —— 她现在在听什么。

数据来自 eryu 的 `/music/recent`（前端每次开始播放都会上报）。

## ⚠️ ttl 只有 3 分钟

和 health（6 小时）不一样：**「她正在听什么」是会变的**。
缓存久了就会出现「他说你在听 A，其实你早换成 B 了」——
那比不知道更糟。

3 分钟是个折中：一首歌平均 3-4 分钟，所以同一首歌里基本只拉一次；
换了歌下一轮就能刷到。

## ⚠️ 不进每轮名单

和 health / memory 同理，靠 Router 按需加载。
不是每句话都跟音乐有关，每轮都塞进 `dynamic_system` 是白付钱
（那段在缓存断点之后，每个字都按未命中价算）。

Router 该在什么时候拉它：她提到歌、音乐、在听什么，
或者聊天气氛需要知道她此刻的状态时。

## 只说「正在听」，不说「听过什么」

历史记录是 `eryu_experience` 工具的事 —— 那是他主动去查的。
Provider 只回答「此刻」，一句话，别把上下文撑大。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from context.base import BaseContextProvider, Turn
from tools.http import RestClient

logger = logging.getLogger(__name__)

# 超过这个时间就不算「正在听」了 —— 一首歌再长也就十分钟
STALE_AFTER = timedelta(minutes=12)


class MusicProvider(BaseContextProvider):
    """她此刻在听什么。只读。"""

    name = "music"
    section = "activity"      # World State 里挂在 activity.music 下
    ttl = timedelta(minutes=3)

    def __init__(self, client: RestClient, **kw: Any) -> None:
        super().__init__(**kw)
        self.client = client

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        r = self.client.get("/music/recent")
        if not r.ok:
            raise RuntimeError(f"读最近播放失败: {r.error}")

        songs = (r.data or {}).get("songs") or []
        if not songs:
            return {"playing": False}

        latest = songs[0]
        played_at = str(latest.get("playedAt") or "")
        when = None
        if played_at:
            try:
                when = datetime.fromisoformat(played_at)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except ValueError:
                when = None

        # 太久之前的就不算「正在听」。宁可说不知道，也别说错 ——
        # 「他说你在听 X」而你三小时前就关了，比不知道更糟
        fresh = bool(when and (datetime.now(timezone.utc) - when) < STALE_AFTER)

        return {
            "playing": fresh,
            "song_id": str(latest.get("songId") or ""),
            "name": latest.get("name") or "",
            "artist": latest.get("artist") or "",
            "played_at": played_at,
            "minutes_ago": (
                int((datetime.now(timezone.utc) - when).total_seconds() / 60)
                if when else None
            ),
        }

    def render(self, state: dict[str, Any]) -> str:
        """一句话。**不要铺开**，这段每个字都按未命中价付费。"""
        if state.get("available") is False:
            return ""
        if not state.get("name"):
            return ""

        who = f"{state['name']}"
        if state.get("artist"):
            who += f" - {state['artist']}"
        stale = "（缓存）" if state.get("stale") else ""

        if state.get("playing"):
            return f"【正在听{stale}】{who}"

        # 不是正在听，但最近听过 —— 说清楚是「刚才」不是「现在」，
        # 免得他张口就说「你正在听的这首」
        mins = state.get("minutes_ago")
        if mins is None:
            return ""
        if mins < 60:
            ago = f"{mins} 分钟前"
        elif mins < 60 * 24:
            ago = f"{mins // 60} 小时前"
        else:
            return ""      # 隔天的就别提了，那不叫「最近」
        return f"【{ago}听过{stale}】{who}"
