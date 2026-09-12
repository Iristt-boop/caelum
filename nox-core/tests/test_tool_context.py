"""`ToolContext` 这个载体本身 —— 「这一轮是谁」的按轮隔离。

## 为什么专门测它

会话 id 原来放在 `Nox.current_session_id` 一个**进程级属性**上：
`api/server.py` 的 `_turn_starts()` 每轮开跑前写它，而工具在**别的时间点**读。
两个并发请求就会互串 —— 而这不是理论：

    她正在聊天（A 轮）→ `_turn_starts(A)` 写进去
    关注链/唤醒链自己开口（B 轮）→ `_turn_starts(B)` 覆盖掉
    A 轮的工具这才真的跑 → 读到的是 **B**

后果：A 轮里 `remind_myself` 留的纸条、`luckin_order` 建的订单（`store.create`
记的 session_id）全挂到 **B 会话**上，而 App 那边按会话找订单会找不到。
**不报错**，只是挂错地方。

修法：会话 id 进 `ToolContext`（按轮隔离，loop 每次 run 造一个新的），
工具经 `context.session_id()` 取值。所以这里的测试分两层：

  1. **载体**：交错跑两轮，各轮的工具必须读到各自的会话 id
  2. **接线**：注册时传的必须是 `context.session_id` 这个函数本身，
     而不是 `lambda: core.current_session_id`（后者取的是「最近一次开跑的那轮」）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from tools import context  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

RECORD_SPEC = ToolSpec(name="record", description="记下这轮的会话 id",
                       parameters={"type": "object", "properties": {}})


class _ToolCallingAdapter:
    """第一轮调工具，第二轮说话。"""

    name = "fake"

    def __init__(self, tool_name: str = "record") -> None:
        self.tool_name = tool_name
        self.calls = 0

    def _turns(self):
        return [
            Turn(stop_reason="tool_use", text="",
                 tool_calls=[ToolCall(id="t1", name=self.tool_name, arguments={})]),
            Turn(stop_reason="end_turn", text="记下了"),
        ]

    def complete(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        return turn

    def stream(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        if turn.text:
            yield StreamEvent("text", text=turn.text)
        yield StreamEvent("done", turn=turn)


def _loop(handler) -> AgentLoop:
    loop = AgentLoop(adapter=_ToolCallingAdapter())
    loop.register(RECORD_SPEC, handler)
    return loop


# ------------------------------------------------------------------ 载体

def test_会话_id_按轮隔离_交错也不串():
    """🔴 这条是 `2.1` 的正身。

    时序是照真实场景造的：A 轮推进到「工具即将执行」→ **让 B 轮完整跑完**
    （旧实现里这一步会把进程级的会话 id 改成 B）→ 再让 A 跑完。
    A 的工具最后读到的必须还是 A。

    如果谁哪天把会话 id 挪回进程级属性、或者挂到 loop 实例上，这条会红。
    """
    seen: list[tuple[str, str | None]] = []

    def make_tool(tag: str):
        def handler(_args: dict) -> str:
            seen.append((tag, context.session_id()))
            return "记下了"
        return handler

    loop_a = _loop(make_tool("A"))
    loop_b = _loop(make_tool("B"))

    gen_a = loop_a.run_stream("A 的话", session_id="sess-A")
    # 推进到工具**即将**执行：`tool_start` 是在 `_execute` 之前 yield 的，
    # 所以这里 A 的工具还没跑 —— 这就是交错的缝隙
    for ev in gen_a:
        if ev.type == "tool_start":
            break
    else:
        raise AssertionError("A 轮没有走到工具调用，测试没测到该测的东西")

    assert seen == [], "A 的工具不该在这时候已经跑过"

    # B 轮完整跑完（旧实现里这一下就把全局会话 id 覆盖了）
    for _ in loop_b.run_stream("B 的话", session_id="sess-B"):
        pass
    assert seen == [("B", "sess-B")], f"B 轮该读到 sess-B：{seen}"

    # 让 A 跑完 —— 它必须还拿着自己的 sess-A
    for _ in gen_a:
        pass
    assert seen == [("B", "sess-B"), ("A", "sess-A")], f"串了：{seen}"


def test_非流式那条也带会话_id():
    """`run` 和 `run_stream` 是两个独立入口 —— 只接一个就会漏另一个。"""
    got: list[str | None] = []

    def handler(_args: dict) -> str:
        got.append(context.session_id())
        return "记下了"

    _loop(handler).run("记一下", session_id="s-1")
    assert got == ["s-1"]


def test_不在轮次里_session_id_是_None():
    """工具被单元测试/脚本直接调用时没有轮次上下文 —— 取值器要能容忍。"""
    assert context.current() is None
    assert context.session_id() is None


def test_没传_session_id_时是_None():
    """没传就是没有（比如 CLI 那条路）—— 不留任何"上一次的值"。"""
    got: list[str | None] = []

    def handler(_args: dict) -> str:
        got.append(context.session_id())
        return "记下了"

    _loop(handler).run("记一下")
    assert got == [None]


# ------------------------------------------------------------------ 接线

def test_注册时传的是取值函数_不是捕获的全局():
    """守「别写回 `lambda: core.current_session_id`」。

    那个 lambda 在**注册时**建好、在**工具调用时**才求值，中间隔着任意多个请求 ——
    它取到的是「最近一次开跑的那轮」，不是「我正在跑的这一轮」。
    这正是 `2.1` 那条并发隐患的写法，所以直接在源码上钉死。
    """
    server = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    nox = (ROOT / "nox.py").read_text(encoding="utf-8")

    assert "session_id_ref=context.session_id" in server, "remind_myself 的取值器接错了"
    assert "session_id_ref=context.session_id" in nox, "luckin 的取值器接错了"
    assert "session_id_ref=lambda" not in server, "又写回 lambda 捕获全局了"
    assert "session_id_ref=lambda" not in nox, "又写回 lambda 捕获全局了"


def _code_only(path: Path) -> str:
    """去掉**注释和字符串**（docstring 也算字符串）之后的源码。

    直接用 `"xxx" not in 源码` 会被注释和说明性 docstring 绊倒 ——
    而那些地方提到旧名字是**故意的**（说明为什么要删）。
    tokenize 一下才分得清「代码里真的还在用」和「只是文档里提了一句」。
    """
    import tokenize

    out: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


def test_生产代码里没有进程级的会话_id_了():
    """把两个属性彻底删掉了，别再有人加回来。

    只看**代码**，不看注释/docstring —— 那些地方留着旧名字是为了说明来历。
    """
    for rel in ("nox.py", "api/server.py", "attention/waker.py", "attention/speaker.py"):
        code = _code_only(ROOT / rel)
        assert "current_session_id" not in code, f"{rel} 的代码里又出现了 current_session_id"
        assert "current_session_started" not in code, f"{rel} 的代码里又出现了 current_session_started"
