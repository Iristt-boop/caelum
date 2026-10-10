"""面前 Source（stackchan 人脸识别 → 「她坐到他面前了」）的测试。

钉的原则：

- arrived 只在**跃迁那一下**产念头 —— tracker 重启重放不算新跃迁
- left 清状态、不产念头（追出门是位置源的事，不是他的事）
- looking 是心跳：只刷新鲜度，**不产念头**（她一直在面前不是新消息）
- identity 跃迁（在场时认出的脸变了）只同步身份，不产念头 ——
  单人环境里这多数是识别断续（null↔iris 反复横跳），不是新闻
- **心跳断了必须降级成「不知道」** —— 位置源 2026-09-22 那课
  （数据断了 14 小时还当成在外面的同款病，这里不许再犯）
- 状态跨重启活着（不然一重启就白追一轮）
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attention.sources.front import FRESH_WINDOW, FrontSource  # noqa: E402


class FakeStore:
    def __init__(self):
        self.data: dict = {}

    def set_source_state(self, key, value):
        self.data[key] = value

    def get_source_state(self, key):
        return self.data.get(key)


def _src() -> FrontSource:
    return FrontSource(FakeStore())


def _t(minute: int = 0) -> datetime:
    return datetime(2026, 9, 24, 20, minute, tzinfo=timezone.utc)


def test_arrived_产一个念头():
    src = _src()
    out = src.observe_event("arrived", "iris", _t())
    assert len(out) == 1
    assert out[0].source == "stackchan"
    assert out[0].subject == "她来到他面前"
    assert out[0].payload["identity"] == "iris"


def test_arrived_重复推_不算新跃迁():
    src = _src()
    assert len(src.observe_event("arrived", "iris", _t())) == 1
    assert src.observe_event("arrived", "iris", _t(1)) == []


def test_left_清状态_不产念头():
    src = _src()
    src.observe_event("arrived", "iris", _t())
    assert src.observe_event("left", None, _t(5)) == []
    assert src.present is False
    assert src.identity is None


def test_looking_心跳_刷新新鲜度不产念头():
    src = _src()
    src.observe_event("arrived", "iris", _t())
    out = src.observe_event("looking", "iris", _t(1))
    assert out == []
    assert src.updated_at == _t(1)


def test_looking_比arrived先到_当arrived用():
    """tracker 重启后心跳先于 arrived 到 —— 不能把「她在」当成没发生。"""
    src = _src()
    assert src.observe_event("looking", "iris", _t()) == []
    assert src.present is True


def test_心跳断了_降级成不知道():
    src = _src()
    src.observe_event("arrived", "iris", _t())
    now = _t() + FRESH_WINDOW + timedelta(minutes=1)
    snap = src.snapshot(now=now)
    assert snap["present"] is True       # 账面上还在
    assert snap["effective"] is False    # 但下游该信的是「不知道」
    assert snap["identity"] is None      # 断了心跳不许再报「iris 在旁边」


def test_心跳新鲜_就是真的在():
    src = _src()
    src.observe_event("arrived", "iris", _t())
    assert src.snapshot(now=_t(1))["effective"] is True


def test_状态跨重启_活着但不误报跃迁():
    store = FakeStore()
    first = FrontSource(store)
    first.observe_event("arrived", "iris", _t())

    second = FrontSource(store)
    assert second.present is True
    # 重启后的 arrived 重放不该再产念头
    assert second.observe_event("arrived", "iris", _t(1)) == []


def test_identity跃迁_同步身份不产念头():
    """在场但没认出（null）→ 认出是她 —— 只更新 identity，不是新闻。"""
    src = _src()
    src.observe_event("arrived", None, _t())
    out = src.observe_event("identity", "iris", _t(1))
    assert out == []
    assert src.identity == "iris"
    assert src.present is True
    # since 不动 —— 身份变了不等于刚到
    assert src.since == _t()


def test_identity_不在场时到_不当作arrived():
    """tracker 重启重放的 identity 不许把「她在」凭空造出来。"""
    src = _src()
    assert src.observe_event("identity", "iris", _t()) == []
    assert src.present is False


def test_identity_同值重复推_幂等():
    src = _src()
    src.observe_event("arrived", "iris", _t())
    src.observe_event("identity", "iris", _t(1))
    assert src.identity == "iris"
