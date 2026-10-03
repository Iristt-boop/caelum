"""长任务 v1 —— 设计见 Nox-长任务循环-v1-设计.md。

这个文件盯的命根子和 `test_orders.py` 同一条：**对话侧的工具永远
不能把任务带到 running**。长任务的破坏力是累积性的（批量改 481 条
数据），启动必须出确认卡、她点了才跑（R8）。

其余守的是设计文档第四节的验收项：重启标 interrupted、progress
保留可续跑、取消后工具轮不再继续、测试会话不产生任务（R6）、
并发 = 1。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import tasks as task_mod  # noqa: E402
from agent.llm import ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from tools import tasks as tasks_tools  # noqa: E402
from tools import context as tool_context  # noqa: E402


# ---------------------------------------------------------------- 测试件


class FakeAdapter:
    """按预设脚本逐轮返回 Turn（同 test_loop.py 的那个）。"""

    name = "fake"

    def __init__(self, script: list[Turn]) -> None:
        self.script = list(script)

    def complete(self, messages, tools, *, system=None, dynamic_system=None,
                 depth=None, max_tokens=None):
        if not self.script:
            return Turn(stop_reason="end_turn", text="脚本用完了")
        return self.script.pop(0)


class FakeLoop:
    """记下注册进来的工具，handler 可直接调用。"""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register(self, spec, handler) -> None:
        self.tools[spec.name] = handler


class FakeCore:
    """run_one / build_task_loop 需要的那点 core 形状。"""

    def __init__(self, script: list[Turn] | None = None) -> None:
        self.loop = AgentLoop(adapter=FakeAdapter(script or []))
        self.loop.register(
            ToolSpec(name="noop", description="测试",
                     parameters={"type": "object", "properties": {}},
                     side_effect="read"),
            lambda args: "好",
        )


@pytest.fixture
def store(tmp_path):
    s = task_mod.TaskStore(tmp_path / "tasks.db")
    yield s
    s.close()


def _registered(store, sid="sess-1"):
    loop = FakeLoop()
    tasks_tools.register_all(
        loop, store_ref=lambda: store, session_id_ref=lambda: sid)
    return loop


def _confirmed(store, goal="把记忆里的书架理一遍") -> str:
    tid = store.create(session_id="sess-1", goal=goal)
    assert store.claim(tid) is not None
    return tid


# ---------------------------------------------------------------- 命根子


def test_tool_never_runs_the_task(store):
    """🔴 **整个设计的命根子。** start_long_task 只落 proposed + 出卡。

    不许出现 confirmed / running —— 那是她点「跑」之后才发生的事，
    走的是确认端点，模型物理上够不到。
    """
    loop = _registered(store)
    with tool_context.scope() as ctx:
        out = loop.tools["start_long_task"]({"goal": "理书架"})
    row = store.latest()
    assert row is not None and row["status"] == task_mod.PROPOSED
    assert any(a["type"] == "task" and a["task_id"] == row["id"]
               for a in ctx.attachments), "确认卡没发出去"
    # 话术必须把「还没开始」说死，否则他会说「已经在弄了」（瑞幸同款教训）
    assert "还没开始" in out


def test_tool_fail_closed_without_store():
    """链路没接上就拒绝（fail-closed），绝不「那就直接跑吧」。"""
    loop = FakeLoop()
    tasks_tools.register_all(
        loop, store_ref=lambda: None, session_id_ref=lambda: "sess-1")
    with pytest.raises(RuntimeError, match="没接上"):
        loop.tools["start_long_task"]({"goal": "x"})


def test_test_session_cannot_create_tasks(store):
    """R6：test- 前缀会话不得立任务 —— 任务是全局副作用。"""
    loop = _registered(store, sid="test-smoke-1")
    with pytest.raises(RuntimeError, match="测试会话"):
        loop.tools["start_long_task"]({"goal": "x"})
    assert store.latest() is None


def test_all_three_tools_registered(store):
    """三处同步守卫：SPEC / handler / register_all 的表缺一个，
    模型看得见调不了（delete_todo 的教训，PROJECT.md 53.2）。"""
    loop = _registered(store)
    for name in ("start_long_task", "task_status", "task_cancel"):
        assert name in loop.tools, f"{name} 没注册上"


def test_status_and_cancel_tools_read_store(store):
    tid = _confirmed(store, goal="理书架")
    assert store.start_next()["id"] == tid
    loop = _registered(store)
    status = loop.tools["task_status"]({})
    assert "正在后台跑" in status

    out = loop.tools["task_cancel"]({"task_id": tid})
    assert "已停" in out
    assert store.get(tid)["status"] == task_mod.CANCELLED


# ---------------------------------------------------------------- 状态机


def test_claim_is_idempotent(store):
    """她连点两次「跑」，只有一次能翻走（同 orders.claim 的理由）。"""
    tid = store.create(session_id="s", goal="g")
    assert store.claim(tid) is not None
    assert store.claim(tid) is None
    assert store.get(tid)["status"] == task_mod.CONFIRMED


def test_claim_from_interrupted_resumes(store):
    """中断的任务可以「接着跑」—— interrupted 不是终态。"""
    tid = _confirmed(store)
    assert store.start_next() is not None
    assert store.interrupt_running() == 1
    assert store.get(tid)["status"] == task_mod.INTERRUPTED
    assert store.claim(tid) is not None, "中断的任务点「接着跑」应该能回 confirmed"


def test_finish_never_overrides_cancel(store):
    """跑到一半她取消了 → 终态保持 cancelled，模型的收尾不覆盖她的决定。"""
    tid = _confirmed(store)
    store.start_next()
    store.cancel(tid)
    assert store.finish(tid, task_mod.DONE, result="做完了") is False
    assert store.get(tid)["status"] == task_mod.CANCELLED


def test_start_next_fifo(store):
    """排队的按**她点「跑」的先后**跑 —— 先立的卡她后点，不该插队。"""
    early_created = store.create(session_id="s", goal="先立后点")
    late_created = store.create(session_id="s", goal="后立先点")
    store.claim(late_created)                     # 她先点了后立的那张
    store.claim(early_created)
    assert store.start_next()["id"] == late_created
    assert store.start_next()["id"] == early_created
    assert store.start_next() is None


def test_interrupt_keeps_progress(store):
    """重启收尸不丢账：progress 留着，续跑才有得看。"""
    tid = _confirmed(store)
    store.start_next()
    store.append_progress(tid, "已理完 A~C 共 42 本")
    store.interrupt_running()
    rows = store.progress(tid)
    assert rows and "A~C" in rows[-1]["step"]


# ---------------------------------------------------------------- 执行


def test_prompt_carries_progress_for_resume(store):
    """断点续跑（agentic resume）：progress 必须进开工指令。"""
    store.append_progress("task-x", "已理完 A~C")
    store.append_progress("task-x", "已理完 D~F")
    prompt = task_mod.build_task_prompt("理书架", "", store.progress("task-x"))
    assert "已理完 A~C" in prompt and "已理完 D~F" in prompt
    assert "不要重复" in prompt


def test_run_one_reports_steps_and_finishes_done(store):
    """端到端：跑一步 task_step → 收尾 → done + result 落库。"""
    tid = _confirmed(store)
    store.start_next()
    core = FakeCore(script=[
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(
            id="c1", name="task_step", arguments={"step": "理完 A~C 共 42 本"})]),
        Turn(stop_reason="end_turn", text="全部理完了，481 本都过了一遍"),
    ])
    task_mod.run_one(core=core, store=store, tid=tid)
    row = store.get(tid)
    assert row["status"] == task_mod.DONE
    assert "481 本" in row["result"]
    steps = store.progress(tid)
    assert any("A~C" in s["step"] for s in steps)
    assert steps[-1]["kind"] == "result"


def test_cancel_gate_blocks_tools_mid_run(store):
    """取消后工具轮不再继续：每件工具先查状态，不在 running 就炸。"""
    tid = _confirmed(store)
    store.start_next()
    core = FakeCore()
    loop = task_mod.build_task_loop(core, store, tid)
    assert loop.tools["noop"].handler({}) == "好"   # running 时照常
    store.cancel(tid)
    with pytest.raises(task_mod.TaskCancelled):
        loop.tools["noop"].handler({})
    # task_step 也一样被闸住 —— 取消就是停，连进度都不再记
    with pytest.raises(task_mod.TaskCancelled):
        loop.tools["task_step"].handler({"step": "还想再记一步"})


def test_cancelled_run_keeps_cancelled_state(store):
    """跑的过程中被取消：finish 不生效，终态是 cancelled 而不是 done。"""
    tid = _confirmed(store)
    store.start_next()

    def _cancel_midway(args):
        store.cancel(tid)                            # 第一个工具调用就把任务停了
        return "停了"

    core = FakeCore()
    core.loop.register(
        ToolSpec(name="stoper", description="测试",
                 parameters={"type": "object", "properties": {}},
                 side_effect="read"),
        _cancel_midway)
    script = [
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(
            id="c1", name="stoper", arguments={})]),
        # 取消之后他还想调工具 —— 闸门该把这件也拦下
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(
            id="c2", name="noop", arguments={})]),
        Turn(stop_reason="end_turn", text="好，停下了"),
    ]
    core.loop.adapter.script = list(script)
    task_mod.run_one(core=core, store=store, tid=tid)
    assert store.get(tid)["status"] == task_mod.CANCELLED
    assert all(s["kind"] != "result" for s in store.progress(tid))


def test_failed_outcome_marks_failed(store):
    """跑炸了（error 结局）→ failed + 原因落库，不装完成。"""
    tid = _confirmed(store)
    store.start_next()
    core = FakeCore(script=[Turn(stop_reason="error", error="上游挂了")])
    task_mod.run_one(core=core, store=store, tid=tid)
    row = store.get(tid)
    assert row["status"] == task_mod.FAILED
    assert "上游挂了" in row["result"]


def test_task_step_rejects_empty(store):
    """进度不许空 —— 这份日志是续跑的账本，空行是噪音。"""
    tid = _confirmed(store)
    store.start_next()
    core = FakeCore()
    loop = task_mod.build_task_loop(core, store, tid)
    with pytest.raises(RuntimeError, match="不能为空"):
        loop.tools["task_step"].handler({"step": "  "})


# ---------------------------------------------------------------- 循环


def test_runner_picks_confirmed_and_beats(tmp_path, store):
    """lifespan 循环：confirmed 的会被接走跑完，心跳有记录。"""
    from obs import heartbeat

    heartbeat.reset()
    tid = _confirmed(store)
    core = FakeCore(script=[Turn(stop_reason="end_turn", text="理完了")])

    async def drive():
        job = asyncio.create_task(task_mod.run_task_loop(
            core=core, store=store, poll_s=0.01, beat_s=0.01))
        for _ in range(500):                      # 最多等 5 秒（假模型实际秒回）
            await asyncio.sleep(0.01)
            if store.get(tid)["status"] in (task_mod.DONE, task_mod.FAILED):
                break
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job

    asyncio.run(drive())
    assert store.get(tid)["status"] == task_mod.DONE
    assert heartbeat.snapshot()[task_mod.HEARTBEAT_JOB]["count"] > 0
