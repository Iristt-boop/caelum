"""两处共享状态的并发安全（审计 2.2）。

## 谁会同时碰它们

FastAPI 的 `def` 端点跑在**线程池**里，Attention 的两条后台循环靠
`asyncio.to_thread` 进来。所以「她手机和桌面同时聊 + 后台恰好在跑」
这三件事会同时落在不同线程上。

    AttentionRegistry._items   心跳 prune/list 在遍历，对话 upsert 在加键
    Sessions._cache            多个会话同时 get/put，LRU 顺序在被改

## 🔴 我第一版测试是假的，这段记下来

第一版写了一堆"多线程猛跑，别崩"的压测，8 条全绿 ——
然后**把锁换成空壳，8 条还是全绿**。也就是它们什么都没证明。

查下来是两件事叠在一起：

1. **挡住崩溃的不是锁，是我顺手加的 `list()` 拷贝。** 实测：

       list(d.values())          → 不崩（一次 C 调用，GIL 下原子）
       [v for v in d.values()]   → 抛 RuntimeError

2. **锁保的是"复合操作"**（读-改-写、先算名单再删、写完再换出），
   而那类错在 CPython 的 GIL 下**很难靠随机压测撞出来**。

所以这份测试分成两类，各证各的：

    结构类   物化快照还在不在（挡崩溃的那道）
    互斥类   用 barrier 卡在临界区里，看另一个线程是不是**真的被挡住**
             （挡住 = 锁生效；没挡住 = 锁没生效。确定性的，不靠撞运气）

压测仍然留着几条 —— 它们证明不了锁，但能跑通真实调用路径，
接线错了照样会红。**别再把它们当成锁的判据。**
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.events import ExperienceEvent  # noqa: E402
from attention import registry as registry_mod  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

WORKERS = 8
ROUNDS = 300
#: 等"另一个线程有没有冲进来"的窗口。给 0.4 秒 ——
#: 短了会把慢机器上的正常调度误判成"被锁挡住了"。
PROBE_S = 0.4


def _run(fns) -> list[BaseException]:
    errors: list[BaseException] = []
    lock = threading.Lock()

    def wrap(fn):
        def inner():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 —— 就是要抓住它
                with lock:
                    errors.append(exc)
        return inner

    threads = [threading.Thread(target=wrap(f)) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return errors


# ═══════════════════════════════ 互斥（锁真的生效吗）

def test_registry_lock_actually_excludes():
    """🔴 **这条是锁的判据。**

    做法：让一个线程卡在 `upsert()` 的临界区里不出来，然后看另一个线程
    调 `get()` 会不会被挡住。挡住 = 锁生效；冲进去 = 锁是摆设。

    卡住的办法是 patch `registry.logger.info` —— 它就在 `upsert` 持锁的
    那段里被调用。不改生产代码，也不依赖任何时序运气。
    """
    reg = AttentionRegistry()
    inside = threading.Event()    # 写线程已经进临界区了
    release = threading.Event()   # 放它走
    got_in = threading.Event()    # 读线程拿到锁了

    real_info = registry_mod.logger.info

    def blocking_info(*a, **kw):
        real_info(*a, **kw)
        inside.set()
        release.wait(timeout=10)

    registry_mod.logger.info = blocking_info
    try:
        writer = threading.Thread(target=lambda: reg.upsert("卡住", 0.9, now=T0))
        writer.start()
        assert inside.wait(timeout=5), "写线程没进到临界区，这条测试本身坏了"

        def reader():
            reg.get("卡住")
            got_in.set()

        r = threading.Thread(target=reader)
        r.start()
        # 锁生效的话，reader 在这段时间里进不来
        assert not got_in.wait(timeout=PROBE_S), (
            "🔴 写线程正持着锁，读线程却冲进去了 —— 锁没生效"
        )

        release.set()
        assert got_in.wait(timeout=5), "放开之后读线程该进得来"
        writer.join(timeout=5)
        r.join(timeout=5)
    finally:
        registry_mod.logger.info = real_info
        release.set()


def test_sessions_lock_actually_excludes():
    """同上，换 `Sessions`。卡点选 `now_cst()` —— 它在 `put()` 的锁里面。"""
    from agent.llm import Message
    from api import server as server_mod

    sess = _sessions()
    inside = threading.Event()
    release = threading.Event()
    got_in = threading.Event()

    real_now = server_mod.now_cst
    first = threading.Event()

    def blocking_now():
        v = real_now()
        # 只卡第一次 —— 卡所有次会把 reader 自己也锁死
        if not first.is_set():
            first.set()
            inside.set()
            release.wait(timeout=10)
        return v

    server_mod.now_cst = blocking_now
    try:
        w = threading.Thread(
            target=lambda: sess.put("卡住", [Message(role="user", text="x")]))
        w.start()
        assert inside.wait(timeout=5), "写线程没进到临界区，这条测试本身坏了"

        def reader():
            len(sess)
            got_in.set()

        r = threading.Thread(target=reader)
        r.start()
        assert not got_in.wait(timeout=PROBE_S), (
            "🔴 put 正持着锁，另一个线程却读进去了 —— 锁没生效"
        )

        release.set()
        assert got_in.wait(timeout=5)
        w.join(timeout=5)
        r.join(timeout=5)
    finally:
        server_mod.now_cst = real_now
        release.set()


# ═══════════════════════════════ 结构（物化快照还在不在）

@pytest.mark.parametrize("method", ["list", "to_list", "prune"])
def test_registry_iterations_are_materialised(method):
    """🔴 遍历前必须先物化一份快照。

    `list(d.values())` 是一次 C 调用、GIL 下原子；
    `[a for a in d.values()]` 会被切走，抛 `dictionary changed size
    during iteration`。实测过这两者的区别，不是听说的。

    这条是**源码结构检查** —— 有点笨，但它钉的东西没有别的办法钉：
    去掉那层 `list()` 不会让任何行为测试变红，只会让线上偶尔炸一次。
    """
    import inspect

    src = inspect.getsource(getattr(AttentionRegistry, method))
    assert "list(self._items" in src, (
        f"{method}() 里没有 `list(self._items…)` 的物化快照 —— "
        "直接遍历 dict 在并发下会抛 RuntimeError"
    )


# ═══════════════════════════════ 压测（跑通真实路径，不是锁的判据）

def test_registry_stress_upsert_list_prune():
    """一边加、一边列、一边清。证明不了锁，但接线错了会红。"""
    reg = AttentionRegistry()
    for i in range(200):
        reg.upsert(f"旧-{i}", 0.05, decay="fast", now=T0)
    later = T0 + timedelta(days=30)

    def adder(tag):
        def go():
            for i in range(ROUNDS):
                reg.upsert(f"{tag}-{i}", 0.9, now=later)
        return go

    def reader():
        for _ in range(ROUNDS):
            reg.list(now=later)
            reg.to_list()

    def pruner():
        for _ in range(ROUNDS):
            reg.prune(now=later)

    errors = _run([adder(f"新{n}") for n in range(3)] + [reader, reader, pruner])
    assert not errors, f"并发下炸了：{errors[:3]}"


def test_registry_still_correct_after_stress():
    """跑完数据要是对的 —— 不崩不等于没写坏。"""
    reg = AttentionRegistry()

    def writer(tag):
        def go():
            for i in range(100):
                reg.upsert(f"{tag}-{i}", 0.7, now=T0)
        return go

    errors = _run([writer(f"w{n}") for n in range(WORKERS)])
    assert not errors
    assert len(reg) == WORKERS * 100, f"少了几条：{len(reg)}"
    for n in range(WORKERS):
        a = reg.get(f"w{n}-0")
        assert a is not None and a.strength == pytest.approx(0.7)


def test_engine_handle_concurrently():
    """从 Engine 压 —— 生产走的是这条路，不是直接调 Registry。"""
    from attention.engine import AttentionEngine
    from attention.evaluator import AttentionEvaluator
    from attention.relationship import RelationshipState

    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
        store=None,
    )

    def chatter(tag):
        def go():
            for i in range(150):
                engine.handle(ExperienceEvent(
                    source="chat", type="message",
                    payload={"text": f"{tag} 第 {i} 句"},
                ))
        return go

    def pruner():
        for _ in range(150):
            engine.prune()

    errors = _run([chatter(f"c{n}") for n in range(4)] + [pruner, pruner])
    assert not errors, f"Engine 并发下炸了：{errors[:3]}"


class _FakeStore:
    """只够 Sessions 用的假库。故意**不加自己的锁** ——
    加了就变成在测那把锁，而不是在测 Sessions 自己的。"""

    def __init__(self):
        self.rows: dict[str, list] = {}

    def load(self, sid, limit=None, recent_window_tokens=None):
        return list(self.rows.get(sid, []))

    def sync(self, sid, history):
        self.rows[sid] = list(history)
        return 0

    def get_summary(self, sid):
        return None

    def drop(self, sid):
        return self.rows.pop(sid, None) is not None


def _sessions(max_sessions=16):
    from api.server import Sessions

    return Sessions(_FakeStore(), history_limit=40, max_sessions=max_sessions)


def test_sessions_stress_put_get():
    """手机和桌面一起聊的形状。"""
    from agent.llm import Message

    sess = _sessions()

    def worker(tag):
        def go():
            for i in range(ROUNDS):
                sid = f"{tag}-{i % 5}"
                sess.put(sid, [Message(role="user", text=f"{i}")])
                sess.get(sid)
                len(sess)
        return go

    errors = _run([worker(f"t{n}") for n in range(WORKERS)])
    assert not errors, f"Sessions 并发下炸了：{errors[:3]}"


def test_sessions_eviction_keeps_the_two_maps_in_step():
    """换出时 `_cache` 和 `_cached_on` 必须同进同出。

    分开改会留下**只在 `_cached_on` 里的孤儿键** —— 不报错、不影响这一轮，
    只是慢慢漏内存，而且没人会发现。
    """
    from agent.llm import Message

    sess = _sessions(max_sessions=8)

    def worker(tag):
        def go():
            for i in range(ROUNDS):
                sess.put(f"{tag}-{i}", [Message(role="user", text="x")])
        return go

    errors = _run([worker(f"t{n}") for n in range(WORKERS)])
    assert not errors
    assert len(sess) <= 8, f"换出没生效，缓存有 {len(sess)} 条"
    assert set(sess._cache) == set(sess._cached_on), (
        "🔴 _cache 和 _cached_on 对不上 —— 换出时没同步，"
        f"孤儿键：{set(sess._cached_on) - set(sess._cache)}"
    )


def test_sessions_drop_while_reading():
    """`DELETE /session/{sid}` 和聊天同时来。"""
    from agent.llm import Message

    sess = _sessions()
    for i in range(20):
        sess.put(f"s{i}", [Message(role="user", text="x")])

    def reader():
        for _ in range(ROUNDS):
            for i in range(20):
                sess.get(f"s{i}")

    def dropper():
        for _ in range(ROUNDS):
            for i in range(20):
                sess.drop(f"s{i}")

    def refiller():
        for _ in range(ROUNDS):
            for i in range(20):
                sess.put(f"s{i}", [Message(role="user", text="y")])

    errors = _run([reader, reader, dropper, refiller])
    assert not errors, f"drop 并发下炸了：{errors[:3]}"
