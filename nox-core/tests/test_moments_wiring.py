"""Moments 接进 `api/server.py` 那一层（T6）：开关、装配、循环、快照。

规格见 `Caelum-Moments-设计.md` 第六节（nox-core 那段）和第七节
「线上验证：两步走，别合并」。

## 为什么单独一个文件（抄 `tests/test_attention_wiring.py` 的先例）

接线这一层的特点是：**它只在开着的环境变量下才活**。
本地不设 `NOX_MOMENTS` 时，`_build_moments` 在第一行就 return None，
底下的取件、装配、起循环一行都不执行 —— 于是「本地全绿、线上第一次
开机就炸」是这层唯一的坏法，而且它不报错给任何人看。

`test_attention_wiring.py` 开头记着 2026-08-08 那次：接线里一个
`AttributeError` 把整个 Core 拖挂了，而且**只在线上活**（本地没开那个
环境变量）。所以这里显式 `monkeypatch.setenv` 逼那些分支在本地跑一遍。

## 每条测试能挡什么、不能挡什么

写在各自 docstring 里（CAELUM-MAP 第三·五节：说不清自己在防什么的测试，
下次重构会被当噪音删掉）。⚠️ 第 12 条是弱判据，它的 docstring 里写明了
它挡不住什么。

⚠️ 不打真网络、不调真模型、不碰真库：`post_tick` / `heartbeat` 被
monkeypatch 换掉，假 core / 假 attention 只提供身份不同的几个对象 ——
接线层一个字段都不读，它只管把五件递给循环。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 走模块名而不是直接 import 那几个函数：`api/server.py` 里还没写出来的那一步，
#: 只有点它的那几条红（AttributeError），别的照常绿 ——
#: 红得看得见，才知道自己红的是什么（同 `tests/test_moments_loop.py` 开头）
from api import server as api_server  # noqa: E402
#: 走模块名而不是直接 import 那几个函数：`loop.py` 里还没写出来的那一步，
#: 每条测试各自红（AttributeError），不会被一个 collection error 整个盖掉 ——
#: 红得看得见，才知道自己红的是什么
from moments import loop as moments_loop  # noqa: E402


# ---------------------------------------------------------------- 开关


def test_default_is_off(monkeypatch):
    """不设 `NOX_MOMENTS` 就是 off —— 先发代码零行为变化（第七节两步走第一步）。

    能挡什么：默认值被改成 on / shadow（那会让 deployed 的 Nox 立刻开始
    按 15 分钟一 tick 掷骰子发帖，而糖糖以为这一步只是「发代码」）。
    挡不住什么：有人把 `NOX_MOMENTS` 真的写进 `.env` —— 那是**有意**开的，
    由第 3、4、5 条从消费侧证明「开之前什么都不做」。
    """
    monkeypatch.delenv("NOX_MOMENTS", raising=False)
    assert moments_loop.mode() == "off"


def test_mode_parses_the_switch(monkeypatch):
    """三个取值逐个断言：on / shadow / off，大小写和前后空格都不算数。

    能挡什么：解析写成 `"不是 off 都算"` 或者只认小写 —— 前者让
    `NOX_MOMENTS=ON`（大写）真的开始发帖，后者让 `shadow` 拼成
    `Shadow` 时静默变成 off，两种都是一字母之差、日志里什么都不说。
    ⚠️ 取值表少于 12 个直接 fail：空集/两个取值的表也能「全过」，
    那不是通过，是没测。
    """
    cases = {
        "on": "on", "1": "on", "true": "on", "yes": "on",
        "ON": "on", "  on  ": "on", "True": "on", "YES": "on",
        "shadow": "shadow", "SHADOW": "shadow", " shadow ": "shadow",
        "": "off", "off": "off", "OFF": "off", "0": "off", "no": "off",
        "随便": "off", "shadows": "off", "2": "off",
    }
    assert len(cases) >= 12, "取值表太少，全过也证明不了什么"
    for raw, want in cases.items():
        monkeypatch.setenv("NOX_MOMENTS", raw)
        assert moments_loop.mode() == want, f"{raw!r} 该是 {want!r}"


# ---------------------------------------------------------------- 假货


class Spy:
    """点任何方法都记一笔，返回空列表。

    「一次都没被调过」是个**否定**断言，得先有东西可断 —— 拿一个什么都
    不记的假货去断 `== []`，那是恒真（同 `docs/LOGGING.md` 说的空集陷阱）。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _record(*_a, **_k):
            self.calls.append(name)
            return []
        return _record


class FakeRouter:
    def __init__(self, light_adapter):
        self.light_adapter = light_adapter


#: 身份不同的三个哑对象。接线层一个字段都不读，它只管把这几件原样递下去 ——
#: 所以「递对了没有」用 `is` 断，不用形状断
LIGHT = object()
BRIDGE = object()
SESSION_DB = object()


class FakeCore:
    """配齐的假 core —— Moments 要的三样：`router` / `bridge` / `session_store`。"""

    def __init__(self, *, light_adapter=LIGHT, bridge=BRIDGE,
                 session_store=SESSION_DB) -> None:
        self.router = FakeRouter(light_adapter)
        self.bridge = bridge
        self.session_store = session_store


class FakeAttention:
    """只要有个 `.store` —— `post_tick` 的 `store=` 要的就是它。"""

    def __init__(self) -> None:
        self.store = object()


class FakeMeter:
    """`agent/meter.py` 的 `tag` 那一个方法。"""

    def __init__(self) -> None:
        self.tags: list[tuple] = []
        self.last: object | None = None

    def tag(self, adapter, task):
        self.last = ("tagged", adapter, task)
        self.tags.append((adapter, task))
        return self.last


class ExplodingCore:
    """取任何属性都炸 —— 模拟「接线里那句取件抛了」。

    2026-08-08 线上那个 `AttributeError` 就是这个形状（假 core 上少一个
    属性），代价是**整个 Core 起不来**。
    """

    def __getattr__(self, name: str):
        raise RuntimeError(f"{name} 炸了")


# ---------------------------------------------------------------- 装配


def test_off_changes_nothing(monkeypatch):
    """🔴 默认 off = **零行为变化**（第七节两步走第一步）。

    能挡什么：① 工厂在 off 时也装配（那会占住五件、白建一个循环）；
    ② 有人硬调 `post_tick(mode="off")` 时假 store / 假 bridge 被碰过 ——
    碰过就意味着「关掉」其实还在读状态、写库、发帖。
    挡不住什么：环境变量设成 shadow 之后的行为，那是第 6、8 条的事。
    """
    monkeypatch.delenv("NOX_MOMENTS", raising=False)
    attn = FakeAttention()
    assert api_server._build_moments(FakeCore(), attn, FakeMeter()) is None

    store, sessions, bridge = Spy(), Spy(), Spy()
    assert moments_loop.post_tick(
        mode="off", store=store, attention=attn, sessions=sessions,
        bridge=bridge, adapter_ref=LIGHT,
    ) is None
    assert store.calls == [], "off 还读了状态"
    assert sessions.calls == [], "off 还查了今天的互动量"
    assert bridge.calls == [], "off 还打了 bridge"


def test_missing_attention_is_skipped(monkeypatch, caplog):
    """没有 attention 引擎就不起 —— drives 是从那儿来的，没有它这条线没输入。

    能挡什么：装配出一个 attention=None 的循环，它每 tick 都在
    「心里没事」上算一遍冲动、照常 beat 心跳 —— 台账里看着是活的，
    实际永远发不出东西（`_read_drives` 读不出来就当 `{}`）。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    with caplog.at_level(logging.INFO):
        caplog.clear()
        assert api_server._build_moments(FakeCore(), None, FakeMeter()) is None
    assert len(caplog.records) == 1, "缺件必须留一句，不然线上只有「它没跑」"
    assert caplog.records[0].levelno in (logging.INFO, logging.WARNING)


def test_missing_utility_model_is_skipped(monkeypatch, caplog):
    """没有 utility 模型就不起 —— 正文是它写的，缺了这一件只能发空帖。

    两路都要挡：`core.router` 整个是 None（`_build_attention` 之前那种
    半装配的 core），以及 router 在但 `light_adapter` 是 None（没配
    NOX_UTILITY 的部署）。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    no_router = FakeCore()
    no_router.router = None
    for core in (FakeCore(light_adapter=None), no_router):
        with caplog.at_level(logging.WARNING):
            caplog.clear()
            assert api_server._build_moments(core, FakeAttention(), FakeMeter()) is None
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING


def test_wired_returns_exactly_the_five_parts(monkeypatch):
    """配齐了就起，而且**五件都在**：键集合完全等于那五个。

    能挡什么：少一件（`run_post_loop(**parts)` 当场 TypeError，而那是
    开机才炸）；多一件 / 键名写错（`sessions` 写成 `session` 那种）。
    `issubset` 断不出这两样，所以这里断**相等**。

    ⚠️ 第二段是同一件事的另一面：`meter` 是个观测用的记账器，
    **没有它也得能起**（`agent/meter.py` 是「不接 = 不记账，行为不变」）——
    它不该成为 Moments 的第六个必需件。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    core, attn, meter = FakeCore(), FakeAttention(), FakeMeter()
    parts = api_server._build_moments(core, attn, meter)

    assert parts is not None
    assert set(parts) == {"store", "attention", "adapter_ref", "bridge", "sessions"}
    assert parts["store"] is attn.store
    assert parts["attention"] is attn
    assert parts["bridge"] is core.bridge
    assert parts["sessions"] is core.session_store
    assert meter.tags == [(LIGHT, "moments")], "utility 得贴任务名记账"
    assert parts["adapter_ref"] is meter.last

    bare = api_server._build_moments(core, attn, None)
    assert bare is not None and bare["adapter_ref"] is LIGHT


def test_wiring_exception_never_escapes(monkeypatch, caplog):
    """接线里出异常**不许往外抛** —— 抛出去就是 Core 起不来。

    2026-08-08 那次：`core.context.get("health")` 在假 core 上抛
    AttributeError，一次干掉 22 个测试，而且**只在线上活**
    （本地没有 `NOX_ATTENTION=1`，那段代码根本不执行）。
    Moments 是附加层，它起不来不该让他连话都说不了。

    能挡什么：try 的范围写成只包住最后那行 return（取件那句在 try 外），
    或者把 `logger.exception` 写成 `logger.warning` 之后静默返回。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    with caplog.at_level(logging.ERROR):
        caplog.clear()
        assert api_server._build_moments(ExplodingCore(), FakeAttention(), FakeMeter()) is None
    broke = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(broke) == 1, "炸了必须留痕（exception 级），不然是静默降级"
    assert broke[0].exc_info, "得带 traceback —— 不然只知道「没起来」"


# ---------------------------------------------------------------- 循环


def _spawn_loop(interval_s: float = 0.0) -> asyncio.Task:
    """起一条 `run_post_loop`。五件假货只要身份不同 —— 接线层不读字段。"""
    return asyncio.create_task(moments_loop.run_post_loop(
        store=object(), attention=object(), sessions=object(),
        bridge=object(), adapter_ref=object(), interval_s=interval_s,
    ))


async def _stop(task: asyncio.Task) -> None:
    """收摊。关机路径（`lifespan` 的 finally）就是这个形状。"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_post_loop_rereads_mode_every_round(monkeypatch):
    """🔴 `mode()` **每一轮现读**，不在启动时读一次存下来。

    能挡什么：把 `mode()` 提到循环外面存成局部变量。那样 shadow 转 on
    必须重启两次、日志里那行 `mode=` 说的是启动那一刻的模式，
    而 `post_tick` 真正在做的事和它记下来的对不上（shadow 变成
    「账上写着影子、人已经在发帖」）。
    挡不住什么：模式**有没有被用过** —— 那由第 6 条从消费侧断。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    seen: list[str] = []
    first, second = asyncio.Event(), asyncio.Event()

    def fake_post_tick(*, mode, **_kw):
        seen.append(mode)
        (first if len(seen) == 1 else second).set()
        return None

    monkeypatch.setattr(moments_loop, "post_tick", fake_post_tick)
    task = _spawn_loop(interval_s=0.05)
    try:
        await asyncio.wait_for(first.wait(), timeout=5)
        assert seen == ["shadow"], "第一轮该是 shadow"
        monkeypatch.setenv("NOX_MOMENTS", "on")
        await asyncio.wait_for(second.wait(), timeout=5)
    finally:
        await _stop(task)
    assert seen[:2] == ["shadow", "on"], (
        f"改了环境变量第二轮却还是 {seen[:2]!r} —— mode 是启动时读的"
    )


@pytest.mark.asyncio
async def test_run_post_loop_does_not_beat_twice(monkeypatch):
    """🔴 这一层收尾时**不许**再 beat，也不许把记录吞掉。

    T5 的第 15 条盯着「每条返回路径都 beat 一次」；这一层再打一次就是**双计**，
    台账里那条 `post_tick` 的 `count` 变成两倍 —— 而看门狗判的是
    「节奏还在不在」，计数偏了这条**唯一**能看出「它是不是还活着」的线就跟着错。

    ⚠️ 判据挑在**下一轮开工的那一刻**：这一轮的收尾（`record.log` 前后都可能
    被人加一句 beat）到那时一定已经跑完了。写在一轮中间断的话，加在
    `record.log` 后面的那句 beat 会在断言之后才执行 —— 那就是一条
    「看着在守，其实漏过去」的检查。

    后半段是同一件事的另一面：`post_tick` 返回的记录**得交给 logger**
    （shadow 的全部产出就是那几行）—— 拿到了不记，等于影子模式白跑。
    """
    beats: list[str] = []
    #: 每一轮开工时台账里的条数。第二轮开工时读到的那个数，
    #: 已经把第一轮**整轮**的收尾算进去了
    seen: list[int] = []
    logged: list[object] = []
    second = asyncio.Event()

    class FakeHeartbeat:
        def beat(self, job: str) -> None:
            beats.append(job)

    class FakeRecord:
        def log(self, logger) -> None:
            logged.append(self)

    monkeypatch.setattr(moments_loop, "heartbeat", FakeHeartbeat())
    monkeypatch.setenv("NOX_MOMENTS", "shadow")

    def fake_post_tick(*, mode, **_kw):
        #: 真 `post_tick` 的 `_finish` 就是这个形状：跑完一轮打一次
        moments_loop.heartbeat.beat(moments_loop.HEARTBEAT_JOB)
        seen.append(len(beats))
        if len(seen) >= 2:
            second.set()
        return FakeRecord()

    monkeypatch.setattr(moments_loop, "post_tick", fake_post_tick)
    task = _spawn_loop(interval_s=0)
    try:
        await asyncio.wait_for(second.wait(), timeout=5)
        assert seen[:2] == [1, 2], (
            f"第二轮开工时台账里有 {seen[1]} 次 beat —— 上一轮多打了一次"
        )
    finally:
        await _stop(task)
    assert beats and set(beats) == {moments_loop.HEARTBEAT_JOB}
    assert logged, "记录没交给 logger —— shadow 记下来的东西全丢了"


@pytest.mark.asyncio
async def test_run_post_loop_survives_a_bad_round(monkeypatch, caplog):
    """一轮炸了接着跑下一轮，而且要留一条 traceback。

    能挡什么：异常冒出去（`asyncio` 会把这条 Task 收掉，从此**再也不会**
    想发帖，而进程活着、`/health` 只有一条 stale）或者被吞成静默
    （`except: pass`）—— 后者的表现是「这条循环死了，日志一片干净」，
    正是 `obs/heartbeat.py` 开头说的那种最难查的坏。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    rounds: list[int] = []
    second = asyncio.Event()

    def fake_post_tick(*, mode, **_kw):
        rounds.append(len(rounds) + 1)
        if len(rounds) == 1:
            raise RuntimeError("第一轮炸")
        second.set()
        return None

    monkeypatch.setattr(moments_loop, "post_tick", fake_post_tick)
    task = _spawn_loop(interval_s=0)
    try:
        with caplog.at_level(logging.ERROR):
            await asyncio.wait_for(second.wait(), timeout=5)
    finally:
        await _stop(task)
    assert rounds[:2] == [1, 2], "第一轮炸完之后第二轮没跑 = 这条循环死在第 1 轮"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "炸了必须留痕"


@pytest.mark.asyncio
async def test_cancelled_error_is_not_swallowed(monkeypatch):
    """`CancelledError` 原样往外抛 —— 吞了的话关机就挂在这儿。

    `lifespan` 的 finally 是 `task.cancel()` + `await asyncio.gather(...)`：
    循环把取消吞掉、回头继续 `while True`，那次 gather 就永远等不到头，
    重启时表现为「关不掉的那个进程」。

    ⚠️ 用 `wait_for` 兜一层超时：这不是测「多久关掉」，是让「吞掉取消」
    这条变异体**红**而不是挂住整个测试会话。
    """
    monkeypatch.setenv("NOX_MOMENTS", "shadow")
    monkeypatch.setattr(moments_loop, "post_tick", lambda **_kw: None)
    task = _spawn_loop(interval_s=3600)
    await asyncio.sleep(0.05)          # 让它进到 sleep 里，停在取消点上
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


# ---------------------------------------------------------------- lifespan


def test_lifespan_uses_the_factory():
    """接线的三样字面量真的在 `api/server.py` 里，而且顺序对。

    🔴 **这是弱判据，只能挡「工厂写了但没人调」。** 它读的是源码文本，
    证明不了运行时真的起了循环，也**挡不住「调了但参数传错」** ——
    参数那件事由第 6 条（键集合完全相等）和第 8 条（每轮现读 mode，
    从消费侧真的跑了一轮）断言。

    它挡得住的坏法只有三种，但三种都真的发生过：

      · `_build_moments` 写完挂着没人调（`attention` 那次挂了就没有任何
        症状：日志干净、`/health` 全绿、那条线一动不动）
      · `declare_heartbeat()` 漏掉（台账里根本没这一行，看门狗不会看它）
      · `declare_heartbeat()` 写在起循环**后面**（一条从没成功过的循环
        在台账里不存在，而那恰恰是最该看见的坏，审计 1.4）
    """
    src = Path(api_server.__file__).read_text(encoding="utf-8")
    assert "def _build_moments(" in src, "工厂被改名/删了"
    assert src.count("_build_moments(") >= 2, "工厂定义了，但 lifespan 里没人调"

    #: ⚠️ 只扫 `lifespan` 那一截。扫整个文件的话，别处（比如工厂自己的
    #: docstring）提到的同一个名字会让「顺序」这种判断当场失效 ——
    #: 一个恒真的判据比没有判据更坏，它看起来像在守着什么
    body = src[src.index("async def lifespan"):]
    assert "_build_moments(" in body, "lifespan 里没有调用"
    assert "declare_heartbeat()" in body, "缺 declare —— 台账里不会有这一行"
    assert "run_post_loop(" in body, "循环压根没起"
    assert body.index("declare_heartbeat()") < body.index("run_post_loop("), \
        "🔴 顺序反了：必须先 declare 再起循环（审计 1.4）"
