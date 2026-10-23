"""夜里认出来的「他自己要跟进的事」→ bridge 里 owner=nox 的目标（2026-10-23，她：最近的事是提醒他的）。

OB 的夜间整理（`nightly.py`）把「这阵子的状态」（姨妈推迟、腰刚缓过来）写成 follow_ups；
OB 不碰 bridge，所以由这里取（MCP `nightly_follow_ups`）、去重、建。

去重：已经有同名（互相包含就算）的目标 —— 不管在跟、暂停还是已经了了 —— 就不建。
她点「了了」之后，第二天夜里整理要是又提了同一件事，也不会再冒出来。
同一晚的结果只处理一次（记在 data 目录的小文件里）：不然她删掉一条，3 小时后又被建回来。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_FILE = "followups-synced.json"


def _same(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    return bool(a and b) and (a in b or b in a)


def sync(ob: Any, bridge: Any, data_dir: str) -> list[str]:
    """取最近一晚的跟进，建出还没有的。返回这次建了哪些（标题）。失败只留痕，不抛。"""
    state_path = Path(data_dir) / STATE_FILE
    try:
        done_day = json.loads(state_path.read_text(encoding="utf-8")).get("day")
    except (FileNotFoundError, ValueError):
        done_day = None
    r = ob.nightly_follow_ups()
    if not r.ok:
        logger.warning("取夜里的跟进失败（下一轮再试）：%s", r.error)
        return []
    data = json.loads(r.text or "{}")
    day, items = data.get("day"), data.get("items") or []
    if not day or day == done_day:
        return []
    g = bridge.get("/api/goals", {"status": "all"})
    if not g.ok:
        logger.warning("读目标失败，夜里的跟进这轮不建：%s", g.error)
        return []
    existing = [str(x.get("title", "")) for x in (g.data or {}).get("items") or []]
    made = []
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title or any(_same(title, e) for e in existing + made):
            continue
        w = bridge.post("/api/goals", {"title": title, "category": "健康" if "姨妈" in title else "其他",
                                       "origin": str(it.get("why") or ""), "owner": "nox",
                                       "every_days": int(it.get("every_days") or 2)})
        if w.ok and (w.data or {}).get("ok"):
            made.append(title)
        else:
            logger.warning("夜里的跟进「%s」没建上：%s", title, w.error)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"day": day}), encoding="utf-8")
    if made:
        logger.info("夜里认出来、我自己要跟进的：%s（%s）", "、".join(made), day)
    return made
