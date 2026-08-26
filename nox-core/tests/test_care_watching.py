"""她在看片时 Care 闭嘴（共影 P1，2026-08-22）。

守三件事：

  1. 她在看片 → **所有线**都拦，包括续链的追问
  2. 读不到观影状态 → **放行**，不是拦。反过来做的话 bridge 抖一下他就整晚
     闭嘴，而且日志里只有一片安静，和「他今天没什么想说的」长得一模一样
  3. 被拦的这几次要**进账本**（BLOCK），她能看见「3 次因为你在看电影没说」
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.care.ledger import BLOCK, CareLedger  # noqa: E402
from attention.care.orchestrator import CareOrchestrator  # noqa: E402
from attention.care.signal import TASK, CareSignal, ThreadBook  # noqa: E402
from attention.care.watching import WatchingCheck  # noqa: E402

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 22, 21, 30, tzinfo=CST)


class _Res:
    def __init__(self, ok=True, data=None, error=""):
        self.ok, self.data, self.error = ok, data, error


class _FakeBridge:
    """假 bridge。`calls` 用来数请求次数（验缓存）。"""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def get(self, path):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _watching_res(title="盗梦空间", episode=""):
    return _Res(data={"watching": True,
                      "session": {"title": title, "episode": episode}})


_NOT_WATCHING = _Res(data={"watching": False, "session": None})


def _orchestrator(watching, spoke=True):
    said = []

    def deliver(signal, thread, now):
        said.append(signal.subject)
        return spoke

    orch = CareOrchestrator(ThreadBook(), deliver, watching=watching,
                            ledger=CareLedger())
    return orch, said


# ---------------------------------------------------------------- 拦


def test_她在看片就不说():
    check = WatchingCheck(_FakeBridge(_watching_res()))
    orch, said = _orchestrator(check)
    orch.submit(CareSignal(source="random", subject="忽然想起你"))
    out = orch.run(NOW)

    assert said == [], "她在看片，一个字都不该说"
    assert out[0].action == "dropped"
    assert "盗梦空间" in out[0].reason, "理由要说清是哪一部，不然账本上看不出为什么"


def test_续链的追问也拦():
    """**这条是这次改动的重点。**

    抑制放在开新链那一段的话，「到点追待办」会照常在她看到高潮时冒出来 ——
    那正是最烦的一种打断。所以判断必须在续链之前。
    """
    check = WatchingCheck(_FakeBridge(_watching_res()))
    orch, said = _orchestrator(check)
    # 先在不看片的时候开一条 TASK 链
    orch.watching = None
    orch.submit(CareSignal(source="todo", subject="背单词", thread_kind=TASK))
    first = orch.run(NOW)
    assert first[0].action == "spoke"
    tid = first[0].thread.id

    # 她开始看片，同一条链的下一步
    orch.watching = check
    orch.submit(CareSignal(source="todo", subject="背单词",
                           thread_kind=TASK, thread_id=tid))
    out = orch.run(NOW + timedelta(hours=2))
    assert said == ["背单词"], "续链那次不该说出口"
    assert out[0].action == "dropped"


def test_被拦的进账本记成BLOCK():
    check = WatchingCheck(_FakeBridge(_watching_res()))
    orch, _ = _orchestrator(check)
    for _ in range(3):
        orch.submit(CareSignal(source="random", subject="想你了"))
        orch.run(NOW)

    summary = orch.ledger.summary()
    assert summary["blocked"] == 3
    assert summary["spoke"] == 0
    # considered = spoke + skipped + blocked（糖糖定的公式）
    assert summary["considered"] == 3


# ---------------------------------------------------------------- 放行


def test_她没在看片就照常说():
    orch, said = _orchestrator(WatchingCheck(_FakeBridge(_NOT_WATCHING)))
    orch.submit(CareSignal(source="random", subject="想你了"))
    assert orch.run(NOW)[0].action == "spoke"
    assert said == ["想你了"]


def test_读不到就放行不是拦():
    """⚠️ **最重要的一条。**

    拦的方向搞反的话，bridge 抖一下他就整晚不出声 —— 而沉默是这套系统里
    最难排查的故障：日志里没有异常，表现和「他今天没什么想说的」一模一样。
    """
    orch, said = _orchestrator(WatchingCheck(_FakeBridge(_Res(ok=False, error="连不上"))))
    orch.submit(CareSignal(source="random", subject="想你了"))
    assert orch.run(NOW)[0].action == "spoke", "读不到观影状态时必须放行"
    assert said == ["想你了"]


def test_bridge直接抛异常也放行():
    orch, said = _orchestrator(WatchingCheck(_FakeBridge(RuntimeError("炸了"))))
    orch.submit(CareSignal(source="random", subject="想你了"))
    assert orch.run(NOW)[0].action == "spoke"


def test_没接共影时行为不变():
    """`watching=None` = 没配 bridge。整条线不存在，和接共影之前一样。"""
    orch, said = _orchestrator(None)
    orch.submit(CareSignal(source="random", subject="想你了"))
    assert orch.run(NOW)[0].action == "spoke"


# ---------------------------------------------------------------- 缓存


def test_短时间内只问一次bridge():
    """快循环 60 秒一轮，一轮里可能判好几个念头。不缓存的话一轮打好几次 HTTP。"""
    bridge = _FakeBridge(_NOT_WATCHING)
    check = WatchingCheck(bridge, ttl_s=20)
    for _ in range(5):
        check(NOW)
    assert bridge.calls == 1

    # 过了 TTL 要重新问 —— 不然她看完了他还闭嘴
    check(NOW + timedelta(seconds=21))
    assert bridge.calls == 2


def test_没有片名时也要说人话():
    check = WatchingCheck(_FakeBridge(_Res(data={"watching": True, "session": {}})))
    reason = check(NOW)
    assert reason, "在看片就得给出拦截理由"
    assert "None" not in reason and "{}" not in reason


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
