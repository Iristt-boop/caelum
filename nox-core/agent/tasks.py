"""长任务 —— 活过单条回复的活（2026-09-22，设计：Nox-长任务循环-v1-设计.md）。

她要的是「帮我把 481 本书理一遍」这种活：AgentLoop 的设计前提是活在
一条回复里（`max_iterations=12` + 聊天 deadline），任务一长它就装不下。
这个模块把「任务」变成一个**一等实体**：立任务 → 她确认 → 后台跑 →
进度随时可查 → 活过重启。

## 三层（照设计文档第三节）

    TaskStore   tasks.db（照 orders.db 先例独立建库：状态天生要改，
                world.db「冻结只追加」的契约装不下它）
    run_task_loop  lifespan 后台循环（照 dream.py 的范式），
                并发 = 1，活过一个任务时也持续 beat 心跳
    tools/tasks.py  对话侧三件套（start_long_task / task_status /
                task_cancel）—— start 只出确认卡，真跑在确认端点后面

## 🔴 边界（设计文档第七节，写死的）

  · 长任务**不是开口**：进度和完成不推送、不占 Care 额度。
    「想让她看见」是 v2 TaskDoneSource 的事，经 Care 决定。
  · proposed → confirmed 是 R8：长任务的破坏力是**累积性**的
    （批量改 481 条数据），启动必须她点头，模型够不到 running。
  · 测试会话（`test-` 前缀）不得立任务（R6，闸在 tools/tasks.py）。

## 断点续跑（v1 策略：agentic resume）

不做工具级幂等/回滚（通用断点是无底洞）。续跑 = 把 progress 日志
作为上下文喂给 AgentLoop，让它自己判断从哪继续 —— 所以 progress
每步都必须是「做成了什么」的一句话，而不是「打算做什么」。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.llm import ToolSpec
from agent.loop import AgentLoop, Tool
from obs import heartbeat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 状态机

#: proposed   工具立的，等她点「跑」（确认卡在前端）
#: confirmed  她点了，排队等 runner（并发 = 1）
#: running    后台 AgentLoop 在跑
#: done / failed / cancelled   终态
#: interrupted  重启时正在跑的 —— 不是终态，「接着跑」会从 progress 续
PROPOSED = "proposed"
CONFIRMED = "confirmed"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

#: 到了就不再变（interrupted 除外 —— 它可以经 confirm 回到 confirmed 续跑）
FINAL = frozenset({DONE, FAILED, CANCELLED})

#: 单任务墙钟预算（秒）。跑不完 → failed，progress 里留着做过的部分，
#: 她可以「接着跑」。30 分钟：比一次部署窗口长，比一夜短。
TASK_DEADLINE_S = float(os.getenv("NOX_TASK_DEADLINE_S", "1800"))

#: 任务循环的轮数上限。默认 AgentLoop 是 12（聊天够用），长任务不够 ——
#: 「理一遍 481 本书」一回合做几本，得上百回合。真正的闸是墙钟，
#: 这个数只是兜底「每轮都很快地打转」的那种。
TASK_MAX_ITERATIONS = int(os.getenv("NOX_TASK_MAX_ITERATIONS", "200"))

#: 心跳。任务跑起来之后 asyncio 循环也不闲着 —— 每 30 秒 beat 一次，
#: 杀掉 runner 最多 3×30s（加 60s 下限的容忍）后 /health 就能看见。
HEARTBEAT_JOB = "task_tick"
BEAT_S = 30.0
#: 没活可干时多久看一眼队（有 confirmed 的任务最多等这么久才开跑）
POLL_S = 5.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  goal        TEXT NOT NULL,
  steps_hint  TEXT,
  status      TEXT NOT NULL,
  result      TEXT,
  created_at  TEXT NOT NULL,
  confirmed_at TEXT,
  started_at  TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS task_progress (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id  TEXT NOT NULL,
  at       TEXT NOT NULL,
  step     TEXT NOT NULL,
  kind     TEXT NOT NULL,
  payload  TEXT
);
CREATE INDEX IF NOT EXISTS idx_progress_task ON task_progress(task_id);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStore:
    """任务实体 + 追加只写的进度日志。

    线程安全同 `orders/store.py`：一把锁串行化写，
    `check_same_thread=False` —— 进度从 runner 的线程池线程写，
    状态从 FastAPI 的线程池线程改，不加锁会撞。

    ⚠️ 建库后第一件事该调 `interrupt_running()`：上一个进程在跑的任务
    在这个进程里没有任何人管着它，不标记的话它永远停在 running ——
    在她眼里就是「卡死了」，而其实只是没人收尾。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ------------------------------------------------------------ 写

    def create(self, *, session_id: str, goal: str,
               steps_hint: str = "") -> str:
        tid = f"task-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id, session_id, goal, steps_hint, status,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (tid, session_id or "", goal, steps_hint or None,
                 PROPOSED, _now().isoformat()),
            )
            self._conn.commit()
        logger.info("长任务已立：%s（%s）%s", tid, session_id or "?", goal[:60])
        return tid

    def claim(self, tid: str) -> dict[str, Any] | None:
        """🔴 **幂等闸门**：把 proposed / interrupted 原子地翻成 confirmed。

        她连点两次「跑」、或者对同一个中断任务点两次「接着跑」，只有
        一次能成（同 `orders.OrderStore.claim` 的理由：先查后改的话
        两个请求都看得到旧状态）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if row is None:
                return None
            if row["status"] not in (PROPOSED, INTERRUPTED):
                logger.info("任务 %s 状态是 %s，不能确认", tid, row["status"])
                return None
            cur = self._conn.execute(
                "UPDATE tasks SET status=?, confirmed_at=?, finished_at=NULL"
                " WHERE id=? AND status=?",
                (CONFIRMED, _now().isoformat(), tid, row["status"]))
            self._conn.commit()
            if cur.rowcount != 1:
                logger.info("任务 %s 已被另一次请求确认，这次跳过", tid)
                return None
        d = dict(row)
        d["status"] = CONFIRMED
        logger.info("任务 %s → confirmed（她点了「跑」）", tid)
        return d

    def start_next(self) -> dict[str, Any] | None:
        """取下一个该跑的任务（并发 = 1 的实现点）：**她最早点「跑」的**
        confirmed 原子地翻成 running。没有就 None。

        按 confirmed_at 排而不是 created_at —— 排队从她点头的时刻算：
        先立的卡她后点，不该插到她先点的那张前面。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE status=?"
                " ORDER BY COALESCE(confirmed_at, created_at) LIMIT 1",
                (CONFIRMED,)).fetchone()
            if row is None:
                return None
            cur = self._conn.execute(
                "UPDATE tasks SET status=?, started_at=? WHERE id=? AND status=?",
                (RUNNING, _now().isoformat(), row["id"], CONFIRMED))
            self._conn.commit()
            if cur.rowcount != 1:
                return None  # 另一个循环实例抢走了（不该发生，但别炸）
        d = dict(row)
        d["status"] = RUNNING
        return d

    def cancel(self, tid: str) -> dict[str, Any] | None:
        """取消。非终态都能取消；running 的是**优雅停**：状态先翻，
        runner 里的工具闸（`_cancel_gate`）下一轮就把循环掐停。
        返回取消后的行，或 None（不存在 / 已经结束）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if row is None or row["status"] in FINAL:
                return dict(row) if row is not None else None
            self._conn.execute(
                "UPDATE tasks SET status=?, finished_at=? WHERE id=?",
                (CANCELLED, _now().isoformat(), tid))
            self._conn.commit()
        logger.info("任务 %s → cancelled（当前状态 %s）", tid, row["status"])
        d = dict(row)
        d["status"] = CANCELLED
        return d

    def finish(self, tid: str, status: str, *, result: str = "") -> bool:
        """收尾。**只在还处于 running 时生效** —— 跑到一半她取消了的话，
        终态保持 cancelled，模型最后说了什么都不覆盖她的决定。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status=?, result=?, finished_at=?"
                " WHERE id=? AND status=?",
                (status, result or None, _now().isoformat(), tid, RUNNING))
            self._conn.commit()
        applied = cur.rowcount == 1
        if applied:
            logger.info("任务 %s → %s", tid, status)
        return applied

    def interrupt_running(self) -> int:
        """启动时把上一进程没跑完的收掉。返回收了几个。

        不在关机钩子里做：kill -9 / 部署脚本都不会给关机钩子机会，
        「启动时收尸」是唯一必然执行的时点。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status=?, finished_at=? WHERE status=?",
                (INTERRUPTED, _now().isoformat(), RUNNING))
            self._conn.commit()
        return cur.rowcount

    def count_waiting_for_her(self) -> int:
        """等她点头的任务有几个：刚提议的 + 被中断等她点「接着跑」的（只读）。

        这两种正是 `claim()` 肯翻成 confirmed 的状态，所以口径和「她能点的」一致。
        给 `guide/world_map.py` 的「待她确认」用。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN (?, ?)",
                (PROPOSED, INTERRUPTED)).fetchone()
        return int(row[0])

    def append_progress(self, tid: str, step: str, *, kind: str = "step",
                        payload: str | None = None) -> None:
        """追加一条进度。**只写不改** —— 它既是给模型续跑看的上下文，
        也是这件事做没做过的账本。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_progress(task_id, at, step, kind, payload)"
                " VALUES(?,?,?,?,?)",
                (tid, _now().isoformat(), step[:1000], kind,
                 (payload or None) if payload else None))
            self._conn.commit()

    # ------------------------------------------------------------ 读

    def get(self, tid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def latest(self) -> dict[str, Any] | None:
        """最近的一个任务（`task_status` 不带 id 时看它）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def progress(self, tid: str, limit: int = 60) -> list[dict[str, Any]]:
        """进度尾部，时间正序（旧→新）。给模型续跑和前端时间线用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM (SELECT * FROM task_progress WHERE task_id=?"
                " ORDER BY seq DESC LIMIT ?) ORDER BY seq",
                (tid, limit)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------- 执行

#: 任务态的 system。不带 11.5K 人设前缀 —— 这是后台干活，不是聊天：
#: 人设帮不了理书架，还会让他在总结里寒暄。
TASK_SYSTEM = (
    "你是 Nox，正在后台执行糖糖交给你的一个长任务。这是干活，不是聊天："
    "不寒暄、不问好，专注把任务做完。用你手里的工具实际去做，"
    "不要只列计划。每完成一小步就调 task_step 报告一步"
    "（做什么、结果如何，一句话，写给糖糖看）。"
    "全部做完后输出一段简短中文总结：做了什么、结果如何、有什么没做成。"
    "卡住或做不了就如实说，绝不编造完成。"
)

TASK_STEP_SPEC = ToolSpec(
    side_effect="none",
    name="task_step",
    description=(
        "长任务进度上报：每完成一小步就调一次。step 是一句人话"
        "（做了什么、结果如何），她会在任务卡的时间线上看到它。"
        "🔴 只报**已经做成**的，不报打算 —— 这份日志之后要用来断点续跑，"
        "把「打算」记进去，续跑时他会把没做过的当成做过的。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "step": {"type": "string", "description": "这一步做了什么，一句话"},
            "detail": {"type": "string", "description": "补充细节，可选"},
        },
        "required": ["step"],
    },
)


class TaskCancelled(RuntimeError):
    """任务已不在 running（被取消 / 被重启打断）。"""


def _cancel_gate(store: TaskStore, tid: str, handler):
    """给任务的每件工具包一道闸：状态已离开 running → 一律失败。

    连续两次失败 `FailureTracker` 熔断（tool_stuck），循环就此收尾 ——
    这就是「AgentLoop 下轮检查退出」的实现：检查点在每次工具调用前，
    不在模型调用前（那边插不进去）。模型只发文字不调工具的话，
    它自己也会在下一轮 end_turn 收尾。
    """

    def checked(args: dict) -> str:
        row = store.get(tid)
        if row is None or row["status"] != RUNNING:
            state = row["status"] if row else "不存在"
            raise TaskCancelled(
                f"任务已被她停止（当前状态：{state}）。"
                "立即停止：不要再调任何工具，直接结束，不要假装完成了。"
            )
        return handler(args)

    return checked


def _make_step_handler(store: TaskStore, tid: str):
    def handler(args: dict) -> str:
        step = str(args.get("step") or "").strip()
        if not step:
            raise RuntimeError("step 不能为空 —— 用一句话说清这一步做成了什么")
        detail = str(args.get("detail") or "").strip() or None
        store.append_progress(tid, step, payload=detail)
        return "已记下这一步。继续。"

    return handler


def build_task_loop(core: Any, store: TaskStore, tid: str, *,
                    deadline_s: float | None = None,
                    max_iterations: int | None = None) -> AgentLoop:
    """为这个任务造一个专属 AgentLoop：同一批工具（各包取消闸）
    + task_step，独立的预算。**不动 core.loop** —— 聊天的护栏
    （12 轮 / 聊天 deadline）和任务的护栏（200 轮 / 30 分钟）是两回事，
    共用一个对象就得互相迁就。

    花钱/不可逆的工具在这里自动失效：任务的 ToolContext.confirmed
    恒为空集，`AgentLoop._execute` 的闸门会把它们拦下 —— 和聊天同一道锁。
    """
    gated = functools.partial(_cancel_gate, store, tid)
    tools: dict[str, Tool] = {
        name: Tool(spec=t.spec, handler=gated(t.handler))
        for name, t in core.loop.tools.items()
    }
    loop = AgentLoop(
        adapter=core.loop.adapter,
        tools=tools,
        max_iterations=max_iterations or TASK_MAX_ITERATIONS,
        deadline_s=deadline_s if deadline_s is not None else TASK_DEADLINE_S,
        failure_limit=core.loop.failure_limit,
    )
    loop.register(TASK_STEP_SPEC, gated(_make_step_handler(store, tid)))
    return loop


def build_task_prompt(goal: str, steps_hint: str,
                      rows: list[dict[str, Any]]) -> str:
    """任务的开工指令。rows 非空 = 续跑：把做过的亮出来，
    从哪继续由模型自己判断（agentic resume，v1 不做工具级断点）。"""
    lines = [f"【任务】{goal}"]
    if steps_hint:
        lines.append(f"【她的提示】{steps_hint}")
    if rows:
        lines.append("")
        lines.append("【之前已经做过的】（接着干，不要重复这些）")
        for r in rows:
            at = r.get("at", "")
            hhmm = at[11:16] if len(at) >= 16 else at
            mark = "→" if r.get("kind") == "result" else "·"
            lines.append(f"  {mark} {hhmm} {r['step']}")
    lines.append("")
    lines.append(
        "一步一步推进，直到整个目标完成。进度随时记（task_step），"
        "做完输出总结。"
    )
    return "\n".join(lines)


def run_one(*, core: Any, store: TaskStore, tid: str,
            deadline_s: float | None = None,
            max_iterations: int | None = None) -> None:
    """跑一个任务（在线程池里）。异常交给调用方 —— 循环层兜底标 failed。"""
    task = store.get(tid)
    if task is None or task["status"] != RUNNING:
        return  # 确认到开跑之间被取消了
    rows = store.progress(tid, limit=60)
    prompt = build_task_prompt(
        task["goal"], task.get("steps_hint") or "", rows)
    loop = build_task_loop(core, store, tid,
                           deadline_s=deadline_s, max_iterations=max_iterations)
    result = loop.run(prompt, system=TASK_SYSTEM,
                      session_id=task.get("session_id") or None)

    # 任务循环里工具攒的附带产物（表情/语音/订单卡）没有 SSE 可搭 ——
    # 只能丢掉，但必须留痕：静默丢了的表现是「他说发了但她什么都没收到」
    if result.attachments:
        logger.warning("任务 %s 产生了 %d 个附带产物，后台跑没有输出通道，已丢弃",
                       tid, len(result.attachments))
    # 任务里写过的状态（todo/health…）→ 打掉 Provider 缓存，
    # 不然下一轮聊天读的还是写之前的快照（同 nox._flush_dirty 的理由）
    registry = getattr(core, "context", None)
    for name in result.dirty_providers or []:
        try:
            registry.invalidate(name)
        except Exception:  # noqa: BLE001
            logger.warning("打掉 Provider 缓存失败: %s", name)

    summary = (result.text or "").strip()
    if not result.ok:
        why = result.detail or result.outcome
        summary = (summary + f"\n（{why}）").strip() if summary else f"任务中断：{why}"
    status = DONE if result.ok else FAILED
    if store.finish(tid, status, result=summary[:4000]):
        store.append_progress(tid, summary[:500], kind="result")


async def run_task_loop(*, core: Any, store: TaskStore,
                        poll_s: float = POLL_S, beat_s: float = BEAT_S) -> None:
    """lifespan 后台循环：有 confirmed 就跑（一次一个），没事就歇。

    形状照 `attention/dream.run_dream_loop`：阻塞活丢线程池、
    CancelledError 原样抛、一轮炸了下轮继续。差别是任务一跑就是
    几十分钟 —— 所以线程在跑的时候这里也醒着，按 beat_s 打心跳：
    心跳说的是「runner 活着」，不是「任务活着」（任务有自己的 deadline）。
    """
    logger.info("长任务循环启动（并发=1，预算 %.0fs，轮数上限 %d）",
                TASK_DEADLINE_S, TASK_MAX_ITERATIONS)
    while True:
        try:
            task = store.start_next()
            if task is None:
                heartbeat.beat(HEARTBEAT_JOB)
                await asyncio.sleep(poll_s)
                continue
            logger.info("长任务开跑：%s %s", task["id"], task["goal"][:60])
            worker = asyncio.ensure_future(asyncio.to_thread(
                run_one, core=core, store=store, tid=task["id"]))
            while not worker.done():
                await asyncio.sleep(beat_s)
                heartbeat.beat(HEARTBEAT_JOB)
            try:
                await worker
            except Exception:  # noqa: BLE001 —— 任务炸了标 failed，循环活着
                logger.exception("长任务 %s 执行炸了", task["id"])
                store.finish(task["id"], FAILED, result="执行环境出错，详见服务日志")
            heartbeat.beat(HEARTBEAT_JOB)
        except asyncio.CancelledError:
            # 在跑的线程收不了（to_thread 不响应取消）—— 进程真退出的话，
            # 下次启动 interrupt_running() 会把它标成 interrupted
            logger.info("长任务循环停止")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("长任务循环出错，%ss 后再来", int(poll_s))
            await asyncio.sleep(poll_s)
