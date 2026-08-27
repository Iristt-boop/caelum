"""促狭 —— 她在闹，他可以接。

## 🔴 这个 Drive 的误判方式很特别

别的 Drive 判错顶多是"多问一句"或者"情绪淡一点"。
促狭判错的后果是**他会跟着开玩笑** ——
她正说着难过的事而他在贫，比"没接住"糟得多。

所以整套设计的方向是**宁可漏判，绝不错判**：

```text
只要句子里有一点难受的迹象      → 一律不算她在玩
她说了一句正经的                → 立刻散，不等窗口滑出去
只看最近 3 轮、20 分钟          → 中午闹过不算晚上还在闹
```

## 它不该让他多说话

强度上限压在 `GENERATE_THRESHOLD`(0.55) 之下。她心情好不该换来
一次主动开口 —— 那会变成「你一笑他就凑上来」。
这个 Drive 的意义是**改变他回话的方式**。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.appraisal import RuleAppraiser  # noqa: E402
from attention.intent import GENERATE_THRESHOLD  # noqa: E402
from attention.playfulness import MAX, WINDOW, PlayfulnessState  # noqa: E402

CST = timezone(timedelta(hours=8))
NOON = datetime(2026, 8, 27, 12, 0, tzinfo=CST)


def _play(st: PlayfulnessState, at: datetime, cue: str = "哈哈哈") -> None:
    st.on_turn(at, valence="playful", cue=cue)


class Test她在闹:
    def test_一句还不算(self):
        st = PlayfulnessState()
        _play(st, NOON)
        assert 0 < st.value_at(NOON) < MAX

    def test_连着几句才满(self):
        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        assert st.value_at(NOON) == MAX

    def test_说得出凭什么(self):
        st = PlayfulnessState()
        _play(st, NOON, cue="嘿嘿")
        assert any("嘿嘿" in b for b in st.because(NOON))

    def test_什么都没有时不说空话(self):
        assert PlayfulnessState().because(NOON) == []


class Test绝不错判:
    """🔴 整套设计的方向。"""

    def test_她说了正经的_立刻散(self):
        """不等窗口滑出去 —— 她刚说完难受，他下一句还在贫，最伤人。"""
        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        assert st.value_at(NOON) == MAX

        st.on_turn(NOON + timedelta(minutes=1), valence="distress", cue="好累")
        assert st.value_at(NOON + timedelta(minutes=1)) == 0

    def test_relief_也算正经的(self):
        """「好多了」是她在说自己的状态，不是在闹。"""
        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        st.on_turn(NOON + timedelta(minutes=1), valence="relief", cue="好多了")
        assert st.value_at(NOON + timedelta(minutes=1)) == 0

    def test_中性的话会把气氛稀释掉(self):
        """🔴 每一轮都要喂 —— 只喂 playful 的话，
        她「哈哈哈」之后说十句正事，窗口里还是三条 playful。"""
        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        for i in range(WINDOW):
            st.on_turn(NOON + timedelta(minutes=1, seconds=i), valence="neutral")
        assert st.value_at(NOON + timedelta(minutes=2)) == 0

    def test_二十分钟前的不算(self):
        """她中午闹了两句，晚上回来第一句不该被当成还在闹。"""
        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        assert st.value_at(NOON + timedelta(minutes=25)) == 0

    def test_warm_不等于在闹(self):
        """她心情好是背景色，不是邀请他一起玩。"""
        st = PlayfulnessState()
        for i in range(WINDOW):
            st.on_turn(NOON + timedelta(seconds=i), valence="warm", cue="开心")
        assert st.value_at(NOON) == 0


class Test不让他多说话:
    def test_上限低于开口阈值(self):
        """🔴 她心情好不该换来一次主动开口 ——
        那会变成「你一笑他就凑上来」，很烦人。"""
        assert MAX < GENERATE_THRESHOLD


class Test识别:
    """appraisal 新加的 playful / warm 两档。"""

    def setup_method(self):
        self.a = RuleAppraiser()

    def test_认得出她在闹(self):
        for t in ["哈哈哈笑死我了", "讨厌啦", "不告诉你", "嘿嘿"]:
            ap = self.a.appraise(t)
            assert ap is not None and ap.valence == "playful", t

    def test_认得出她心情好(self):
        for t in ["今天好开心", "爱你", "太棒了"]:
            ap = self.a.appraise(t)
            assert ap is not None and ap.valence == "warm", t

    def test_难受优先_不会被当成在闹(self):
        """🔴 「哈哈哈我好累」—— 那不是在闹。"""
        ap = self.a.appraise("哈哈哈我好累啊")
        assert ap is not None and ap.valence == "distress"

    def test_挡词表挡住模糊情况(self):
        """句子里有任何一点难受的迹象，一律不算她在玩。"""
        for t in ["嘿嘿我有点怕", "哈哈哈怎么办啊", "嘻嘻对不起"]:
            ap = self.a.appraise(t)
            assert ap is None or ap.valence != "playful", t

    def test_原来的两档没被改坏(self):
        assert self.a.appraise("我好累").valence == "distress"
        assert self.a.appraise("好多了").valence == "relief"
        assert self.a.appraise("今天天气不错") is None


class Test不进_Registry:
    """🔴 「她说哈哈哈」被记成一条**担心**，是彻底反的。"""

    def test_playful_不产生_concern(self):
        from attention.evaluator import AttentionEvaluator
        from attention.events import ExperienceEvent
        from attention.registry import AttentionRegistry
        from attention.relationship import RelationshipState

        ev = ExperienceEvent(
            source="chat", type="message",
            payload={"text": "哈哈哈笑死我了", "session_id": "s"},
        )
        d = AttentionEvaluator(RelationshipState()).evaluate(
            ev, AttentionRegistry(), NOON)
        assert d.action == "ignore", "她笑了，他不该记一条担心"

    def test_warm_也不产生_concern(self):
        from attention.evaluator import AttentionEvaluator
        from attention.events import ExperienceEvent
        from attention.registry import AttentionRegistry
        from attention.relationship import RelationshipState

        ev = ExperienceEvent(
            source="chat", type="message",
            payload={"text": "今天好开心", "session_id": "s"},
        )
        d = AttentionEvaluator(RelationshipState()).evaluate(
            ev, AttentionRegistry(), NOON)
        assert d.action == "ignore"


class Test进_Resonance:
    def test_在闹的时候有这个_Drive(self):
        from attention.registry import AttentionRegistry
        from attention.resonance import ResonanceState

        st = PlayfulnessState()
        for i in range(WINDOW):
            _play(st, NOON + timedelta(seconds=i))
        drives = ResonanceState(AttentionRegistry(), playfulness=st).snapshot(NOON)
        assert "playfulness" in drives
        assert drives["playfulness"].intensity == MAX

    def test_不在闹的时候这个_Drive_根本不存在(self):
        """🔴 挂一个 0.00 在自省接口里，等于说「他现在有点促狭（0.00）」。"""
        from attention.registry import AttentionRegistry
        from attention.resonance import ResonanceState

        drives = ResonanceState(
            AttentionRegistry(), playfulness=PlayfulnessState()).snapshot(NOON)
        assert "playfulness" not in drives


class Test真跑一轮:
    """🔴 今天已经被 `except Exception` 吞过三次了。

    ```text
    req.text                     NameError
    DEJECTION_KEY                NameError
    engine.appraiser             AttributeError（它挂在 evaluator 上）
    ```

    三次表现一模一样：日志一句「更新失败（不影响对话）」，整条线静默失效。
    读源码的测试抓不到属性路径错 —— 只有真跑才知道。
    """

    def test_她闹一句之后_状态真的落库(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient
        from api.server import create_app
        from attention.service import PLAYFUL_KEY
        from data.store import Store
        from tests.test_api import FakeNox
        from tests.test_api_world import _Ctx, _Loop
        from tests.test_dejection import _find_attention

        monkeypatch.setenv("NOX_ATTENTION", "1")
        nox = FakeNox()
        nox.cfg.db_path = str(tmp_path / "nox.db")
        nox.context = _Ctx()
        nox.bridge = None
        nox.current_session_id = None
        nox.loop = _Loop()
        nox.router = type("R", (), {"light_adapter": None})()
        app = create_app(nox, Store(tmp_path / "sessions.db"))
        client = TestClient(app)

        r = client.post("/chat", json={"session_id": "s1", "text": "哈哈哈笑死我了"})
        assert r.status_code == 200, r.text

        attention = _find_attention(app)
        assert attention is not None
        saved = attention.store.get_source_state(PLAYFUL_KEY)
        assert saved is not None, "促狭状态没落库 —— 那条线又被 except 吞了"
        turns = saved.get("turns") or []
        assert any(t.get("valence") == "playful" for t in turns), \
            f"这一轮没被认成 playful：{turns}"


class Test存盘:
    def test_存了能读回来(self):
        st = PlayfulnessState()
        _play(st, NOON, cue="嘿嘿")
        back = PlayfulnessState.from_dict(st.to_dict())
        assert back.value_at(NOON) == st.value_at(NOON)

    def test_空的读得动(self):
        assert PlayfulnessState.from_dict(None).value_at(NOON) == 0

    def test_坏记录跳过不清零(self):
        bad = {"turns": [
            {"at": NOON.isoformat(), "valence": "playful", "cue": "好的"},
            {"at": "不是时间", "valence": "playful", "cue": "坏的"},
        ]}
        assert PlayfulnessState.from_dict(bad).value_at(NOON) > 0
