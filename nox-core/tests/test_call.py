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


# ---------------------------------------------------------------- 开场白他自己写（10-29）
# 她：「他主动打电话的开口第一句现在是固定的？」—— 线上 12 通几乎一字不差
# （utility 只拿到一句「想听听你的声音」）。现在走全管线草稿 + 给他看最近几次的开场白


class _Drafter:
    def __init__(self, reply="锁屏：问问视频剪完没\n开场：“喂，视频剪完了没？我惦记一下午了。”"):
        self.reply = reply
        self.prompts = []

    def __call__(self, prompt, recall):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _svc2(tmp_path, drafter, utility=None, bridge=None):
    svc = _svc(tmp_path, bridge=bridge or _Bridge(), utility=utility, speaker=_Speaker())
    svc.call_drafter = drafter
    return svc


def test_drafter_writes_opener_and_lock_line(tmp_path):
    d = _Drafter()
    svc = _svc2(tmp_path, d, utility=_Utility(text=RuntimeError("不该走到 utility")))
    assert svc._call_her(_signal(), None, NOW) is True
    _, body = svc.call_bridge.posts[0]
    assert body["opener"] == "喂，视频剪完了没？我惦记一下午了。", "外层引号要剥掉"
    assert body["reason"] == "问问视频剪完没", "锁屏那行用他写的理由"
    assert "9月19日" in d.prompts[0], "草稿提示要带日期时间"


def test_drafter_hold_means_no_call(tmp_path):
    svc = _svc2(tmp_path, _Drafter(reply=None), utility=_Utility())
    assert svc._call_her(_signal(), None, NOW) is False
    assert svc.call_bridge.posts == []


def test_drafter_failure_falls_back_to_utility(tmp_path):
    svc = _svc2(tmp_path, _Drafter(reply=RuntimeError("core 挂了")),
                utility=_Utility(text="「喂……是我。」"))
    assert svc._call_her(_signal(), None, NOW) is True
    _, body = svc.call_bridge.posts[0]
    assert body["opener"] == "喂……是我。", "utility 兜底也剥引号"
    assert body["reason"] == "想听听你的声音"


def test_drafter_without_format_uses_whole_text(tmp_path):
    svc = _svc2(tmp_path, _Drafter(reply="喂，刚看你朋友圈那张晚霞，太好看了。"))
    assert svc._call_her(_signal(), None, NOW) is True
    _, body = svc.call_bridge.posts[0]
    assert body["opener"] == "喂，刚看你朋友圈那张晚霞，太好看了。"
    assert body["reason"] == "想听听你的声音"


def test_recent_openers_shown_next_time_and_capped(tmp_path):
    d = _Drafter()
    svc = _svc2(tmp_path, d)
    for i in range(6):
        d.reply = f"锁屏：第{i}通\n开场：开场白{i}"
        assert svc._call_her(_signal(), None, NOW) is True
    last = d.prompts[-1]
    assert "开场白4" in last and "开场白1" in last, "最近几次要给他看"
    assert "开场白0" not in last, "只留最近 4 条"
    assert "开场白5" not in last, "这次的还没说过"
    assert "别再这样说" not in d.prompts[0], "第一次没有可比的"


def test_utility_fallback_also_sees_recent_openers(tmp_path):
    u = _Utility(text="喂")
    seen = []
    orig = u.complete
    u.complete = lambda messages, tools, **kw: (seen.append(messages[0].text), orig(messages, tools, **kw))[1]
    svc = _svc2(tmp_path, None, utility=u)
    svc._remember_opener("上次那句开场白")
    assert svc._call_her(_signal(), None, NOW) is True
    assert "上次那句开场白" in seen[0]


def test_failed_invite_does_not_remember_opener(tmp_path):
    svc = _svc2(tmp_path, _Drafter(), bridge=_Bridge(ok=False))
    with pytest.raises(RuntimeError):
        svc._call_her(_signal(), None, NOW)
    assert svc._recent_openers() == []


def test_build_drafter_neither_saves_nor_pushes():
    """草稿：她可能不接，没说出口的话不能进他的历史，也不能推到她锁屏。"""
    from attention.speaker import build_drafter

    class _Sess:
        def __init__(self):
            self.puts = []

        def get(self, sid):
            return ["历史"]

        def put(self, sid, messages):
            self.puts.append(sid)

    class _Core:
        bridge = None

        def __init__(self, text):
            self.text = text
            self.calls = []

        def chat(self, text, history, **kw):
            self.calls.append((text, history, kw))
            return SimpleNamespace(result=SimpleNamespace(text=self.text, outcome="answered", ok=True),
                                   messages=[], usage=None)

    store = SimpleNamespace(recent=lambda limit, clean_only: [SimpleNamespace(id="s1")])
    sess, core = _Sess(), _Core("锁屏：x\n开场：y")
    draft = build_drafter(core, sess, store)
    assert draft("提示", "翻记忆") == "锁屏：x\n开场：y"
    assert sess.puts == []
    text, history, kw = core.calls[0]
    assert history == ["历史"] and kw["session_id"] == "s1" and kw["recall"] == "翻记忆"
    assert build_drafter(_Core("[SKIP]"), sess, store)("p", "r") is None
    with pytest.raises(RuntimeError):
        build_drafter(_Core(""), sess, store)("p", "r")


def test_server_wires_drafter_into_service():
    """装配断言（起整个 server 太重）：这一行一丢，开场白就悄悄退回 utility 那句老话。"""
    src = (Path(__file__).resolve().parents[1] / "api" / "server.py").read_text(encoding="utf-8")
    assert "drafter = build_drafter(core, sessions, db) if live else None" in src
    assert "call_drafter=drafter" in src
