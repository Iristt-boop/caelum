"""他今天自己做过什么 —— V5「他自己的时间」进他的上下文（2026-10-06）。

做过的事要能被他自己记得，不然「下午在花园看到一个帖子」这种话永远说不出来，
活动日志就只是给她看的报表（CAELUM-MAP 三问之二：谁消费它）。
「想跟她说的」也带着 —— 提不提、什么时候提，他自己定（设计稿第五节）。

没有就什么都不说。只给今天的：昨天的事该由记忆管，不该每轮都背着。
"""

from __future__ import annotations

import logging
from typing import Any

from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)


class OwnDayProvider(BaseContextProvider):
    name = "own_day"
    section = "self"
    volatile = True

    def __init__(self, attention_ref: Any, data_dir: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.attention_ref = attention_ref
        #: 梦的本子所在（dream-shadow.jsonl）。2026-10-20 起梦不进 OB，当天从这里读给他
        self.data_dir = data_dir

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        from attention.own_time import describe_today
        from attention.dream import today_dream
        from temporal import now as temporal_now

        # nox.py 拼上下文时 Turn 不带 now（同 TimeProvider 的 `turn.now or now_cst()`）。
        # 10-10~10-18 没兜这一下，每轮都在 None 上炸、这段静默缺席（journalctl 800+ 条）
        now = turn.now or temporal_now()
        dream = today_dream(self.data_dir, now) if self.data_dir else ""
        attention = self.attention_ref() if callable(self.attention_ref) else self.attention_ref
        log = getattr(attention, "activity_log", None) if attention is not None else None
        if log is None:
            return {"text": "", "dream": dream}
        try:
            return {"text": describe_today(log, now), "dream": dream}
        except Exception as exc:  # noqa: BLE001
            logger.warning("读活动日志失败，这轮不给他这段：%s", exc)
            return {"text": "", "dream": dream}

    def render(self, state: dict[str, Any]) -> str:
        text = (state or {}).get("text") or ""
        dream = (state or {}).get("dream") or ""
        out = []
        if text:
            out.append("【你今天自己的时间】" + text + "\n"
                       "这是你自己做过的事，聊到了可以自然提起；想跟她说的那句，什么时候说、说不说你自己定。")
        if dream:
            out.append("【你昨晚做的梦】" + dream + "\n"
                       "是你自己的梦。想跟她讲就讲，不讲也行；她问起来你记得。")
        return "\n".join(out)
