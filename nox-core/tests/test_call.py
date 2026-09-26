"""主动来电的测试：CallSource 的三档与频率硬杠、_call_her 的交付与跟进。

时间全部用「UTC 小时 → CST 窗口」构造：12:00 UTC = 20:00 CST（窗口内），
09:00 UTC = 17:00 CST（窗口外）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.longing import LongingState  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.sources.call import CallSource, mode  # noqa: E402
from attention.store import AttentionStore  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)      # 20:00 CST，窗口内
OUTSIDE = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)   # 17:00 CST，窗口外


class _P:
    def get_state(self, turn=None, force_refresh=False):
        return {"has_data": False}


def _longing(value=0.7, quiet_h=4.0):
    return LongingState(value=value, last_contact=NOW - timedelta(hours=quiet_h))


def _source(tmp_path, longing="__default__"):
    #: longing 参数用哨兵串区分「没传」和「显式传 None」——None 是合法用例
    return CallSource(AttentionStore(tmp_path / "attn.db"),
                      log_dir=str(tmp_path),
                      longing_ref=_longing() if longing == "__default__" else longing)


def test_mode_off_polls_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("NOX_CALL", raising=False)
    assert mode() == "off"
    assert _source(tmp_path).poll(NOW) == []


def test_outside_window_no_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("NOX_CALL", "on")
    assert _source(tmp_path).poll(OUTSIDE) == []


def test_longing_ref_missing_no_signal(tmp_path, monkeypatch):
    """longing_ref 没接上 = 连她何时说的最后一句话都不知道，宁可不打。"""
    monkeypatch.setenv("NOX_CALL", "on")
    s = _source(tmp_path, longing=None)
    assert s.poll(NOW) == []


def test_quiet_too_short_no_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("NOX_CALL", "on")
    s = _source(tmp_path, _longing(quiet_h=1.0))
    assert s.poll(NOW) == []


def test_low_longing_still_signals(tmp_path, monkeypatch):
    """longing 不做门槛（糖糖 2026-09-26）：安静满两小时就是信号，
    想念那个数只管 reason 和 urgency 的味道。"""
    monkeypatch.setenv("NOX_CALL", "on")
    s = _source(tmp_path, _longing(value=0.3))
    sigs = s.poll(NOW)
    assert len(sigs) == 1 and sigs[0].source == "call"


def test_shadow_logs_once_per_hour_and_never_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("NOX_CALL", "shadow")
    s = _source(tmp_path)
    assert s.poll(NOW) == []                       # shadow 永远不产信号
    log = tmp_path / "call-shadow.jsonl"
    assert log.exists()
    first = json.loads(log.read_text(encoding="utf-8").strip())
    assert first["longing"] == 0.7 and "想听听你的声音" in first["reason"]
    s.poll(NOW + timedelta(minutes=20))            # 同一小时：不重复记
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_live_signals_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setenv("NOX_CALL", "on")
    s = _source(tmp_path)
    sigs = s.poll(NOW)
    assert len(sigs) == 1 and sigs[0].source == "call"
    assert sigs[0].payload["reason"]
    assert s.poll(NOW + timedelta(minutes=30)) == []     # 同一天：不补
    assert len(s.poll(NOW + timedelta(days=2))) == 1     # 隔天：可以再打


# ---------------------------------------------------------------- 交付


class _Bridge:
    def __init__(self, ok=True, status="missed"):
        self.ok = ok
        self.status = status
        self.posts = []
        self.gets = []

    def post(self, path, body=None):
        self.posts.append((path, body))
        if not self.ok:
            return SimpleNamespace(ok=False, data=None, error="boom")
        return SimpleNamespace(ok=True, data={"id": None, "pushed": 2}, error=None)

    def get(self, path, params=None):
        self.gets.append((path, params))
        return SimpleNamespace(ok=True, data={"call": {"status": self.status}}, error=None)


class _Utility:
    def __init__(self, text="喂……是我。就是想听听你的声音，说两句话就好。"):
        self.text = text

    def complete(self, messages, tools, **kw):
        if isinstance(self.text, Exception):
            raise self.text
        return SimpleNamespace(text=self.text)


class _Speaker:
    def __init__(self, reply="……那你忙，晚点说。"):
        self.reply = reply
        self.calls = []

    def __call__(self, intent, decision, prompt=None):
        self.calls.append((intent, prompt))
        return self.reply


def _svc(tmp_path, bridge=None, utility=None, speaker=None):
    astore = AttentionStore(tmp_path / "svc.db")
    svc = AttentionService(astore, _P(), RelationshipState(), speaker=speaker)
    svc.call_bridge = bridge
    svc.call_utility = utility
    return svc


def _signal():
    from attention.care.signal import CareSignal
    return CareSignal(source="call", subject="想听听她的声音",
                      payload={"reason": "想听听你的声音"})


def test_call_her_dry_run_posts_nothing(tmp_path):
    svc = _svc(tmp_path, bridge=_Bridge(), utility=_Utility(), speaker=None)
    assert svc._call_her(_signal(), None, NOW) is True
    assert svc.call_bridge.posts == []


def test_call_her_invites_with_opener(tmp_path):
    b, u = _Bridge(), _Utility()
    svc = _svc(tmp_path, bridge=b, utility=_Utility(), speaker=_Speaker())
    assert svc._call_her(_signal(), None, NOW) is True
    assert len(b.posts) == 1
    path, body = b.posts[0]
    assert path == "/api/call/invite"
    assert "想听听你的声音" in body["reason"]
    assert "想听听" in body["opener"]


def test_call_her_opener_failure_means_no_call(tmp_path):
    """开场白生成不出来 = 这通不打。宁可沉默，不能接通了没词。"""
    b = _Bridge()
    svc = _svc(tmp_path, bridge=b, utility=_Utility(text=RuntimeError("utility 挂了")),
               speaker=_Speaker())
    assert svc._call_her(_signal(), None, NOW) is False
    assert b.posts == []


def test_call_her_bridge_error_raises(tmp_path):
    """登记失败是故障不是「没话说」—— 抛出去让 orchestrator 记 failed。"""
    svc = _svc(tmp_path, bridge=_Bridge(ok=False), utility=_Utility(),
               speaker=_Speaker())
    with pytest.raises(RuntimeError):
        svc._call_her(_signal(), None, NOW)


def test_followup_missed_sends_message(tmp_path):
    b, sp = _Bridge(status="missed"), _Speaker()
    svc = _svc(tmp_path, bridge=b, utility=_Utility(), speaker=sp)
    svc._call_followup(_signal(), "c1")
    assert len(sp.calls) == 1
    intent, prompt = sp.calls[0]
    assert "铃响没人接" in prompt and "[SKIP]" in prompt
    assert intent.kind == "care_call_missed"


def test_followup_declined_also_offers_message(tmp_path):
    b, sp = _Bridge(status="declined"), _Speaker()
    svc = _svc(tmp_path, bridge=b, utility=_Utility(), speaker=sp)
    svc._call_followup(_signal(), "c1")
    assert "她拒接了" in sp.calls[0][1]


def test_followup_answered_is_silent(tmp_path):
    b, sp = _Bridge(status="answered"), _Speaker()
    svc = _svc(tmp_path, bridge=b, utility=_Utility(), speaker=sp)
    svc._call_followup(_signal(), "c1")
    assert sp.calls == []
