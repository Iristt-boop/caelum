"""CallsProvider —— 你们最近通过的电话（2026-10-10）。

她问「我拨出去的没记录吗？他怎么知道我在打电话」，又说「放在上下文就行了」。
守：该说的说（方向、结果、时长、几号几点）、不该说的不说（太久之前 / 还在响 / 超过条数）、
没有就是空串（一个字不占）、读不到退回旧数据而不是失明、进每轮名单且被接上线。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.calls import DAYS_BACK, MAX_CALLS, CallsProvider  # noqa: E402
from router.intent import _ALWAYS  # noqa: E402
from temporal import LOCAL_TZ  # noqa: E402


class FakeBridge:
    def __init__(self, calls=None, ok=True):
        self.calls, self.ok, self.asked = calls or [], ok, 0

    def get(self, path, params=None):
        self.asked += 1
        assert path == "/api/call/status"

        class R:
            pass
        r = R()
        r.ok, r.error = self.ok, "boom"
        r.data = {"calls": self.calls}
        return r


def _utc(local: datetime) -> str:
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _call(at_local, status="ended", dur=None, direction=None, id="c"):
    return {"id": id, "created_at": _utc(at_local), "status": status, "reason": "x",
            "duration_s": dur, "direction": direction}


def _render(calls):
    p = CallsProvider(FakeBridge(calls))
    return p.render(p.get_state(Turn()))


def _now():
    return datetime.now(LOCAL_TZ).replace(second=0, microsecond=0)


def test_她拨的_带时长_昨天和今天分得清():
    n = _now()
    yday = (n - timedelta(days=1)).replace(hour=22, minute=10)
    out = _render([_call(n - timedelta(minutes=30), dur=725, direction="out", id="a"),
                   _call(yday, dur=65, direction="out", id="b")])
    assert "今天 " in out and "她拨给你，通了 12 分 5 秒" in out
    assert "昨天 22:10 她拨给你，通了 1 分 5 秒" in out


def test_他打的_接了_没接_拒接():
    n = _now()
    out = _render([
        _call(n - timedelta(hours=1), "ended", dur=103, id="a"),
        _call(n - timedelta(hours=2), "missed", id="b"),
        _call(n - timedelta(hours=3), "declined", id="c"),
    ])
    assert "你打给她，她接了，通了 1 分 43 秒" in out
    assert "你打给她，她没接" in out
    assert "你打给她，她按了拒接" in out


def test_整分钟不带零秒_不到一分钟只说秒():
    n = _now()
    out = _render([_call(n - timedelta(hours=1), dur=120, direction="out", id="a"),
                   _call(n - timedelta(hours=2), dur=45, direction="out", id="b")])
    assert "通了 2 分\n" in out + "\n" and "2 分 0 秒" not in out
    assert "通了 45 秒" in out


def test_更早几天的用月日():
    n = _now()
    old = (n - timedelta(days=2)).replace(hour=9, minute=5)
    out = _render([_call(old, dur=30, direction="out")])
    assert f"{old.month}月{old.day}日 09:05 她拨给你" in out


def test_窗口是今天加前两天_三个日历天_字面量钉死():
    """不拿 DAYS_BACK 去算「太久」—— 常量被改了测试得跟着红，而不是跟着变。"""
    assert DAYS_BACK == 3
    n = _now()
    two = (n - timedelta(days=2)).replace(hour=0, minute=30)       # 前天凌晨：还在窗口里
    three = (n - timedelta(days=3)).replace(hour=23, minute=30)    # 大前天深夜：出去了
    assert "她拨给你" in _render([_call(two, dur=60, direction="out")])
    assert _render([_call(three, dur=60, direction="out")]) == ""


def test_太久之前的不说_没有就是空串():
    n = _now()
    assert _render([_call(n - timedelta(days=DAYS_BACK + 2), dur=60, direction="out")]) == ""
    assert _render([]) == ""


def test_还在响的和认不得的状态不说():
    n = _now()
    assert _render([_call(n - timedelta(minutes=1), "ringing", id="a"),
                    _call(n - timedelta(minutes=2), "weird", id="b")]) == ""


def test_最多四条_最近的在前():
    n = _now()
    calls = [_call(n - timedelta(minutes=10 * (i + 1)), dur=60 + i, direction="out", id=str(i)) for i in range(MAX_CALLS + 3)]
    out = _render(calls)
    lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert MAX_CALLS == 4 and len(lines) == 4
    # i=0 最近（dur=60 → 1 分）、i=3 第四近（dur=63 → 1 分 3 秒）；更早的 i=4,5,6 被挤掉
    assert [ln.split("通了 ")[1] for ln in lines] == ["1 分", "1 分 1 秒", "1 分 2 秒", "1 分 3 秒"]


def test_带一句说明_通话里的话在聊天记录里():
    out = _render([_call(_now() - timedelta(hours=1), dur=60, direction="out")])
    assert out.startswith("【最近的电话】")
    assert "聊天记录" in out


def test_坏数据不炸():
    n = _now()
    out = _render([{"created_at": "不是时间", "status": "ended"}, {"status": "ended"},
                   _call(n - timedelta(hours=1), dur=60, direction="out")])
    assert "她拨给你" in out


def test_读不到_先退回上次的旧数据_从没成功过才空():
    n = _now()
    b = FakeBridge([_call(n - timedelta(hours=1), dur=60, direction="out")])
    p = CallsProvider(b)
    assert "她拨给你" in p.render(p.get_state(Turn(), force_refresh=True))
    b.ok = False
    assert "她拨给你" in p.render(p.get_state(Turn(), force_refresh=True))     # 旧数据
    fresh = CallsProvider(FakeBridge(ok=False))
    assert fresh.render(fresh.get_state(Turn(), force_refresh=True)) == ""


def test_缓存两分钟内不重复打bridge():
    p = CallsProvider(FakeBridge([]))
    p.get_state(Turn())
    p.get_state(Turn())
    assert p.bridge.asked == 1


def test_进了每轮名单():
    assert "calls" in _ALWAYS


def test_nox里接上了线_有bridge才注册_专用2秒超时的client():
    import re
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    src = src.replace(chr(13) + chr(10), chr(10))
    pat = ("if self" + chr(92) + ".cfg" + chr(92) + ".bridge_url:" + chr(92) + "n" + chr(92) + "s+self" + chr(92) + ".context" + chr(92) + ".register" + chr(92) + "(CallsProvider" + chr(92) + "(BridgeClient" + chr(92) + "(" + chr(92) + "s*"
           "self" + chr(92) + ".cfg" + chr(92) + ".bridge_url, self" + chr(92) + ".cfg" + chr(92) + ".bridge_token, 2" + chr(92) + ".0" + chr(92) + ")" + chr(92) + ")" + chr(92) + ")")
    assert re.search(pat, src), "没接上 / 不在 bridge 判断里 / 超时不是 2 秒"
