"""历史只往后追加，前缀缓存才命中得上（2026-10-05）。

她问「缓存还能优化吗」。查出来的：
  · 会话缓存每轮只留最后 60 条 —— 她的会话早超了，每说一轮开头就被挤掉几条，
    前缀缓存只认「从开头起一模一样」，于是历史那段**每轮都按原价重算**，
    命中的永远只有人设 + 工具那 3 万（她看到的 55~65%）
  · 压缩量的是**全部**历史（八千多条，永远超预算）→ 窗口外每多几条就压一次，
    每压一次摘要就变，摘要在最前面，整段缓存作废

改法照 usewhale/Whale：模型看到「摘要 + 水位之后的全部原文」，两次压缩之间只往后长；
攒满预算才压一次。每轮的日志带一串 shape，命中掉了能看出是哪块变了。
"""

from __future__ import annotations

import logging

import pytest

from agent.llm import Message
from agent.loop import cache_shape
from api.server import Sessions
from context.compactor import plan_compaction
from data.store import Store


def _msgs(n: int, text: str = "今天也想你" * 4) -> list[Message]:
    return [Message(role="user" if i % 2 == 0 else "assistant", text=f"{text} #{i}") for i in range(n)]


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


# ---------------------------------------------------------------- 压缩：只量水位之后

def test_压缩只看水位之后那段_老历史再长也不触发():
    full = _msgs(400)
    assert plan_compaction(full, recent_window_tokens=200, budget_tokens=8000, already=0).should_compact
    plan = plan_compaction(full, recent_window_tokens=200, budget_tokens=8000, already=396)
    assert not plan.should_compact, "水位之后只有 4 条，八千多条的老历史不该让它每轮都压"


def test_水位之后攒满了照样压():
    full = _msgs(400)
    plan = plan_compaction(full, recent_window_tokens=200, budget_tokens=8000, already=10)
    assert plan.should_compact
    assert plan.compressible[0] is full[10], "从水位开始压，不重压摘要里已有的"


# ---------------------------------------------------------------- 库：水位之后的全部原文

def test_读库_摘要加水位之后的全部原文(store):
    store.append("s1", _msgs(120))
    store.set_summary("s1", "她这周在外地", 100)
    got = store.load_window("s1", 100, cap_tokens=100_000)
    assert got[0].role == "system" and "她这周在外地" in got[0].text
    assert len(got) == 1 + 20
    assert got[-1].text.endswith("#119")


def test_读库_超过安全上限才截_而且报警(store, caplog):
    store.append("s1", _msgs(300))
    caplog.set_level(logging.WARNING)
    got = store.load_window("s1", 0, cap_tokens=500)
    assert 0 < len(got) < 300
    assert got[-1].text.endswith("#299")
    assert any("安全上限" in m for m in caplog.messages)


# ---------------------------------------------------------------- 会话缓存：只往后长

def _turn(history: list[Message], i: int) -> list[Message]:
    """模拟一轮：拿到的历史 + 她一句 + 他一句（loop 回来的 messages 就是这个形状）"""
    return [*history, Message(role="user", text=f"她第{i}句"), Message(role="assistant", text=f"他第{i}句")]


def test_聊了很多轮_历史开头一直不变(store):
    store.append("s1", _msgs(80))
    sess = Sessions(store, history_limit=40, cap_tokens=100_000)
    first_head = None
    for i in range(30):                      # 原来每轮只留最后 60 条：第 1 轮开头就开始挪
        h = sess.get("s1")
        head = [(m.role, m.text) for m in h if m.role != "system"][:2]
        first_head = first_head or head
        assert head == first_head, f"第 {i} 轮开头变了 —— 前缀缓存每轮失配"
        sess.put("s1", _turn(h, i))
    assert len(sess.get("s1")) >= 80 + 60, "历史被截了"


def test_前缀形状_相邻两轮只有条数在涨(store):
    store.append("s1", _msgs(80))
    sess = Sessions(store, history_limit=40, cap_tokens=100_000)
    h1 = sess.get("s1")
    sess.put("s1", _turn(h1, 1))
    h2 = sess.get("s1")
    s1, s2 = cache_shape("人设", [], h1), cache_shape("人设", [], h2)
    assert s1.split("+")[0] == s2.split("+")[0]
    assert s1 != s2


def test_内存窗口超安全上限才截_而且报警(store, caplog):
    store.append("s1", _msgs(10))
    sess = Sessions(store, history_limit=40, cap_tokens=300)
    caplog.set_level(logging.WARNING)
    sess.put("s1", _msgs(200))
    assert len(sess.get("s1")) < 200
    assert any("安全上限" in m for m in caplog.messages)


def test_压缩落地后扔掉缓存_下一轮按新水位重读(store):
    store.append("s1", _msgs(120))
    sess = Sessions(store, history_limit=40, cap_tokens=100_000)
    assert len(sess.get("s1")) == 120
    store.set_summary("s1", "前面聊了很多", 100)
    sess.invalidate("s1")
    got = sess.get("s1")
    assert got[0].role == "system" and "前面聊了很多" in got[0].text
    assert len(got) == 1 + 20, "已经折进摘要的那段不该再原文出现一遍"


def test_不开只追加的老用法不变(store):
    """命令行 / 老测试不传 cap：还是读库按 limit 截、缓存留最后 60 条。"""
    store.append("s1", _msgs(100))
    sess = Sessions(store, history_limit=40)
    assert len(sess.get("s1")) == 40
    sess.put("s1", _msgs(100))
    assert len(sess.get("s1")) == 60


# ---------------------------------------------------------------- 体检日志

def test_形状认得出是哪块变了():
    hist = _msgs(10)
    base = cache_shape("人设", [], hist)
    assert cache_shape("人设改了", [], hist).split()[0] != base.split()[0]
    head_fp = lambda s: s.split()[2].split("+")[0]   # noqa: E731 —— 只比指纹，不比条数
    assert head_fp(cache_shape("人设", [], hist[2:])) != head_fp(base), "开头被删了要看得出来"
    assert cache_shape("人设", [], hist + _msgs(2)).split()[2].split("+")[0] == base.split()[2].split("+")[0]


def test_每轮日志带上形状(caplog):
    from agent.loop import LoopResult, Usage, log_turn
    caplog.set_level(logging.INFO)
    r = LoopResult(outcome="answered", text="嗯", iterations=1, usage=Usage(), messages=[])
    log_turn(r, model="m", elapsed_s=0.1, history_len=0, stream=True, shape="sys=aaaaaa tools=bbbbbb/3 hist=cccccc+9")
    assert any("shape=sys=aaaaaa tools=bbbbbb/3 hist=cccccc+9" in m for m in caplog.messages)
