"""共影剧情记忆（2026-10-11，memory/watched.py）。

糖糖 10-11：「他知道看了什么，却不知道剧情。」—— 看到第三集，他没有前两集的记忆。
选 B：画面 + 网上**这一集**的分集剧情；World Model 存完整档案（前情从这取），OB 只存「我们一起看的」。

守的几条：
  · 认得出剧名 / 集数；电影、`01.mp4` 不硬认
  · 🔴 不剧透：前情只给**这一集之前**的；网上只查看过的这一集
  · 🔴 网上的剧情不进 OB；OB 只有「一起看时」
  · 网上结果连剧名都没有 → 当没查到；模型判不匹配 → 来源只写「画面」
  · 一场只整理一次；没画面不整理
  · 从使用方断言：前情真的出现在 `_dynamic` 里；观影源真的触发回调
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from memory import watched
from memory.watched import Episode, parse_title
from world_model import WorldModel

UTC = timezone.utc
START = datetime(2026, 10, 10, 6, 51, tzinfo=UTC)
END = datetime(2026, 10, 10, 7, 48, tzinfo=UTC)

FRAME = ("【共影·主动】她在看一部本地的片子，刚到第 374 秒，这里换场了，你想说一句。\n"
         "【片名】风吹半夏第三集.mp4\n【当前画面】户外乡村土路，一辆白色老式轿车停在路上。字幕：让你接着你就接着")
PAUSE = ("【共影】她暂停在 400.2 秒问你：\n【当前画面】一名中年男子坐在书桌前吃饭。\n\n这人是谁啊")


@pytest.fixture
def world(tmp_path):
    return WorldModel(tmp_path / "world.db")


# ---- 认片名 ------------------------------------------------------------

@pytest.mark.parametrize("title,want", [
    ("风吹半夏第三集.mp4", ("风吹半夏", 1, 3)),
    ("风吹半夏第十二集.mp4", ("风吹半夏", 1, 12)),
    ("风吹半夏 第21集", ("风吹半夏", 1, 21)),
    ("摩登家庭S01E01 (AAC音轨).mkv", ("摩登家庭", 1, 1)),
    ("Modern.Family.S02E05.1080p.WEB-DL.mkv", ("Modern.Family", 2, 5)),
])
def test_认得出剧和集(title, want):
    ep = parse_title(title)
    assert ep is not None and (ep.series, ep.season, ep.episode) == want


@pytest.mark.parametrize("title", ["01.mp4", "The.Sheep.Detectives.2026.1080p中英字幕.mp4", "", None])
def test_电影和光秃秃的编号不硬认(title):
    assert parse_title(title) is None


# ---- 素材 ---------------------------------------------------------------

def test_素材_画面带时间_她暂停问的那句算她说的():
    sid = "a" * 32
    rows = [
        {"session_id": sid, "role": "user", "text": FRAME},
        {"session_id": sid, "role": "assistant", "text": "这车停在荒郊野外，有点悬疑|||<心里>想她</心里>"},
        {"session_id": sid, "role": "user", "text": PAUSE},
        {"session_id": sid, "role": "user", "text": "（系统提示：这不是糖糖在跟你说话）"},
        {"session_id": sid, "role": "user", "text": "哈哈哈哈这个老头"},
    ]
    mat = watched.collect(rows)
    assert mat.frames[0].startswith("[06:14]") and "白色老式轿车" in mat.frames[0]
    assert "[06:40]" in mat.frames[1] and "这人是谁" not in mat.frames[1]
    assert "她：这人是谁啊" in mat.talk and "她：哈哈哈哈这个老头" in mat.talk
    assert not any("系统提示" in t for t in mat.talk), "他自己的开场白不是她说的"
    assert any(t.startswith("他：") and "心里" not in t and "想她" not in t for t in mat.talk)


# ---- 网上 ---------------------------------------------------------------

def test_网上结果没有剧名_当没查到():
    ep = Episode("风吹半夏", 3)
    assert watched.search_synopsis(lambda a: "某某短剧第3集：霸总……", ep) is None
    assert "许半夏" in watched.search_synopsis(lambda a: "风吹半夏第3集：许半夏…", ep)


def test_红线_只查这一集():
    seen = []
    watched.search_synopsis(lambda a: seen.append(a["query"]) or "", Episode("风吹半夏", 3))
    assert seen == ["风吹半夏 第3集 分集剧情"]


def test_网搜抛异常_不炸():
    def boom(a):
        raise RuntimeError("tavily 挂了")
    assert watched.search_synopsis(boom, Episode("风吹半夏", 3)) is None


# ---- 整条流水线 ---------------------------------------------------------

class Adapter:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def complete(self, messages, tools, **kw):
        self.prompts.append(messages[0].text)
        return SimpleNamespace(text=self.text, stop_reason="end_turn")


GOOD = ('{"web_matches": true, "plot": "许半夏和赵垒在废钢生意上第一次正面交锋……", '
        '"moments": ["她问那个吃饭的中年男人是谁", "她笑那个老头"], "characters": ["许半夏", "赵垒"]}')


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.asked = None

    def messages_between(self, a, b, clean_only=True):
        self.asked = (a, b)
        return self.rows


class OB:
    def __init__(self):
        self.held = []

    def hold(self, content, **kw):
        self.held.append((content, kw))
        return SimpleNamespace(ok=True)


def _run(world, *, rows=None, adapter=None, web=None, ob=None, title="风吹半夏第三集.mp4", wid="w3"):
    return watched.summarize_episode(
        title=title, started=START, ended=END, watch_id=wid,
        store=Store(rows if rows is not None else [{"session_id": "a" * 32, "role": "user", "text": FRAME}]),
        world=world, adapter=adapter or Adapter(GOOD),
        web_search=web if web is not None else (lambda a: "风吹半夏第3集：许半夏……"), ob=ob)


def test_红线_一集看完_World有完整档案_OB只有一起看的(world):
    ob = OB()
    got = _run(world, ob=ob)
    assert got["series"] == "风吹半夏" and got["episode"] == 3
    assert got["sources"] == ["画面", "网上分集剧情"]
    raw = world.query(watched.TYPE)[0].raw
    assert "许半夏" in raw["plot"]
    content, kw = ob.held[0]
    assert "她问那个吃饭的中年男人是谁" in content and "2026-10-10" in content
    assert "废钢" not in content, "网上的剧情不许进 OB —— 只放两个人的回忆"
    assert "共影" in kw["tags"]


def test_模型判网上不匹配_来源只写画面(world):
    bad = GOOD.replace('"web_matches": true', '"web_matches": false')
    assert _run(world, adapter=Adapter(bad))["sources"] == ["画面"]


def test_没查到网上的_来源只写画面_即使模型说匹配(world):
    assert _run(world, web=lambda a: "")["sources"] == ["画面"]


def test_同一场只整理一次(world):
    assert _run(world) is not None
    assert _run(world) is None
    assert len(world.query(watched.TYPE)) == 1


def test_没画面不整理_认不出是剧不整理(world):
    assert _run(world, rows=[{"session_id": "a" * 32, "role": "user", "text": "好看"}]) is None
    assert _run(world, title="01.mp4") is None
    assert world.query(watched.TYPE) == []


def test_模型回的不是JSON_不写(world):
    assert _run(world, adapter=Adapter("我觉得这集挺好看的")) is None
    assert world.query(watched.TYPE) == []


def test_素材窗口_前后放宽(world):
    st = Store([{"session_id": "a" * 32, "role": "user", "text": FRAME}])
    watched.summarize_episode(title="风吹半夏第三集.mp4", started=START, ended=END, watch_id="w",
                              store=st, world=world, adapter=Adapter(GOOD))
    a, b = st.asked
    assert a < START.isoformat() and b > END.isoformat()


def test_思考模型给够max_tokens():
    seen = {}

    class A(Adapter):
        def complete(self, messages, tools, **kw):
            seen.update(kw)
            return super().complete(messages, tools, **kw)
    watched.summarize(A(GOOD), Episode("风吹半夏", 3), watched.Material(frames=["x"]), None)
    assert seen["max_tokens"] >= 3000


# ---- 前情 ---------------------------------------------------------------

def _record(world, n, plot, series="风吹半夏", wid=None, later=0):
    world.observe("cowatch", watched.TYPE,
                  {"series": series, "season": 1, "episode": n, "label": f"第{n}集", "plot": plot},
                  START + timedelta(days=n + later), dedup_key=f"{watched.TYPE}/{wid or n}")


def test_红线_前情只给这一集之前的_不剧透(world):
    for n in (1, 2, 3, 5):
        _record(world, n, f"第{n}集的剧情")
    _record(world, 1, "摩登家庭的", series="摩登家庭", wid="mf1")
    items = watched.previously(world, Episode("风吹半夏", 4))
    assert [i["episode"] for i in items] == [1, 2, 3]
    out = watched.render_previously(Episode("风吹半夏", 4), items)
    assert "第5集" not in out and "摩登家庭" not in out
    assert out.index("第1集") < out.index("第3集"), "按集数顺序给"
    mf = watched.previously(world, Episode("摩登家庭", 2))
    assert [i["plot"] for i in mf] == ["摩登家庭的"], "别的剧的前情不许混进来"


def test_前情有预算_最近一集给得最多(world):
    for n in range(1, 13):
        _record(world, n, f"第{n}集" + "剧" * 600)
    out = watched.render_previously(Episode("风吹半夏", 13), watched.previously(world, Episode("风吹半夏", 13)))
    assert len(out) <= watched.PREV_BUDGET + 80
    assert "第12集" in out and "更早的几集省略了" in out


def test_同一集看了两次_取后写的那份(world):
    _record(world, 2, "旧的", wid="a")
    _record(world, 2, "新的", wid="b", later=5)   # 隔几天重看了一遍
    assert watched.previously(world, Episode("风吹半夏", 3))[0]["plot"] == "新的"


# ---- 接线：Nox._dynamic / 观影源 ------------------------------------------

def _nox(world):
    from agent import toolpacks
    from context import ContextProviderRegistry
    from nox import Nox
    n = Nox.__new__(Nox)
    n._restore_state_once = lambda: None
    n.context = ContextProviderRegistry()
    n.card_debt = {}
    n.loop = SimpleNamespace(tools={}, used_packs=toolpacks.UsedPacks())
    n.world = world
    return n


def test_红线_共影第四集_他每轮都带着前三集(world):
    for n in (1, 2, 3):
        _record(world, n, f"第{n}集：许半夏的事")
    d = _nox(world)._dynamic("这人是谁", False, runtime={
        "surface": "watch", "object": {"title": "风吹半夏第四集.mp4"}})
    assert "【前情】" in d and "第3集：许半夏的事" in d
    assert d.index("【你在哪】") < d.index("【前情】")


def test_不在共影就没有前情(world):
    _record(world, 1, "第1集")
    d = _nox(world)._dynamic("风吹半夏第二集好看吗", False, runtime={"surface": "chat"})
    assert "【前情】" not in d


def test_红线_观影源_一场看完只触发一次回调(world):
    from attention.sources.shared_activities import SharedActivitiesSource
    got = []
    rows = {"items": [{"id": "w3", "title": "风吹半夏第三集.mp4", "episode": "",
                       "started_at": "2026-10-10T06:51:09Z", "ended_at": "2026-10-10T07:48:25Z"}]}
    bridge = SimpleNamespace(get=lambda path, params=None: SimpleNamespace(ok=True, data=rows, error=None))
    src = SharedActivitiesSource(bridge=bridge, world=world, on_watched=got.append)
    src.poll(datetime(2026, 10, 10, 8, 0, tzinfo=UTC))
    src.poll(datetime(2026, 10, 10, 8, 5, tzinfo=UTC))
    assert len(got) == 1 and got[0]["title"] == "风吹半夏第三集.mp4" and got[0]["watch_id"] == "w3"


def test_红线_回填端点_收JSON_body():
    """10-11 线上 422：请求模型定义在 create_app 里，FastAPI 当成了 query 参数。走一遍 HTTP。"""
    from tests.test_api import FakeNox, client_for
    fake = FakeNox()
    got = []
    fake.cowatch_summarize = lambda info: got.append(info) or {"series": "风吹半夏", "episode": 1}
    r = client_for(fake).post("/api/nox/cowatch/summarize", json={
        "title": "风吹半夏第一集.mp4", "started_at": "2026-10-09T09:56:28.758Z",
        "ended_at": "2026-10-09T10:37:51.092Z", "watch_id": "w1"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and got[0]["started"].tzinfo is not None
