"""小课堂并入目标层（2026-10-24，《Caelum-小课堂并入目标层-设计稿》）。

她 10-24 定：**学习目标是目标，小课堂是推进它的方式。**

    目标本身        bridge `/api/goals`（标题、她的原话、计划、进展……）—— 照旧
    课程线          这里 `lessons.db` 的 lines：节奏、她现在在哪、接下来想讲什么
    讲过的卡        这里 `lessons.db` 的 cards：每天一张，讲没讲、她回没回

第一步（她 10-24「先做后端，讲过的先在你那边记着，页面下一步再做」）不动 bridge：
课程线按 goal_id 挂在这边，页面那一步再接出去。

`NOX_LESSONS=off` 退回旧小课堂（星期几轮主题、独立通道）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_stores: dict[str, object] = {}
_lock = threading.Lock()


def enabled() -> bool:
    """每次现读（同 pressure.enabled）。"""
    return os.getenv("NOX_LESSONS", "on").strip().lower() not in ("off", "0", "false")


def store_for(db_path: str | Path):
    """和 sessions.db 同目录的 lessons.db。同一个进程里只开一份（Core 的工具、上下文和 Attention 共用）。"""
    from lessons.store import LessonStore
    path = str(Path(db_path).parent / "lessons.db")
    with _lock:
        if path not in _stores:
            _stores[path] = LessonStore(path)
        return _stores[path]
