"""「想逗她」的第二个来源：想起开心的旧事（她 2026-10-06 拍板）。

09-29→10-06 他主动开口 185 次，心情里「想逗她」0 次 —— 促狭只看她此刻在不在闹，
而他找她时她多半不在聊天。现在：记忆库里开心的生活旧事 + Gallery 收藏的照片，
在他本来就要想起她的那一次里多一个可抽的心情。**只换心情，不加开口次数。**
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from attention import fond as fond_mod
from attention import service as service_mod
from attention.fond import FondSource
from attention.resonance import Drive
from attention.store import AttentionStore
from temporal import CST
from tests.test_think_budget import AWAKE, cn, make, think

NOW = datetime.now(CST)


def _iso(days: float) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _md(bid, name, days, valence, domains, body="那晚涮羊肉喝了一点酒，她笑得停不下来。", **extra):
    lines = ["---", f"id: {bid}", f"name: {name}", "type: dynamic", f"created: {_iso(days)}",
             f"valence: {valence}", "domain:", *[f"- {d}" for d in domains]]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    return "\n".join(lines + ["---", "", body])


@pytest.fixture()
def buckets(tmp_path):
    d = tmp_path / "dynamic"
    d.mkdir()
    files = {
        "warm.md": _md("aaa111", "涮羊肉微醺夜", 10, 0.9, ["饮食", "恋爱"]),
        "code.md": _md("bbb222", "Caelum OS 改版", 10, 0.95, ["编程", "创作"]),      # 修代码的开心不算
        "codelove.md": _md("bbb333", "糖糖完成共听系统", 10, 0.95, ["编程", "恋爱"]),  # 带了编程就不算，哪怕也带恋爱
        "sad.md": _md("ccc333", "怕蛇", 10, 0.3, ["情绪"]),                          # 不开心
        "new.md": _md("ddd444", "刚才的晚安", 1, 0.9, ["恋爱"]),                       # 太新，是「刚才」不是「想起」
        "old.md": _md("eee555", "七月的事", 90, 0.9, ["恋爱"]),                        # 太远
        "noname.md": _md("fff666", "c3a42cd0de15", 10, 0.9, ["恋爱"]),                 # 只有编号
        "pinned.md": _md("ggg777", "准则", 10, 0.9, ["恋爱"], pinned="true"),
    }
    for n, t in files.items():
        (d / n).write_text(t, encoding="utf-8")
    return str(tmp_path)


class Gallery:
    def __init__(self, items):
        self.items = items

    def get(self, path):
        assert "favorites" in path
        #: 真接口回的是**裸数组**（bridge /api/gallery/list 的 res.json(rows)），不是 {items}
        return SimpleNamespace(ok=True, data=self.items)


def _src(tmp_path, buckets=None, photos=(), rng=None):
    return FondSource(store=AttentionStore(tmp_path / "a.db"), buckets_dir=buckets,
                      bridge=Gallery(list(photos)), rng=rng)


# ---------------------------------------------------------------- 挑哪件

def test_只挑生活里开心的旧事(tmp_path, buckets):
    s = _src(tmp_path, buckets)
    got = {f.title for _, f in s._memories(NOW)}
    assert got == {"涮羊肉微醺夜"}


def test_Gallery收藏的照片也算_要有描述_不能是刚拍的(tmp_path):
    photos = [
        {"id": "p1", "description": "十一趴在键盘上睡着了", "created_at": _iso(20)},
        {"id": "p2", "description": "", "created_at": _iso(20)},
        {"id": "p3", "description": "今天的晚霞", "created_at": _iso(0.5)},
    ]
    got = _src(tmp_path, photos=photos)._photos(NOW)
    assert [f.key for _, f in got] == ["p1"]
    assert got[0][1].kind == "photo" and got[0][1].query


def test_用过的十四天内不再想起(tmp_path, buckets):
    s = _src(tmp_path, buckets)
    f = s.pick(NOW)
    assert f is not None and f.title == "涮羊肉微醺夜"
    s.mark_used(f, NOW)
    only = lambda now: [(0.9, f)]        # noqa: E731 —— 只看这一件，别让别的旧事这时候长到够格
    s._memories = only
    assert s.pick(NOW + timedelta(days=13)) is None
    assert s.pick(NOW + timedelta(days=15)) == f


def test_记忆库和Gallery都读不到_不抛_返回None(tmp_path):
    class Broken:
        def get(self, path):
            return SimpleNamespace(ok=False, error="502")
    s = FondSource(store=AttentionStore(tmp_path / "a.db"), buckets_dir=str(tmp_path / "nope"), bridge=Broken())
    assert s.pick(NOW) is None


# ---------------------------------------------------------------- 接进主动开口

def _fond(kind="memory"):
    return fond_mod.Fond(kind=kind, key="k1", title="涮羊肉微醺夜", detail="涮羊肉微醺夜：她笑得停不下来",
                         age_days=10, query="十一趴在键盘" if kind == "photo" else "")


class FakeFond:
    def __init__(self, f):
        self.f, self.used, self.picked = f, [], 0

    def pick(self, now):
        self.picked += 1
        return self.f

    def mark_used(self, f, now):
        self.used.append(f)


def _drv(name, x):
    return Drive(name=name, intensity=x, load=x, because=["x"], evidence=[], source_count=1, computed_at=NOW)


def _svc(tmp_path, monkeypatch, *, drives=None, fond=None, chance=0.0, reply="想你了"):
    svc, calls = make(tmp_path, AWAKE, reply=reply)
    svc.fond = FakeFond(fond or _fond())
    svc.drives = lambda now=None: dict(drives or {})
    monkeypatch.setattr(service_mod.random, "random", lambda: chance)
    return svc, calls


def test_想起开心的旧事_他从这件事说起_说出口才记用过(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch)          # 只有这一个心情可抽 → 必抽中「想逗她」
    assert think(svc, cn(5, 15)) is True
    assert "你忽然想起一件开心的旧事" in calls[0] and "她笑得停不下来" in calls[0]
    assert svc.fond.used, "说出口了要记用过，不然明天又想起同一件"


def test_他没说出口_不记用过(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, reply=None)
    think(svc, cn(5, 15))
    assert calls and svc.fond.used == []


def test_照片要提示他可以发出来(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, fond=_fond("photo"))
    think(svc, cn(5, 15))
    assert "send_gallery_image" in calls[0] and "十一趴在键盘" in calls[0]


def test_她不舒服的时候不翻旧事逗她(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, drives={"concern": _drv("concern", 0.64)})
    think(svc, cn(5, 15))
    assert svc.fond.picked == 0, "她感冒那天（担心 0.64）就不该去翻开心的旧事"
    assert "开心的旧事" not in calls[0]


def test_没轮到就不想(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, chance=0.99)
    think(svc, cn(5, 15))
    assert "开心的旧事" not in calls[0]


def test_她正在闹_用她的气氛_不翻旧事(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, drives={"playfulness": _drv("playfulness", 0.5)})
    think(svc, cn(5, 15))
    assert "开心的旧事" not in calls[0]


def test_抽中别的心情_就当没想起(tmp_path, monkeypatch):
    svc, calls = _svc(tmp_path, monkeypatch, drives={"longing": _drv("longing", 0.9)})
    monkeypatch.setattr("attention.care.her_state.pick_mood",
                        lambda drives, recent=None, rng=None: __import__("attention.care.her_state", fromlist=["Mood"]).Mood(
                            "longing", "想她", 0.9, []))
    think(svc, cn(5, 15))
    assert "开心的旧事" not in calls[0] and svc.fond.used == []


def test_不加开口次数_八次上限照旧(tmp_path, monkeypatch):
    from attention.service import AWAKE_DAILY_MAX
    svc, calls = _svc(tmp_path, monkeypatch)
    for i in range(AWAKE_DAILY_MAX + 2):
        think(svc, cn(5, 15, i))
    assert len(calls) == AWAKE_DAILY_MAX
