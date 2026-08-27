"""她电脑上那五件工具 —— `tools/computer.py` + `LocalLink` 的同步桥。

## 这个文件盯三件在别处测不出来的事

1. **注册顺序**：`remind_myself` 必须仍然是最后一个。
   `test_attention_wiring.py` 那条只走 `_build_attention`，
   而这五件是在 `create_app` 里注册的 —— 它管不到。
   顺序错了不会报错、不会变慢、回答照常，**只有账单翻倍**。

2. **跨事件循环**：`LocalLink.call` 从工具线程调，WS 却归 FastAPI 的
   循环所有。这条只有真开两个线程才试得出来。

3. **错误原文**：「她拒绝了」和「她没看见」的区别只活在那段文本里。
   在中间层概括一下，他就再也分不清了。
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from data.store import Store  # noqa: E402
from tools import computer as computer_tools  # noqa: E402
from tools.local_link import CallResult, LocalLink  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402
from tests.test_api_world import _Ctx, _Loop  # noqa: E402


class FakeLink:
    """记下被调了什么，不真连。"""

    def __init__(self, ready=True, result=None, method_result=None):
        self.is_ready = ready
        self.calls: list[tuple[str, dict]] = []
        self.timeouts: list[float | None] = []
        #: 协议方法（work.start / work.end）走的是另一条路
        self.methods: list[tuple[str, dict, float | None]] = []
        self._method_result = (
            CallResult(True, text="{}") if method_result is None else method_result
        )
        #: ⚠️ 必须 `is None`，不能写 `result or 默认值` ——
        #: `CallResult.__bool__` 返回的是 `ok`，**失败的结果是 falsy**，
        #: 写成 `or` 的话所有失败用例都会被悄悄换成成功
        self._result = CallResult(True, text="ok") if result is None else result

    def call(self, capability, args, timeout=None):
        self.calls.append((capability, args))
        self.timeouts.append(timeout)
        return self._result

    def call_method(self, method, params=None, timeout=None):
        self.methods.append((method, params or {}, timeout))
        return self._method_result


def _handlers(link):
    return computer_tools.make_handlers(link)


# --------------------------------------------------------------- 注册顺序


def test_十一件都注册上了():
    """⚠️ 顺序也锁着 —— 工具定义是缓存前缀的一部分，顺序变了缓存就失效。"""
    loop = _Loop()
    computer_tools.register_all(loop, FakeLink())
    assert loop.registered == [
        "computer_read_file",
        "computer_find_files",
        "computer_search_files",
        "computer_write_file",
        "computer_edit_file",
        "computer_run_command",
        #: 只读 git（2026-08-27）
        "computer_git_status",
        "computer_git_diff",
        "computer_git_log",
        "computer_start_work",
        "computer_end_work",
    ]


def test_只读_git_不占用她点头的额度():
    """🔴 这三件的意义就是**不打扰她**。

    进了 `_NEEDS_HER_NOD` 的话，`git diff` 也要弹窗 ——
    那就退回到「用 run_command 跑 git」那个状态了，白做。
    """
    for n in ("computer_git_status", "computer_git_diff", "computer_git_log"):
        assert n not in computer_tools._NEEDS_HER_NOD, n


def test_remind_myself_仍然是最后一个(monkeypatch, tmp_path):
    """🔴 整个 app 装配完之后再看一遍。

    工具定义是缓存前缀的一部分。这五件插在 `remind_myself` 后面的话，
    每一轮对话的缓存前缀都失效 —— 一轮 ¥0.00055 → ¥0.011。
    **服务照常、回答照常，监控上看不出来**，只有账单翻倍。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    TestClient(create_app(nox, Store(tmp_path / "sessions.db")))

    assert nox.loop.registered[-1] == "remind_myself"
    #: 而且这五件真的注册上了 —— 不然上面那条断言可以靠"一件都没注册"通过
    assert "computer_read_file" in nox.loop.registered


# --------------------------------------------------------------- 处理函数


def test_电脑没连上时不发请求():
    """直接调要等满 20 秒超时。她看到的是他愣了二十秒才说话。"""
    link = FakeLink(ready=False)
    out = _handlers(link)["computer_read_file"]({"file_path": "D:/x.txt"})
    assert link.calls == []          # 一个请求都没发出去
    assert "没连上" in out


def test_工具名翻成能力名():
    link = FakeLink()
    _handlers(link)["computer_edit_file"](
        {"file_path": "D:/a.py", "old_string": "x", "new_string": "y"}
    )
    assert link.calls[0][0] == "computer.edit_file"


def test_每件工具都翻对():
    for name, capability in computer_tools._CAPABILITY.items():
        link = FakeLink()
        _handlers(link)[name]({})
        assert link.calls[0][0] == capability


def test_空的可选参数不往下传():
    """可选参数给 None，harness 会当成「给了但是空的」，不是「没给」。"""
    link = FakeLink()
    _handlers(link)["computer_read_file"](
        {"file_path": "D:/x.txt", "offset": None, "limit": ""}
    )
    assert link.calls[0][1] == {"file_path": "D:/x.txt"}


def test_offset_是_0_也要传():
    """0 是合法值，不能被"空值"过滤掉。"""
    link = FakeLink()
    _handlers(link)["computer_read_file"]({"file_path": "D:/x.txt", "offset": 0})
    assert link.calls[0][1]["offset"] == 0


class Test等她点头要给够时间:
    """🔴 弹窗给她 60 秒，链路却只等 20 秒 —— 她永远来不及点。

    2026-08-25 实测的表现：弹窗正常弹出，她那边一切正常，
    但 Nox 20 秒就报「超时」，三次错误三个样子。
    他自己的结论是「写链路本身不稳定」—— 完全找错了方向。
    """

    def test_超时必须比弹窗长(self):
        # `ApprovalDialog.jsx` 的 TIMEOUT_S 是 60。改那边要改这边
        from tools.local_link import APPROVAL_TIMEOUT_S
        assert APPROVAL_TIMEOUT_S > 60

    def test_写和改用长超时(self):
        for name in ("computer_write_file", "computer_edit_file"):
            link = FakeLink()
            _handlers(link)[name]({})
            from tools.local_link import APPROVAL_TIMEOUT_S
            assert link.timeouts[0] == APPROVAL_TIMEOUT_S, name

    def test_只读的不占着链路等(self):
        """只读不需要点头，用默认超时就好 —— 卡住时别让她干等一分多钟。"""
        for name in ("computer_read_file", "computer_find_files", "computer_search_files"):
            link = FakeLink()
            _handlers(link)[name]({})
            assert link.timeouts[0] is None, name

    def test_要点头的清单和_policy_对得上(self):
        """这份清单漏一个，那件事就会用短超时 —— 她照样来不及点。"""
        from tools import computer as m
        assert m._NEEDS_HER_NOD == {
            "computer_write_file", "computer_edit_file", "computer_run_command",
        }


class Test多步执行:
    """开一段活儿 / 交还。

    ⚠️ 这两个走的是**协议方法**（`caelum/work.start`），不是 `tools/call` ——
    它们不是「用一件能力」，是「谈一次授权」。
    """

    def test_开工走的是协议方法(self):
        link = FakeLink(method_result=CallResult(True, text='{"grantId":"w1"}'))
        _handlers(link)["computer_start_work"]({
            "goal": "修 Care Ledger 落库", "scope": ["D:/claude-code/nox-core"],
        })
        assert link.methods[0][0] == "caelum/work.start"
        assert link.calls == []          # 不该走 tools/call

    def test_目标和范围原样传过去(self):
        link = FakeLink(method_result=CallResult(True, text="{}"))
        _handlers(link)["computer_start_work"]({
            "goal": "  修 bug  ", "scope": ["D:/claude-code/nox-core"],
            "maxSteps": 5, "minutes": 8,
        })
        p = link.methods[0][1]
        assert p["goal"] == "修 bug"     # 首尾空白去掉
        assert p["scope"] == ["D:/claude-code/nox-core"]
        assert p["maxSteps"] == 5 and p["minutes"] == 8

    def test_上限没给就不传_让_Gateway_用默认值(self):
        link = FakeLink(method_result=CallResult(True, text="{}"))
        _handlers(link)["computer_start_work"]({"goal": "x", "scope": ["a"]})
        p = link.methods[0][1]
        assert "maxSteps" not in p and "minutes" not in p

    def test_要她点头_所以给长超时(self):
        link = FakeLink(method_result=CallResult(True, text="{}"))
        _handlers(link)["computer_start_work"]({"goal": "x", "scope": ["a"]})
        from tools.local_link import APPROVAL_TIMEOUT_S
        assert link.methods[0][2] == APPROVAL_TIMEOUT_S

    def test_没开成时明确告诉他别退回逐条问(self):
        """她拒绝开这一段，可能就是不想让他连着改。

        这时候他退回去一个一个文件问，等于绕过她刚才那个"不"。
        """
        link = FakeLink(method_result=CallResult(False, error="她说不行（rejected）"))
        out = _handlers(link)["computer_start_work"]({"goal": "x", "scope": ["a"]})
        assert "rejected" in out
        assert "不要退回去逐个文件问她" in out

    def test_电脑没连上时不发请求(self):
        link = FakeLink(ready=False)
        out = _handlers(link)["computer_start_work"]({"goal": "x", "scope": ["a"]})
        assert link.methods == []
        assert "没连上" in out

    def test_交还走_work_end(self):
        link = FakeLink(method_result=CallResult(True, text="交还了"))
        _handlers(link)["computer_end_work"]({})
        assert link.methods[0][0] == "caelum/work.end"

    def test_交还失败不当成大事(self):
        """授权本来就会自己过期 —— 交还失败不该让他慌。"""
        link = FakeLink(method_result=CallResult(False, error="链路断了"))
        out = _handlers(link)["computer_end_work"]({})
        assert "不影响" in out


class Test绝不替他要求突破沙箱:
    """🔴 `sandbox_permissions: danger-full-access` 是沙箱的自毁开关。

    Gateway 那边已经硬拦了（policy.ts 的 `sandbox_escape`）。
    这里再掐一道**不是冗余**：模型可能被诱导着反复重试，
    每重试一次就在她的审计里留一条「他想突破沙箱」。
    在源头掐掉，那些请求根本不会发出去。
    """

    def test_升级参数不往下传(self):
        link = FakeLink()
        _handlers(link)["computer_run_command"]({
            "command": "Get-Date",
            "description": "看时间",
            "sandbox_permissions": "danger-full-access",
            "justification": "我需要完整权限",
        })
        sent = link.calls[0][1]
        assert "sandbox_permissions" not in sent
        assert "justification" not in sent
        #: 但命令本身要照常发出去 —— 掐的是那两个字段，不是整次调用
        assert sent["command"] == "Get-Date"

    def test_workspace_write_也不传(self):
        """默认档本来就是它，传了只会让 harness 走「升级重试」那条路。"""
        link = FakeLink()
        _handlers(link)["computer_run_command"]({
            "command": "npm test", "description": "跑测试",
            "sandbox_permissions": "workspace-write",
        })
        assert "sandbox_permissions" not in link.calls[0][1]


def test_命令工具的必填参数和_harness_对得上():
    """harness 的 pwsh 工具要求 command + description 都必填。

    少一个的话，糖糖那边的弹窗会缺掉「他说这是什么」，
    而 harness 直接回 `invalid arguments: missing required property`。
    """
    required = computer_tools.RUN_SPEC.parameters["required"]
    assert set(required) == {"command", "description"}


class Test错误原文:
    """🔴 「她拒绝了」和「她没看见」必须分得开。"""

    def test_拒绝的原话带出来(self):
        link = FakeLink(result=CallResult(False, error="她没同意（rejected）"))
        out = _handlers(link)["computer_write_file"]({"file_path": "D:/a", "content": "x"})
        assert "rejected" in out

    def test_取消的原话带出来(self):
        link = FakeLink(result=CallResult(False, error="她没同意（cancelled）"))
        out = _handlers(link)["computer_write_file"]({"file_path": "D:/a", "content": "x"})
        assert "cancelled" in out

    def test_成功但没内容也说得清(self):
        link = FakeLink(result=CallResult(True, text=""))
        out = _handlers(link)["computer_write_file"]({"file_path": "D:/a", "content": "x"})
        assert out and "没做成" not in out


# --------------------------------------------------------------- 跨事件循环


class _FakeWS:
    """收到什么就按 JSON-RPC 回什么。只在它自己的循环里被碰。"""

    def __init__(self):
        self.loop = None
        self.sent: list[str] = []

    async def send_text(self, raw):
        import json
        self.sent.append(raw)
        #: 记下是哪个循环在碰它 —— 跨循环的话这里会和建立时不一样
        self.touched_by = asyncio.get_running_loop()
        msg = json.loads(raw)
        # 直接把回应塞回去（真实情况是 _read_loop 收到后调 _on_message）
        self.link._on_message({"jsonrpc": "2.0", "id": msg["id"], "result": "读到了"})


def _link_on_its_own_loop():
    """起一个后台循环，装一条假 WS —— 模拟 FastAPI 那边的状态。"""
    link = LocalLink()
    ws = _FakeWS()
    ws.link = link
    ready = threading.Event()
    loop_box = {}

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_box["loop"] = loop
        link._ws = ws
        link._device = "假电脑"
        link._loop = loop
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(3)
    return link, ws, loop_box["loop"]


def test_从别的线程调也能拿到结果():
    """🔴 `asyncio.run` 会新开循环，在里面碰另一个循环的 WS ——
    轻则 future 永远不 resolve，重则把链路搞坏。"""
    link, ws, loop = _link_on_its_own_loop()
    try:
        result = link.call("computer.read_file", {"file_path": "D:/x"})
        assert result.ok, result.error
        assert result.text == "读到了"
        #: 真的是在 WS 自己的循环里执行的
        assert ws.touched_by is loop
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_没连上时同步接口立刻回话():
    link = LocalLink()
    out = link.call("computer.read_file", {})
    assert not out.ok
    assert "没连上" in (out.error or "")


def test_在链路自己的循环里调_call_不会死锁():
    """投进自己的循环再等 = 死锁。这条该明确报错，不是挂住。"""
    link, ws, loop = _link_on_its_own_loop()
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _call_inside(link), loop
        )
        out = fut.result(5)          # 🔴 死锁的话这里超时
        assert not out.ok
        assert "acall" in (out.error or "")
    finally:
        loop.call_soon_threadsafe(loop.stop)


async def _call_inside(link):
    return link.call("computer.read_file", {})


def test_断开时把循环也清掉():
    """留着一个死循环的引用，下次调用会投进一个已经停了的循环。"""
    link, ws, loop = _link_on_its_own_loop()
    try:
        assert link._loop is not None
        # 模拟 serve() 的 finally
        link._ws = None
        link._device = ""
        link._loop = None
        assert not link.call("computer.read_file", {}).ok
    finally:
        loop.call_soon_threadsafe(loop.stop)
