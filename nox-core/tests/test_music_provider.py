"""MusicProvider 测试：她现在在听什么。

守两件容易做砸的事：

1. **别把「三小时前听过」说成「正在听」** —— 说错比不知道更糟
2. **render 要短** —— 那段进 dynamic_system，在缓存断点之后，
   每个字都按未命中价付费
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.providers.music import MusicProvider  # noqa: E402
from router.intent import classify_context  # noqa: E402


class FakeClient:
    def __init__(self, songs: list | None = None, ok: bool = True) -> None:
        self._songs = songs if songs is not None else []
        self._ok = ok

    def get(self, path: str, params: Any = None):
        class R:
            ok = self._ok
            data = {"ok": True, "songs": self._songs}
        return R()


def _song(name="晚霞", artist="MULA SAKEE", minutes_ago=1):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"songId": "123", "name": name, "artist": artist,
            "playedAt": when.isoformat()}


def _state(provider):
    """绕开缓存直接取，测试不关心 ttl。"""
    return provider._fetch(None)


def test_playing_now():
    p = MusicProvider(FakeClient([_song(minutes_ago=2)]))
    s = _state(p)
    assert s["playing"] is True
    assert "正在听" in p.render(s)
    assert "晚霞" in p.render(s)


def test_old_play_is_not_playing():
    """三小时前听的不是「正在听」。

    说「你正在听的这首」而她早关了，比不知道更糟。
    """
    p = MusicProvider(FakeClient([_song(minutes_ago=180)]))
    s = _state(p)
    assert s["playing"] is False
    out = p.render(s)
    assert "正在听" not in out
    assert "小时前听过" in out


def test_yesterday_is_not_mentioned():
    """隔天的就别提了，那不叫「最近」。"""
    p = MusicProvider(FakeClient([_song(minutes_ago=60 * 30)]))
    assert p.render(_state(p)) == ""


def test_nothing_played():
    p = MusicProvider(FakeClient([]))
    s = _state(p)
    assert s["playing"] is False
    assert p.render(s) == ""


def test_render_is_short():
    """进 dynamic_system 的东西必须短 —— 缓存断点之后每个字都要钱。"""
    p = MusicProvider(FakeClient([_song(
        name="All Too Well (10 Minute Version) (Taylor's Version)",
        artist="Taylor Swift")]))
    assert len(p.render(_state(p))) < 80


def test_router_loads_music_when_relevant():
    """聊到音乐才拉，别的话题不拉 —— 每轮都塞是白付钱。"""
    assert "music" in classify_context("放首歌吧")
    assert "music" in classify_context("这首歌叫什么")
    assert "music" in classify_context("推荐几首歌")
    assert "music" not in classify_context("今天吃什么")
    # 轻量路径强制最小集，一句「早上好」不该去打 eryu
    assert "music" not in classify_context("放首歌吧", light=True)
