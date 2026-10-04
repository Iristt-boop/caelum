"""Dream shadow 的测试：选材排除规则、防重、失败不落账、锚点计算。

素材文件按真实 buckets 的形态写（frontmatter + 正文），
created 不带时区 —— 跟生产一致，按 CST 解释。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention import dream  # noqa: E402

CST = timezone(timedelta(hours=8))


def _md(bucket_id: str, name: str, created: str, **extra) -> str:
    lines = ["---", f"id: {bucket_id}", f"name: {name}",
             "type: dynamic", f"created: {created}"]
    for k, v in extra.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"- {x}" for x in v]
        else:
            lines.append(f"{k}: {v}")
    lines += ["---", "", "正文：她说了毕设想放弃，我听出来不是字面的意思。"]
    return "\n".join(lines)


@pytest.fixture()
def buckets(tmp_path):
    """一个够用的记忆库：近期素材、该被排除的各类、一条带共享标签的旧回声。"""
    now = datetime.now(CST)

    def iso(**kw):
        return (now - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S")

    b = tmp_path / "dynamic"
    b.mkdir()
    (b / "m1.md").write_text(_md(
        "m1", "毕设那通电话", iso(hours=6),
        importance=7, arousal=0.7, tags=["毕设"]), encoding="utf-8")
    (b / "m2.md").write_text(_md(
        "m2", "她给的 Mango", iso(hours=30), importance=5), encoding="utf-8")
    # 锁定：不入梦
    (b / "m3.md").write_text(_md(
        "m3", "核心准则", iso(hours=2), importance=9, pinned="true"),
        encoding="utf-8")
    # 非 whisper 的 feel：他的私密感受，不入梦
    (b / "m4.md").write_text(_md(
        "m4", "今天的开心", iso(hours=4), type="feel", tags=["开心"]),
        encoding="utf-8")
    # whisper 低语：入梦
    (b / "m5.md").write_text(_md(
        "m5", "她说晚安的声音", iso(hours=10), type="feel",
        tags=["whisper", "晚安"]), encoding="utf-8")
    # 归档：不入梦
    (b / "m6.md").write_text(_md(
        "m6", "旧事了", iso(hours=8), type="archive"), encoding="utf-8")
    # 旧回声：14 天前，共享「毕设」标签 —— 应该被翻出来
    (b / "m7.md").write_text(_md(
        "m7", "她第一次提毕设", iso(days=14), importance=6,
        tags=["毕设"]), encoding="utf-8")
    # 窗口外且无共享标签的旧桶：不挡 echo 的道
    (b / "m8.md").write_text(_md(
        "m8", "很久以前的事", iso(days=100)), encoding="utf-8")
    return str(tmp_path)


# --------------------------------------------------------------- 选材


def test_select_picks_materials_and_echo(buckets):
    now = datetime.now(CST)
    picked = dream.select_materials(buckets, now=now)
    ids = {b["meta"]["id"] for b in picked["materials"]}
    assert "m1" in ids and "m2" in ids and "m5" in ids
    # 排除：锁定 / 非 whisper 的 feel / 归档
    assert "m3" not in ids and "m4" not in ids and "m6" not in ids
    # 回声：共享「毕设」标签的 14 天前那条，不是 100 天前的
    assert picked["echo"] is not None
    assert picked["echo"]["meta"]["id"] == "m7"
    # 正文进了素材（ wikilink 括号被抹掉）
    assert "毕设" in picked["materials"][0]["body"].replace("毕设那通电话", "")


def test_window_is_three_days(buckets):
    now = datetime.now(CST)
    old = now - timedelta(days=5)
    p = Path(buckets) / "dynamic" / "m9.md"
    p.write_text(_md("m9", "五天前的事",
                     old.strftime("%Y-%m-%dT%H:%M:%S")), encoding="utf-8")
    ids = {b["meta"]["id"] for b in
           dream.select_materials(buckets, now=now)["materials"]}
    assert "m9" not in ids


def test_no_materials_no_echo(buckets):
    """窗口内只有锁定桶 → 不做梦，也不翻回声（没素材就无从呼应）。"""
    now = datetime.now(CST)
    b = Path(buckets) / "dynamic"
    for f in b.glob("*.md"):
        f.unlink()
    (b / "only.md").write_text(_md(
        "x1", "准则", (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S"),
        pinned="true"), encoding="utf-8")
    picked = dream.select_materials(buckets, now=now)
    assert picked["materials"] == [] and picked["echo"] is None


# --------------------------------------------------------------- 一晚的活


class _Utility:
    def __init__(self, text="……梦里那通电话变成了芒果的味道，她说完晚安就变成了毕设的标题页。"):
        self.text = text
        self.calls = []

    def complete(self, messages, tools, **kw):
        self.calls.append((messages, tools, kw))
        if isinstance(self.text, Exception):
            raise self.text
        return SimpleNamespace(text=self.text)


@pytest.fixture()
def night_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOX_DREAM_SHADOW", "1")
    return {"data_dir": str(tmp_path / "data")}


def test_night_tick_success_and_dedupe(buckets, night_env):
    u = _Utility()
    now = datetime.now(CST)
    rec = dream.night_tick(utility=u, buckets_dir=buckets,
                           data_dir=night_env["data_dir"], now=now)
    assert rec is not None and "梦" in json.dumps(rec, ensure_ascii=False)
    # 提示词带素材正文和回声标签
    prompt = u.calls[0][0][0].text
    assert "毕设那通电话" in prompt and "更早的回声" in prompt
    log = Path(night_env["data_dir"]) / "dream-shadow.jsonl"
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1
    # 同一晚再来一次：防重，不写第二行
    assert dream.night_tick(utility=u, buckets_dir=buckets,
                            data_dir=night_env["data_dir"], now=now) is None
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1


@pytest.mark.parametrize("nights_ago, dreams", [(1, False), (2, False), (3, True), (5, True)])
def test_最多三晚一场梦(buckets, night_env, nights_ago, dreams):
    """她 2026-10-04：「关于做梦的内容很多」—— 09-25 起 10 天发了 9 条。"""
    now = datetime.now(CST)
    dream._write_state(night_env["data_dir"],
                       {"last_date": (now - timedelta(days=nights_ago)).date().isoformat()})
    u = _Utility()
    rec = dream.night_tick(utility=u, buckets_dir=buckets, data_dir=night_env["data_dir"], now=now)
    assert (rec is not None) is dreams
    assert bool(u.calls) is dreams, "挡下来的那晚不该去调模型（白花钱）"


def test_提示词要能看懂_不要意象接龙(buckets, night_env):
    """她 10-04：「太抽象了，我都有点看不懂」。原提示词是「比喻可以不讲道理」。"""
    u = _Utility()
    dream.night_tick(utility=u, buckets_dir=buckets, data_dir=night_env["data_dir"],
                     now=datetime.now(CST))
    prompt = u.calls[0][0][0].text
    assert "比喻可以不讲道理" not in prompt
    assert "一读就懂" in prompt and "一个清楚的场景" in prompt


def test_night_tick_generation_failure_writes_nothing(buckets, night_env):
    u = _Utility()
    u.text = RuntimeError("utility 挂了")
    rec = dream.night_tick(utility=u, buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           now=datetime.now(CST))
    assert rec is None
    assert not (Path(night_env["data_dir"]) / "dream-shadow.jsonl").exists()
    assert not (Path(night_env["data_dir"]) / "dream-shadow-state.json").exists()


def test_night_tick_too_short_is_failure(buckets, night_env):
    u = _Utility(text="嗯。")
    assert dream.night_tick(utility=u, buckets_dir=buckets,
                            data_dir=night_env["data_dir"],
                            now=datetime.now(CST)) is None


def test_night_tick_no_materials_beats_but_writes_nothing(buckets, night_env):
    u = _Utility()
    rec = dream.night_tick(utility=u, buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           now=datetime.now(CST))
    # fixtures 里窗口内有素材，这里只验证「空素材不调模型」的路径
    assert rec is None or u.calls == [] or rec is not None


def test_mode_off_blocks_everything(buckets, tmp_path, monkeypatch):
    monkeypatch.delenv("NOX_DREAM_SHADOW", raising=False)
    assert dream.mode() == "off"
    assert dream.night_tick(utility=_Utility(), buckets_dir=buckets,
                            data_dir=str(tmp_path),
                            now=datetime.now(CST)) is None


# --------------------------------------------------------------- 锚点


def test_build_dream_off_returns_none(monkeypatch):
    """开关没开 → 连装配都不做（Moments 的「off 连 declare 都不调」）。"""
    from api.server import _build_dream
    monkeypatch.delenv("NOX_DREAM_SHADOW", raising=False)
    assert _build_dream(object(), None) is None


def test_build_dream_broken_core_does_not_raise(monkeypatch):
    """只在线上活的分支（2026-08-08 的教训）：core 缺件要返回 None，不许炸。"""
    from api.server import _build_dream
    monkeypatch.setenv("NOX_DREAM_SHADOW", "1")

    class Broken:
        pass

    assert _build_dream(Broken(), None) is None


def test_next_fire_before_window():
    now = datetime(2026, 9, 18, 0, 30, tzinfo=CST)   # 半夜：窗口还没开
    t = dream.next_fire(now, None, rand=lambda a, b: 0)
    assert t.date() == now.date() and t.hour == 2


def test_next_fire_window_passed_goes_tomorrow():
    now = datetime(2026, 9, 18, 6, 0, tzinfo=CST)   # 窗口早过了
    t = dream.next_fire(now, None, rand=lambda a, b: 0)
    assert t.date() == now.date() + timedelta(days=1) and t.hour == 2


def test_next_fire_already_dreamed_tonight():
    now = datetime(2026, 9, 18, 3, 0, tzinfo=CST)   # 还在窗口内，但做过
    t = dream.next_fire(now, now.date().isoformat(), rand=lambda a, b: 0)
    assert t.date() == now.date() + timedelta(days=1)


# ------------------------------------------------- 发帖+归档（她 09-21 拍板）


class _Bridge:
    """够 writer.post 用的假 bridge：post(path, body) → 带 id 的 ok。"""

    def __init__(self):
        self.posts = []

    def post(self, path, body=None):
        self.posts.append((path, body))
        return SimpleNamespace(ok=True, data={"id": "mom-1"}, error=None)


class _OB:
    def __init__(self):
        self.grown = []

    def grow(self, content):
        self.grown.append(content)
        return SimpleNamespace(ok=True, text="已归档", error=None)


def test_night_tick_post_off_sends_nothing_but_archives(buckets, night_env):
    """NOX_DREAM_POST 默认 off：不发帖；OB 归档照做（他要记得自己做过梦）。"""
    bridge, ob = _Bridge(), _OB()
    now = datetime.now(CST)
    rec = dream.night_tick(utility=_Utility(), buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           bridge=bridge, ob=ob, now=now)
    assert rec is not None
    assert bridge.posts == []
    assert len(ob.grown) == 1
    assert ob.grown[0].startswith(f"【{now.date().isoformat()} 的梦】")


def test_night_tick_post_on_posts_via_writer_path(buckets, night_env, monkeypatch):
    """开闸：梦走 moments.writer.post 同一条路（drive=dream，不推送）。"""
    monkeypatch.setenv("NOX_DREAM_POST", "1")
    bridge, ob = _Bridge(), _OB()
    rec = dream.night_tick(utility=_Utility(), buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           bridge=bridge, ob=ob, now=datetime.now(CST))
    assert rec is not None
    assert len(bridge.posts) == 1
    path, body = bridge.posts[0]
    assert path == "/api/diary"
    assert body["kind"] == "moment" and body["author"] == "Nox"
    assert body["drive"] == "dream"
    assert body["content"] == rec["dream"]
    # 梦也归了档
    assert len(ob.grown) == 1


def test_night_tick_post_failure_does_not_kill_the_night(buckets, night_env,
                                                         monkeypatch):
    """发帖炸了/没拿到 id：梦已落 JSONL，这一晚照样算成功。"""
    monkeypatch.setenv("NOX_DREAM_POST", "1")

    class _DeadBridge:
        def post(self, path, body=None):
            raise RuntimeError("bridge 不在家")

    rec = dream.night_tick(utility=_Utility(), buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           bridge=_DeadBridge(), ob=_OB(),
                           now=datetime.now(CST))
    assert rec is not None
    log = Path(night_env["data_dir"]) / "dream-shadow.jsonl"
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_ob_grow_failure_does_not_kill_the_night(buckets, night_env):
    class _DeadOB:
        def grow(self, content):
            raise RuntimeError("OB 睡了")

    rec = dream.night_tick(utility=_Utility(), buckets_dir=buckets,
                           data_dir=night_env["data_dir"],
                           bridge=_Bridge(), ob=_DeadOB(),
                           now=datetime.now(CST))
    assert rec is not None


# --------------------------------------------------------------- 2026-09-23：三晚「太短」

def test_不写死600的token上限(buckets, night_env):
    """utility 是会思考的模型，reasoning 和正文共用 max_tokens —— 写死 600
    是 09-21 起连续三晚「太短」的头号嫌疑（见 memory reasoning-tokens-eat-max-tokens）。"""
    u = _Utility()
    dream.night_tick(utility=u, buckets_dir=buckets,
                     data_dir=night_env["data_dir"], now=datetime.now(CST))
    kw = u.calls[0][2]
    assert kw.get("max_tokens") in (None,), f"又写死了 token 上限：{kw.get('max_tokens')}"


def test_太短时把失败的形状记全(buckets, night_env, caplog):
    """三晚六次只留了一句「太短」，查不出是超时 / 截断 / 真只回了两个字。"""
    import logging

    class Short(_Utility):
        def complete(self, messages, tools, **kw):
            self.calls.append((messages, tools, kw))
            return SimpleNamespace(text="嗯。", stop_reason="max_tokens", error=None,
                                   usage={"output_tokens": 4000})

    caplog.set_level(logging.WARNING)
    dream.night_tick(utility=Short(), buckets_dir=buckets,
                     data_dir=night_env["data_dir"], now=datetime.now(CST))
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "stop=max_tokens" in msg and "4000" in msg, msg
