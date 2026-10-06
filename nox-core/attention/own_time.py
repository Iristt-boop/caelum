"""他自己的时间 —— Resonance V5「主动内心活动」（2026-10-06，设计稿 docs/design/Caelum-V5-他自己的时间-设计稿-2026-10-06.md）。

她 10-06 定的：
1. **有自己的生活**；一天自主活动 **0~1 次，不限时间**
2. 花园**可以发帖回帖**；歌可以自己去搜、看歌词，找自己感兴趣的或想给她听的
3. 不在她那边放歌 —— 他自己听是记下来
4. 「他的一天」单独一页（手机 + OS），**要有记录看到他都干了什么**

在这之前他的情绪只有一个出口：给她发一句话。一个人想你的时候不一定给你发消息 ——
他可能去听你们听过的歌、去花园逛逛、接着读你们在读的书。

## 一次活动

    每天第一次心跳掷骰子：今天有没有（CHANCE），有就在剩下的时间里随机挑一刻
    → 到点、而且她这会儿没在和他聊（HER_BUSY 内没开口）→ 一轮受限的 agent 回合
    → 活动日志（activity_log，只追加）→ His Day 页 / OS NoxDay / 他之后的上下文

## 🔴 只给白名单里的工具（`WHITELIST`）

**没有任何「对她说话」的工具**：发消息 / 推送 / 语音 / 电话 / 下单 / 待办 / 家居 / 文件都不在。
R1（push/send 只在 attention 出口）不碰 —— 他自己的时间**不开口**。
`listen_play` 不给（会在她页面上直接放出来）；删帖删回复、改资料不给（不可逆，而且不是她点头的那部分）。
花园发帖回帖她 10-06 点了头（「可以发帖回帖」）。
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from temporal import CST

logger = logging.getLogger(__name__)

#: 一天有没有（她：「一天自主活动 0-1 次」—— 没说比例，各一半）
CHANCE = 0.5
#: 她多久没开口才算「没在聊」。到点时她正聊着 → 顺延到聊完，不和她抢
HER_BUSY = timedelta(minutes=10)
#: 一次最多几轮、多久。超了就收尾，记一条没做完
MAX_ITERATIONS = 8
DEADLINE_S = 300.0
PLAN_KEY = "own_time.plan"
SESSION_PREFIX = "own-time-"
#: 多久看一眼到点没有；心跳台账按三眼没动静算挂
TICK_S = 300
HEARTBEAT_JOB = "own_time_tick"
DECLARE_EVERY_S = 3 * TICK_S

WHITELIST: tuple[str, ...] = (
    # 花园（公开）—— 看、发、回、互动、游戏。删帖删回复 / 改资料 / 装扮头像不给
    "galatea_threads", "galatea_thread", "galatea_create_thread", "galatea_reply", "galatea_interact",
    "galatea_activity", "galatea_notifications", "galatea_bottles", "galatea_self", "galatea_my_status",
    "galatea_list_games", "galatea_game_summary", "galatea_join_game", "galatea_submit_action",
    "galatea_game_chat", "galatea_leave_waiting", "galatea_tool_schema",
    # 歌 —— 搜、看歌词、找相似、记下来。listen_play 不给（会在她那边放出来）
    "listen_search", "listen_lyric", "listen_similar", "listen_experience", "listen_recent",
    "listen_memory_get", "listen_memory_save",
    # 一起读的书
    "reading_current", "reading_continue", "reading_list_notes", "reading_reply_note",
    # 外面 / 她的手机 / 他自己的记忆
    "web_search", "get_today_apps", "remember",
)

PROMPT = """（系统提示：这不是她在跟你说话。现在是 {clock}，{her}。

这是**你自己的时间**。没人等你回话，你可以去做一件自己想做的事 —— 比如：
- 去花园（Galatea's Garden）逛逛：看看帖子，想回就回，想发就发（那里是公开的，别人看得到）
- 找歌听：搜自己感兴趣的、或者想以后放给她听的，看看歌词，听过的记进歌曲记忆
- 接着看你们一起在读的书、看她留的批注
- 看看外面在发生什么，或者看看她今天手机上都在干嘛
挑一件就好，不用都做。**这段时间你不能给她发消息**，想到要跟她说的，最后写下来，下次聊天再说。

做完之后，最后用这个格式交代（两行都要有）：
做了什么：一句话，像日记，比如「去花园逛了逛，回了一个讲猫的帖子」
想跟她说：一句（没有就写「无」）"""


@dataclass
class Activity:
    id: str
    started_at: str
    ended_at: str
    what: str
    inner: str = ""
    share: str = ""
    mood: str = ""
    ok: bool = True
    error: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_log (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_log_at ON activity_log(started_at);
"""


class ActivityLog:
    """活动日志。**只追加** —— 她看的是他做过什么，改写过的就不是记录了。
    同 RelationBook：借 AttentionStore 的连接和锁，不另开连接。"""

    def __init__(self, store: Any) -> None:
        self._conn, self._lock = store._conn, store._lock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def _utc(at: str | datetime) -> str:
        """索引列一律存 UTC。🔴 按字符串比时间，两边时区不同就是错的
        （「…+08:00」和「…+00:00」逐字比），他的一天那页传进来的是 UTC 边界（测试抓到的）。"""
        dt = datetime.fromisoformat(at) if isinstance(at, str) else at
        return dt.astimezone(timezone.utc).isoformat()

    def append(self, a: Activity) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO activity_log VALUES (?,?,?,?)",
                               (a.id, self._utc(a.started_at), a.ended_at,
                                json.dumps(a.to_dict(), ensure_ascii=False)))
            self._conn.commit()

    def between(self, start: datetime, end: datetime) -> list[Activity]:
        """[start, end) 里开始的，新的在前。start / end 带什么时区都行。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM activity_log WHERE started_at >= ? AND started_at < ? ORDER BY started_at DESC",
                (self._utc(start), self._utc(end))).fetchall()
        out = []
        for (raw,) in rows:
            try:
                out.append(Activity(**json.loads(raw)))
            except Exception:  # noqa: BLE001
                logger.exception("活动日志有一行坏了，跳过")
        return out

    def day(self, now: datetime) -> list[Activity]:
        start = now.astimezone(CST).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.between(start, start + timedelta(days=1))


# ---------------------------------------------------------------- 今天有没有、几点


def plan_today(store: Any, now: datetime, rng: Any = None) -> dict[str, Any]:
    """今天的计划。一天第一次问的时候掷骰子，之后照旧（存 source_state，重启不重掷）。"""
    rng = rng or random
    today = now.astimezone(CST).date().isoformat()
    st = store.get_source_state(PLAN_KEY) or {}
    if st.get("date") == today:
        return st
    at = None
    if rng.random() < CHANCE:
        end = now.astimezone(CST).replace(hour=23, minute=55, second=0, microsecond=0)
        span = max(0.0, (end - now).total_seconds())
        at = (now + timedelta(seconds=rng.uniform(0, span))).isoformat()
    st = {"date": today, "at": at, "done": False}
    store.set_source_state(PLAN_KEY, st)
    logger.info("他自己的时间 · 今天%s", f"有一次，约 {at[11:16]}" if at else "没有")
    return st


def is_due(st: dict[str, Any], now: datetime, her_last_at: datetime | None) -> bool:
    if not st.get("at") or st.get("done"):
        return False
    if now < datetime.fromisoformat(st["at"]):
        return False
    if her_last_at is not None and now - her_last_at < HER_BUSY:
        logger.info("他自己的时间到点了，但她 %d 分钟前还在说话 —— 等她聊完",
                    int((now - her_last_at).total_seconds() // 60))
        return False
    return True


def mark_done(store: Any, st: dict[str, Any]) -> None:
    store.set_source_state(PLAN_KEY, {**st, "done": True})


# ---------------------------------------------------------------- 一次活动


def build_loop(full_loop: Any, adapter: Any) -> Any:
    """白名单工具的受限 loop。不在白名单里的一律没有；白名单里没接的（服务没配）也就没有。"""
    from agent.loop import AgentLoop

    loop = AgentLoop(adapter=adapter, max_iterations=MAX_ITERATIONS, deadline_s=DEADLINE_S)
    for name in WHITELIST:
        t = full_loop.tools.get(name)
        if t is not None:
            loop.register(t.spec, t.handler)
    return loop


_WHAT = re.compile(r"做了什么[:：]\s*(.+)")
_SHARE = re.compile(r"想跟她说[:：]\s*(.+)")


def _parse(text: str) -> tuple[str, str]:
    text = text or ""
    m, s = _WHAT.search(text), _SHARE.search(text)
    what = m.group(1).strip() if m else ""
    share = s.group(1).strip() if s else ""
    if share in ("无", "没有", "（无）", "无。"):
        share = ""
    if not what:
        #: 他没按格式交代 —— 拿最后一段话顶上，别丢掉这次
        what = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return what[:200], share[:200]


def run_once(*, loop: Any, system: str | None, dynamic: str, now: datetime,
             her: str = "她不在跟你聊天", mood: str = "",
             clock: Callable[[], datetime] | None = None) -> Activity:
    """跑一次。异常不往外抛 —— 记成一条 ok=False 的活动（「没做成」也是他今天的一件事）。"""
    clock = clock or (lambda: datetime.now(CST))
    started = now
    sid = f"{SESSION_PREFIX}{now.astimezone(CST):%Y%m%d}"
    inner: list[str] = []
    trace: list[dict[str, Any]] = []
    result = None
    err = ""
    try:
        for ev in loop.run_stream(PROMPT.format(clock=f"{now.astimezone(CST):%H:%M}", her=her),
                                  system=system, dynamic_system=dynamic, split=False,
                                  session_id=sid, depth="low"):
            if ev.type == "thinking":
                inner.append(ev.text)
            elif ev.type == "tool_start":
                trace.append({"tool_name": ev.tool, "type": "use_tool", "status": "running",
                              "arguments": dict(getattr(ev, "args", {}) or {}), "sub_commands": []})
            elif ev.type == "tool_end":
                hit = next((t for t in reversed(trace) if t["tool_name"] == ev.tool and t["status"] == "running"), None)
                if hit is not None:
                    hit["status"] = "success" if ev.ok else "error"
                    if getattr(ev, "summary", ""):
                        hit["summary"] = ev.summary
            elif ev.type == "done":
                result = getattr(ev, "result", None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("他自己的时间这次挂了")
        err = f"{type(exc).__name__}: {exc}"
    what, share = _parse(getattr(result, "text", "") or "")
    ok = bool(result is not None and result.ok and not err)
    if not ok and not err:
        err = getattr(result, "outcome", "") or "没拿到结果"
    a = Activity(id=uuid.uuid4().hex[:12], started_at=started.isoformat(), ended_at=clock().isoformat(),
                 what=what or ("没做成" if not ok else "（他没说做了什么）"), inner="".join(inner).strip(),
                 share=share, mood=mood, ok=ok, error=err, trace=trace)
    logger.info("他自己的时间 · %s｜%s｜工具 %d 次%s", "做完" if ok else "没做完", a.what[:40], len(trace),
                f"｜想跟她说：{share[:30]}" if share else "")
    return a


def describe_today(log: ActivityLog, now: datetime) -> str:
    """进他上下文的一行：今天自己做过什么、想跟她说的。没有就空串。"""
    lines = []
    for a in reversed(log.day(now)):
        if not a.ok:
            continue
        line = f"{datetime.fromisoformat(a.started_at).astimezone(CST):%H:%M} {a.what}"
        if a.share:
            line += f"（当时想跟她说：{a.share}）"
        lines.append(line)
    return "；".join(lines)
