"""TodoSource —— 到点该做的事，变成一个念头。

设计出处：`Todo-Daily-Planner-设计.md` 第二节（糖糖 & Nox，2026-08-17 深夜）：

> 到点没完成 → Nox 主动开口追，不是提醒，是「追」。
> 没做就一直追，直到糖糖说「做完了」→ 划掉 → 翻篇，不再烦她。
> 追的时候结合记忆抓线头，说具体的话，不说「你该做 xxx 了」这种客服腔。

## 它产出的是 TASK 型关心链

这一点是整件事的关键，也是 2026-08-18 那个 bug 教出来的：

    她回话了 → 追问型链关闭（她回了就没什么可追的）
    她回话了 → **任务型链不关**（事情该不该做，跟她回不回话无关）

「到点该运动了」和「到点该关空调」是同一类事。她说一句「在忙」并不代表
运动做了 —— 只有她说「做完了」（→ `/api/todo/complete`）才翻篇。

## 跨天重开（糖糖 2026-08-18 拍板）

「一直追」不是今晚不停地问，而是**明天他还会再来**：

    今天 20:00 命中 → 开一条链 → 链内最多追 3 次 → 收手
    第二天 20:00 再命中 → 重新开一条新链

bridge 用 `fired_on` 记「今天追过了」，追完打一次 `/api/todo/fired`。
跨天自动失效，不用定时清理。

## 数据在 bridge，不在这儿

待办的真源是 bridge 的 SQLite（前端 todo 是唯一活清单，GitHub todo.md
2026-08-18 退役为只读存档）。所以这个 Source 是个 HTTP 客户端。
读不到就当这轮没有念头 —— **不猜、不缓存**：拿旧清单去追人，
会追一件她十分钟前刚划掉的事。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from attention.care.signal import TASK, CareSignal

logger = logging.getLogger(__name__)

#: 一条链内部最多追几次。糖糖 2026-08-18 定 3 ——
#: 「一直追」的语义由跨天重开承担，不是今晚问到底
MAX_CHASE = 3


class TodoDueSource:
    """到点没做的待办。轮询 bridge 的 `/api/todo/due`。"""

    name = "todo"

    def __init__(self, client: Any) -> None:
        #: 指向 bridge 的 RestClient（`core.bridge`，已带 X-Nox-Token）。
        #: 它的方法返回 `RestResult(ok, data, error)`，不是裸 dict
        self.client = client
        #: 待办 id → thread_id，续链用。
        #: 只在内存里 —— Core 重启后一件追到一半的事会重开一条链，
        #: 代价是最多多追几次，不值得为它加一张表
        self._threads: dict[str, str] = {}

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime) -> list[CareSignal]:
        items = self._fetch_due()
        out: list[CareSignal] = []
        for t in items:
            tid = str(t.get("id") or "")
            text = str(t.get("text") or "").strip()
            if not tid or not text:
                continue
            out.append(CareSignal(
                source=self.name,
                subject=text,
                # ⚠️ 必须是 TASK：她回话不代表事做了
                thread_kind=TASK,
                # 到点没做比「随便想起她」急，但不该压过睡眠那种身体信号
                urgency=0.7,
                thread_id=self._threads.get(tid),
                payload={
                    "todo_id": tid,
                    "text": text,
                    "at": t.get("at") or "",
                    "repeat": t.get("repeat") or "",
                },
            ))
        return out

    # ------------------------------------------------------------ 记账

    def remember_thread(self, todo_id: str, thread_id: str) -> None:
        """开了链就记下来，下一轮同一件事续这条链，而不是又开一条。"""
        if todo_id and thread_id:
            self._threads[todo_id] = thread_id

    def mark_fired(self, todo_id: str) -> bool:
        """今天这条追完了。跨天自动失效（bridge 按中国时区的日期比对）。"""
        r = self.client.post("/api/todo/fired", {"id": todo_id})
        if not r.ok:
            # 标记失败只会让他明天之前多追几次，不该让整轮挂掉
            logger.warning("标记待办已追失败：%s（%s）", todo_id, r.error)
        return bool(r.ok)

    # ------------------------------------------------------------ 内部

    def _fetch_due(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/todo/due")
        if not r.ok:
            # 读不到就当没有念头。**不缓存旧清单** ——
            # 拿过期的清单去追人，会追一件她刚划掉的事
            logger.warning("读不到到期待办，这轮跳过：%s", r.error)
            return []
        items = (r.data or {}).get("items")
        return items if isinstance(items, list) else []
