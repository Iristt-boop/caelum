"""eryu 工具的**参数名**测试。

## 为什么单独为参数名写一组测试

2026-08-08 查出来三个工具的参数名全是错的，线上 100% 失败：

    /music/search   Core 发 keyword，eryu 要 q          → 搜歌全挂，连带放歌也用不了
    /music/analyze  Core 发 song_id，eryu 要 songId     → 400
    /music/memory   Core 发 song_id，eryu 要 songId     → 400
                    而且不带 action="note" 会走 listen 分支，
                    **返回 200 但一个字都不存**

根因是当时对着别人的接口猜着写（eryu 源码只在 VPS `/root/eryu`，
本地没有镜像）—— 第十九节第 15 条那条教训的原样复发。

这类 bug 的可怕之处：**代码本身没有任何毛病**，类型对、逻辑通、
单测能过，只有真的打到对端才会炸。所以这里不测逻辑，
只钉死「发出去的字段名到底是什么」。

对端实现在 VPS `/root/eryu/server/eryu.py`，行号写在各个断言旁边。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.eryu import make_handlers  # noqa: E402


@dataclass
class FakeResult:
    ok: bool = True
    data: Any = field(default_factory=dict)
    text: str = ""
    error: str | None = None


class FakeClient:
    """只记下「被调用时发了什么」，不真打网络。"""

    def __init__(self, data: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._data = data if data is not None else {}

    def get(self, path: str, params: dict | None = None) -> FakeResult:
        self.calls.append(("GET", path, params or {}))
        return FakeResult(data=self._data)

    def post(self, path: str, body: dict | None = None) -> FakeResult:
        self.calls.append(("POST", path, body or {}))
        return FakeResult(data=self._data)

    def last(self) -> tuple[str, str, Any]:
        return self.calls[-1]

    def find(self, path: str) -> dict:
        for _, p, payload in self.calls:
            if p == path:
                return payload
        raise AssertionError(f"没有调用过 {path}，实际调了：{[c[1] for c in self.calls]}")


def test_search_sends_q_not_keyword():
    """eryu.py:480 —— `qs.get("q")`，发 keyword 会被回 400 missing q。"""
    client = FakeClient({"songs": [{"id": 1, "name": "x", "artist": "y"}]})
    make_handlers(client)["eryu_search"]({"keyword": "起风了"})

    params = client.find("/music/search")
    assert params.get("q") == "起风了"
    assert "keyword" not in params


def test_search_does_not_send_limit():
    """服务端硬编码只取 6 条，不认 limit。发了是噪音，条数在 Core 侧截。"""
    client = FakeClient({"songs": []})
    make_handlers(client)["eryu_search"]({"keyword": "x", "limit": 3})
    assert "limit" not in client.find("/music/search")


def test_analyze_reads_ready_result():
    """服务端给的是 `{"status": "ready", "analysis": {...}}`
    （server/eryu.py:988），不是 `{"ready": True, "result": ...}`。

    Core 原来找 `ready`/`result`，所以分析结果早就躺在 music_cache 里
    也永远匹配不上，只会回一句「任务已提交」。
    """
    client = FakeClient({
        "ok": True, "status": "ready",
        "analysis": {"songId": "77426", "name": "测试曲", "bpm": 103,
                     "key": "G", "energy": 0.101, "dynamics": 0.061,
                     "brightness": 2461.0, "harmonicRatio": 0.71},
    })
    out = make_handlers(client)["eryu_analyze"]({"song_id": "77426"})

    assert "BPM: 103" in out
    assert "测试曲" in out
    # ⚠️ 能量是 0~1 不是 0~10。原来写死 /10，会把 0.101 显示成 0.101/10
    assert "0.101" in out
    assert "/10" not in out


def test_analyze_never_triggers_vps_job():
    """**故意不 POST /music/analyze**。

    VPS 没装 librosa，触发只会跑一个必定失败的任务，还会留下
    `_analyze_error.txt` —— 把「还没分析」弄脏成「分析失败」。
    分析在糖糖电脑上跑，结果回传即可。
    """
    client = FakeClient({"ok": True, "status": "none"})
    out = make_handlers(client)["eryu_analyze"]({"song_id": "999"})

    assert not any(p == "/music/analyze" for _, p, _ in client.calls)
    assert "还没有分析过" in out
    # 要给模型指一条退路，别让它硬猜
    assert "eryu_get_lyric" in out


def test_save_memory_sends_camel_case_and_note_action():
    """eryu.py:793 —— 要 `songId`，而且必须 action="note"。

    不带 action 的话服务端默认走 "listen" 分支，只加播放计数、
    **不存 notes，还返回 200** —— 看起来完全像成功了。
    """
    client = FakeClient()
    make_handlers(client)["eryu_save_memory"](
        {"song_id": "123", "notes": "她说这首像夏天", "tags": "夏天"}
    )

    body = client.find("/music/memory")
    assert body.get("songId") == "123"
    assert body.get("action") == "note"
    assert body.get("notes") == "她说这首像夏天"
    assert "song_id" not in body


def test_search_parses_flattened_shape():
    """eryu 返回的是**拍平**的结构（server/eryu.py:509）：
    `artist` 是拼好的字符串、`album` 是字符串。

    照网易云原始格式写成 `s.get("album", {}).get("name")` 会抛
    AttributeError: 'str' object has no attribute 'get' ——
    参数名修好之后就是栽在这一行。
    """
    client = FakeClient({"songs": [{
        "id": 1330348068, "name": "起风了",
        "artist": "冯沁苑(买辣椒也用券)", "album": "起风了", "cover": "",
    }]})
    out = make_handlers(client)["eryu_search"]({"keyword": "起风了"})

    assert "1330348068" in out
    assert "冯沁苑(买辣椒也用券)" in out


def test_play_posts_to_remote_queue():
    """点播 = 写进 `/music/remote` 队列，她的播放器每 5 秒轮询取走。

    2026-08-08 之前 `eryu_play` 只调 `/music/url` 下载缓存，
    **从来没写过这个队列** —— 所以它从头到尾就没让任何设备播过歌。
    """
    client = FakeClient({"ok": True, "url": "/music/file/1.mp3", "cached": True})
    out = make_handlers(client)["eryu_play"](
        {"song_id": "1330348068", "name": "起风了", "artist": "周深"})

    body = client.find("/music/remote")
    assert body["song"]["songId"] == "1330348068"
    assert body["song"]["name"] == "起风了"
    assert "起风了" in out


def test_search_exposes_cover():
    """搜索结果必须带 cover —— 否则模型手上没有封面地址，
    `eryu_play` 想带也带不了，糖糖的唱片中间就永远是个音符占位
    （2026-08-10 她发现的）。"""
    client = FakeClient({"songs": [{
        "id": 3353721067, "name": "晚霞(SUN DOWN)", "artist": "MULA SAKEE",
        "album": "晚霞", "cover": "https://p2.music.126.net/xxx.jpg",
    }]})
    out = make_handlers(client)["eryu_search"]({"keyword": "晚霞"})
    assert "cover=https://p2.music.126.net/xxx.jpg" in out


def test_play_backfills_missing_cover():
    """模型漏带封面时，服务端自己搜一次补上。

    不能全指望它每次都记得抄 —— 漏一次她那边就难看一次。
    """
    client = FakeClient({
        "ok": True, "url": "/music/file/1.mp3", "cached": True,
        # search 和 url 共用一个 FakeClient 返回体，这里让 songs 也在
        "songs": [{"id": 3353721067, "name": "晚霞(SUN DOWN)",
                   "cover": "https://p2.music.126.net/found.jpg"}],
    })
    make_handlers(client)["eryu_play"](
        {"song_id": "3353721067", "name": "晚霞(SUN DOWN)", "artist": "MULA SAKEE"})

    body = client.find("/music/remote")
    assert body["song"]["cover"] == "https://p2.music.126.net/found.jpg"


def test_play_saves_reason():
    """「他为什么选这首」要存进歌曲记忆，显示在共听页那张卡片的灰底里。

    ⚠️ 必须带 action="note" —— 不带的话服务端走 listen 分支，
    只加播放计数、notes 一个字不存，**还返回 200**。
    """
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    make_handlers(client)["eryu_play"]({
        "song_id": "1", "name": "晚霞", "artist": "MULA SAKEE",
        "reason": "你说昨晚总醒，这首慢得能睡着",
    })

    body = client.find("/music/memory")
    assert body["action"] == "note"
    assert body["notes"] == "你说昨晚总醒，这首慢得能睡着"
    assert body["songId"] == "1"


def test_play_without_reason_skips_note():
    """没写理由就别往记忆里塞空的。"""
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    make_handlers(client)["eryu_play"]({"song_id": "1", "name": "某首"})
    assert not any(p == "/music/memory" for _, p, _ in client.calls)


def test_play_rejects_filler_reason():
    """套话等于没写，别让它出现在她的共听页上。

    「他为什么选这首」那条灰底摆着一句「这是一首好听的歌」，
    比空着更伤人。
    """
    for filler in ("这是一首很好听的歌", "根据你的喜好推荐", "好听"):
        client = FakeClient({"ok": True, "url": "x", "cached": True})
        make_handlers(client)["eryu_play"](
            {"song_id": "1", "name": "某首", "reason": filler})
        assert not any(p == "/music/memory" for _, p, _ in client.calls), filler


def test_play_sends_queue():
    """一次给一串 —— 只给一首的话三分钟后就没声了。"""
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    out = make_handlers(client)["eryu_play"]({
        "song_id": "1", "name": "第一首", "artist": "A", "cover": "c1",
        "queue": [
            {"song_id": "2", "name": "第二首", "artist": "B", "cover": "c2"},
            {"song_id": "3", "name": "第三首", "artist": "C", "cover": "c3"},
        ],
    })

    body = client.find("/music/remote")
    assert body["song"]["songId"] == "1"
    assert [t["songId"] for t in body["queue"]] == ["2", "3"]
    assert body["queue"][0]["cover"] == "c2"
    assert "还排了 2 首" in out


def test_play_queue_capped_and_dedup():
    """最多 10 首；正在放的那首不许再排进队列（否则会立刻重播一遍）。"""
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    make_handlers(client)["eryu_play"]({
        "song_id": "1", "name": "当前",
        "queue": [{"song_id": "1"}] + [{"song_id": str(i)} for i in range(2, 20)],
    })

    q = client.find("/music/remote")["queue"]
    assert len(q) == 10
    assert "1" not in [t["songId"] for t in q]


def test_play_without_queue_still_works():
    """不给 queue 时不该塞一个空数组进去。"""
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    make_handlers(client)["eryu_play"]({"song_id": "1", "name": "就这一首"})
    assert "queue" not in client.find("/music/remote")


def test_play_without_name_does_not_fabricate():
    """`/music/url` 不返回歌名（server/eryu.py:537）。

    没拿到歌名就如实用 song_id 标识，别再输出「正在放 1382576173 - 」
    那种拿 ID 冒充歌名、歌手还是空的东西。
    """
    client = FakeClient({"ok": True, "url": "x", "cached": True})
    out = make_handlers(client)["eryu_play"]({"song_id": "999"})
    assert "song_id=999" in out


def test_play_unavailable_does_not_queue():
    """拿不到音频就别往队列里塞 —— 塞了她那边会放一首空的。"""
    client = FakeClient({"ok": False, "error": "no url, may need VIP"})
    out = make_handlers(client)["eryu_play"]({"song_id": "1"})

    assert "拿不到音频" in out
    assert not any(p == "/music/remote" for _, p, _ in client.calls)


def test_recent_parses_songs():
    """`/music/recent` 返回 {"ok", "songs"}（server/eryu.py:739）。"""
    client = FakeClient({"ok": True, "songs": [{
        "songId": 1382576173, "name": "Cruel Summer",
        "artist": "Taylor Swift", "playedAt": "2026-08-08T15:30:00+00:00",
    }]})
    out = make_handlers(client)["eryu_recent"]({})

    assert "Cruel Summer" in out
    assert "Taylor Swift" in out
    assert "1382576173" in out


def test_recent_empty_is_not_an_error():
    client = FakeClient({"ok": True, "songs": []})
    assert "没在共听页面听歌" in make_handlers(client)["eryu_recent"]({})


def test_roam_reads_singular_song():
    """`/music/roam` 返回的是 **`song` 单数、一次一首**，不是 `songs` 数组。

    原来这里找 `songs`，永远拿到空 —— 漫游从上线起就一直回
    「没找到歌」，而服务端其实每次都正常返回了一首。
    """
    client = FakeClient({"ok": True, "song": {
        "songId": 3410722310, "name": "FLY", "artist": "河铉雨", "album": "아파트 OST",
    }})
    out = make_handlers(client)["eryu_roam"]({})

    assert "3410722310" in out
    assert "FLY" in out
    assert "河铉雨" in out


def test_roam_does_not_send_limit():
    """服务端不认 limit（一次就一首），发了是噪音。"""
    client = FakeClient({"ok": True, "song": {"songId": 1, "name": "x"}})
    make_handlers(client)["eryu_roam"]({})
    assert client.find("/music/roam") == {}


def test_daily_uses_id_not_song_id():
    """⚠️ `daily` 用 `id`，`roam` 用 `songId` —— 服务端这两个接口
    字段名就是不一致的，别想当然统一。"""
    client = FakeClient({"ok": True, "songs": [{
        "id": 29747526, "name": "Who Says", "artist": "Selena Gomez", "album": "For You",
    }]})
    out = make_handlers(client)["eryu_daily"]({})

    assert "29747526" in out
    assert "Who Says" in out


def test_daily_empty_explains_why():
    """空的时候要说清楚是种子歌单没歌，不是坏了。"""
    client = FakeClient({"ok": True, "songs": []})
    assert "Liked" in make_handlers(client)["eryu_daily"]({})


def test_remote_poll_must_not_be_registered():
    """`eryu_remote_poll` 和 `eryu_play` 抢同一个一次性队列。

    两个都注册的话，小克 poll 的时候会把自己刚点给她的歌取走 ——
    她那边永远等不到。实现还留着，但**不许注册**。
    """
    from tools.eryu import register_all

    registered: list[str] = []

    class FakeLoop:
        def register(self, spec, handler):
            registered.append(spec.name)

    register_all(FakeLoop(), FakeClient())

    assert "eryu_recent" in registered
    assert "eryu_remote_poll" not in registered


def test_id_based_gets_use_plain_id():
    """这几个 GET 用的是 `id`（eryu.py:528/570/785/981/1160），本来就对，
    加断言是防止有人「统一」成 songId 时顺手改坏。"""
    client = FakeClient({"name": "x", "notes": "n"})
    handlers = make_handlers(client)

    handlers["eryu_get_lyric"]({"song_id": "1"})
    assert client.find("/music/lyric") == {"id": "1"}

    handlers["eryu_get_memory"]({"song_id": "2"})
    assert client.find("/music/memory") == {"id": "2"}
