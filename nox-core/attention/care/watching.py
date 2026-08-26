"""她在不在看片 —— Care 的一条硬抑制。

## 为什么这条要单独存在

共影（`co-watching`）2026-08-21 上线之后有两天是**一座孤岛**：播放器在前端，
Care 在 Core，两边谁也不知道谁。于是会出现最难受的一种打断 ——
她正看到高潮，他在旁边冒一句「今天走了多少步呀」。

我们在第六节花了两天设计抑制器（`tension_level > 0.8` 就闭嘴），
但那套的前提是他知道她在看片。**最该被抑制的场景，感知是瞎的。**

这个文件就是那根线：bridge 的 `/api/watch/state` ← 播放器每 30 秒的心跳。

## 拦的是所有线，不只是新链

放在 `_decide` 的最前面（`ready()` 之后），所以**续链的追问也拦**。
理由很直白：看片时被追问「运动做了没」，和被随便惦记一句，一样烦。

## ⚠️ 读不到的时候**放行**，不是拦

和 `asleep` 同一个口径（糖糖 2026-08-18：不知道就宁可说）。
反过来做的话，bridge 抖一下他就整晚闭嘴了 —— 而且**看不出来**：
日志里只有一片安静，和「他今天没什么想说的」长得一模一样。

沉默是最难排查的故障，不许让它成为默认。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: 问一次 bridge 之后缓存多久。快循环 60 秒一轮，一轮里可能判好几个念头，
#: 不缓存的话一轮打好几次 HTTP
DEFAULT_TTL_S = 20


class WatchingCheck:
    """她在不在看片。返回拦截理由，空字符串 = 放行。

    形状和 `gate_check` 一致（返回理由字符串），这样 orchestrator 那边
    两种拦截读起来是一回事。
    """

    def __init__(self, client: Any, ttl_s: int = DEFAULT_TTL_S) -> None:
        #: 指向 bridge 的 RestClient（`core.bridge`，已带 X-Nox-Token）
        self.client = client
        self.ttl = timedelta(seconds=ttl_s)
        self._cached: str = ""
        self._at: datetime | None = None

    def __call__(self, now: datetime) -> str:
        if self._at is not None and now - self._at < self.ttl:
            return self._cached
        self._cached = self._fetch()
        self._at = now
        return self._cached

    def _fetch(self) -> str:
        try:
            r = self.client.get("/api/watch/state")
        except Exception:  # noqa: BLE001
            logger.warning("读不到观影状态，这轮不拦他", exc_info=True)
            return ""
        if not r.ok:
            # 读不到 ≠ 她在看片。放行（见模块开头）
            logger.warning("读不到观影状态，这轮不拦他：%s", r.error)
            return ""

        data = r.data or {}
        if not data.get("watching"):
            return ""

        s = data.get("session") or {}
        title = str(s.get("title") or "").strip()
        ep = str(s.get("episode") or "").strip()
        name = f"《{title}》{ep}".strip() if title else "一部片子"
        return f"她在看{name}，别打断"
