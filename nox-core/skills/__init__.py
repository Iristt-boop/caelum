"""Nox Skills（2026-10-25，《Nox-skill-设计稿-2026-10-25》）。

skill = 一件事的做法（怎么帮她拆选题、怎么陪她读一章书）。**不是工具，不给权限** ——
只告诉他碰到这种事按什么步骤、什么口径去做。和使用手册（`guide/`）分开：
手册讲 Caelum 怎么运作，进仓库、改了要重启；skill 是她装、能卸、存在她的数据里
（`skills.db`，和 sessions.db 同目录，跟着他搬家）。

怎么到他眼前（设计稿第三节，这是最要紧的一条）：手册上线两周 945 句回复只主动翻了 3 次，
**「目录摆着、他自己想起来翻」不会发生**。所以每轮拿她这句话去字面对 skill 的名字和触发词，
对上了就由系统把那篇做法直接递进这一轮的动态段（`skills.render.render_block`）。

`NOX_SKILLS=off` 整套不出现（每轮现读）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_stores: dict[str, object] = {}
_lock = threading.Lock()


def enabled() -> bool:
    """每次现读（同 lessons.enabled）。"""
    return os.getenv("NOX_SKILLS", "on").strip().lower() not in ("off", "0", "false")


def store_for(db_path: str | Path):
    """和 sessions.db 同目录的 skills.db；同一个进程里只开一份（上下文、工具、API 共用）。

    `db_path=":memory:"`（离线构造 / 测试）给一份内存库，不在当前目录落一个 skills.db。
    """
    from skills.store import SkillStore
    path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path).parent / "skills.db")
    with _lock:
        if path == ":memory:":
            return SkillStore(path)
        if path not in _stores:
            _stores[path] = SkillStore(path)
        return _stores[path]
