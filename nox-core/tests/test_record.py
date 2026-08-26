"""生理期的主动记录（2026-08-19）。

HealthKit 那条同步坏了（经期表停在 7-24，flow_level 被快捷指令写成一堆换行），
改成他在对话里记 + 她在 App 里填，两条路写同一个 World Model。

⚠️ **体重不在这里** —— `tools/diet.py` 的 `log_weight` 早就有了。
2026-08-19 差点写成第二个，见 `tools/record.py` 开头那段。

钉住的重点是**别把错数据写进事实库** —— 它会一直躺在那儿，
而且以后他还要拿它去算趋势。
"""

from __future__ import annotations

from tools.record import PERIOD, make_handlers


class FakeWorld:
    def __init__(self, boom=False):
        self.written = []
        self.boom = boom

    def observe(self, **kw):
        if self.boom:
            raise RuntimeError("库炸了")
        self.written.append(kw)


def _h(world=None):
    return make_handlers(world if world is not None else FakeWorld())


# ---------------------------------------------------------------- 生理期


def test_记下来了():
    w = FakeWorld()
    out = _h(w)["record_period"]({"event": "start", "flow": "量少", "date": "2026-08-19"})
    assert "来了" in out
    assert w.written[0]["type"] == PERIOD
    assert w.written[0]["observed"]["event"] == "start"
    assert w.written[0]["observed"]["flow"] == "量少"


def test_记下结束了():
    out = _h()["record_period"]({"event": "end", "date": "2026-08-19"})
    assert "结束了" in out


def test_回执提醒他别追问():
    """这是很私人的事 —— 她要的是被记住，不是被科普。"""
    out = _h()["record_period"]({"event": "start"})
    assert "别追问" in out


def test_认不出的event不记():
    w = FakeWorld()
    assert "只能是" in _h(w)["record_period"]({"event": "maybe"})
    assert w.written == []


def test_start和end同一天不互相覆盖():
    """同一天既开始又结束是可能的（她记晚了补一条），别让后者盖掉前者。"""
    w = FakeWorld()
    h = _h(w)
    h["record_period"]({"event": "start", "date": "2026-08-19"})
    h["record_period"]({"event": "end", "date": "2026-08-19"})
    assert len({x["dedup_key"] for x in w.written}) == 2


# ---------------------------------------------------------------- 韧性


def test_没有world就如实说记不了():
    assert "记不了" in make_handlers(None)["record_period"]({"event": "start"})


def test_写失败要如实说不能假装记上了():
    assert "没记成" in _h(FakeWorld(boom=True))["record_period"]({"event": "start"})


def test_只注册了一个工具():
    """⚠️ 这条是拿来防重复的：体重有 `tools/diet.py` 的 `log_weight`，
    这里再加一个「记体重」就是两个工具做同一件事 ——
    模型看到的是扁平的工具表，会随机挑一个。"""
    assert set(make_handlers(None)) == {"record_period"}
