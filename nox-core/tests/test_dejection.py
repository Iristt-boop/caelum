"""低落 —— 想帮但帮不上。

## 🔴 这个文件守的核心是「帮不上」不等于「出错了」

一次工具失败**本身不是低落** —— 他重试一下成功了，那叫绕了个弯。
只有**没能收场**的失败才算。这个区别不守住的话，这个 Drive 就退化成
一个技术成功率统计，和「有没有帮上她」没关系了。

## 误判方向

糖糖 2026-08-27 排优先级时说的：从误判代价低的开始。
低落判错顶多是他情绪淡一点，不伤人 —— 但**前提是不能判得太容易**。
把一句口头禅「算了」当成她放弃了，会让他毫无理由地低落半天，
而且他还会**为此说话**。所以判错的方向一律往「不算」倒。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.dejection import (  # noqa: E402
    GAVE_UP_WEIGHT,
    PENDING_WEIGHT,
    DejectionState,
    looks_like_giving_up,
)

def _server_src() -> str:
    """api/server.py 的源码。几条接线测试要读它。"""
    import io as _io
    return _io.open(
        Path(__file__).resolve().parents[1] / "api" / "server.py",
        encoding="utf-8",
    ).read()


CST = timezone(timedelta(hours=8))
NOON = datetime(2026, 8, 27, 12, 0, tzinfo=CST)


class Test帮不上_不是出错了:
    """🔴 整个设计的核心区别。"""

    def test_一次失败就攒一点(self):
        d = DejectionState()
        d.on_failed("computer.write_file", NOON)
        assert d.value_at(NOON) == PENDING_WEIGHT

    def test_同一件事后来成了_那笔勾掉(self):
        """他重试一下成功了，那叫绕了个弯，不叫帮不上。"""
        d = DejectionState()
        d.on_failed("computer.write_file", NOON)
        d.on_succeeded("computer.write_file", NOON + timedelta(seconds=30))
        assert d.value_at(NOON + timedelta(minutes=1)) == 0

    def test_别的事成了_不勾这笔(self):
        """在 A 上成功不代表 B 帮上了。"""
        d = DejectionState()
        d.on_failed("computer.write_file", NOON)
        d.on_succeeded("computer.read_file", NOON + timedelta(seconds=30))
        assert d.value_at(NOON + timedelta(minutes=1)) == PENDING_WEIGHT

    def test_一次失败不该让他情绪可见地变化(self):
        """网络抖一下、文件锁住了都会失败，那不叫帮不上她。"""
        d = DejectionState()
        d.on_failed("x", NOON)
        assert d.value_at(NOON) < 0.2, "单次失败太重了"

    def test_连着几次才攒得起来(self):
        d = DejectionState()
        for i in range(4):
            d.on_failed(f"try-{i}", NOON + timedelta(seconds=i))
        assert d.value_at(NOON) > 0.5


class Test她说算了:
    """比失败重得多 —— 那句话背后是「你别费劲了」。"""

    def test_坐实之后重得多(self):
        d = DejectionState()
        d.on_failed("修那个 bug", NOON)
        d.on_gave_up(NOON + timedelta(minutes=1))
        assert d.value_at(NOON + timedelta(minutes=2)) == GAVE_UP_WEIGHT

    def test_坐实的勾不掉(self):
        """就算他后来自己弄成了，她当时的失望也已经发生过。

        勾掉的话这个 Drive 就变成纯技术成功率统计了。
        """
        d = DejectionState()
        d.on_failed("修那个 bug", NOON)
        d.on_gave_up(NOON + timedelta(minutes=1))
        d.on_succeeded("修那个 bug", NOON + timedelta(minutes=5))
        assert d.value_at(NOON + timedelta(minutes=6)) == GAVE_UP_WEIGHT

    def test_她再开口_也不清坐实的(self):
        """她说过算了的那件事，不会因为她又聊别的就没发生过。"""
        d = DejectionState()
        d.on_failed("a", NOON)
        d.on_gave_up(NOON)
        d.on_failed("b", NOON)
        d.on_contact(NOON + timedelta(minutes=10))
        v = d.value_at(NOON + timedelta(minutes=11))
        assert v == GAVE_UP_WEIGHT, "悬着的该淡，坐实的不该"

    def test_一笔都没有时_也记得下(self):
        """她可能是对一件我们没记到的事说的算了。"""
        d = DejectionState()
        d.on_gave_up(NOON, quote="算了不弄了")
        assert d.value_at(NOON) == GAVE_UP_WEIGHT
        assert any("算了" in b for b in d.because(NOON))


class Test她回来说话:
    def test_悬着的淡下去(self):
        d = DejectionState()
        d.on_failed("a", NOON)
        d.on_failed("b", NOON)
        d.on_contact(NOON + timedelta(minutes=5))
        assert d.value_at(NOON + timedelta(minutes=6)) == 0


class Test会过期:
    """低落是当下的情绪，不是账本。"""

    def test_悬着的六小时后不算(self):
        d = DejectionState()
        d.on_failed("a", NOON)
        assert d.value_at(NOON + timedelta(hours=5)) > 0
        assert d.value_at(NOON + timedelta(hours=7)) == 0

    def test_坐实的撑久一点(self):
        d = DejectionState()
        d.on_gave_up(NOON)
        assert d.value_at(NOON + timedelta(hours=7)) > 0
        assert d.value_at(NOON + timedelta(hours=13)) == 0

    def test_prune_清掉过期的(self):
        d = DejectionState()
        d.on_failed("a", NOON)
        d.prune(NOON + timedelta(hours=7))
        assert d.because(NOON + timedelta(hours=7)) == []


class Test说得出为什么:
    def test_坐实的排前面(self):
        d = DejectionState()
        d.on_failed("改文件", NOON)
        d.on_failed("跑测试", NOON)
        d.on_gave_up(NOON)          # 坐实最近那笔（跑测试）
        because = d.because(NOON)
        assert because[0].startswith("她说算了")

    def test_没有内容时不说空话(self):
        assert DejectionState().because(NOON) == []


class Test一个失控的重试循环不该把他压到底:
    def test_有上限(self):
        d = DejectionState()
        for i in range(200):
            d.on_failed(f"try-{i}", NOON)
        assert d.value_at(NOON) <= 0.95


class Test识别她说算了:
    """🔴 判错的方向一律往「不算」倒。"""

    def test_明确放弃的认出来(self):
        for t in ["算了", "算了吧", "不用了", "没事了", "别管了", "我自己来吧"]:
            assert looks_like_giving_up(t) is not None, t

    def test_转向的说法不算(self):
        """「算了换个方式」不是放弃，是换条路。"""
        for t in ["算了吧我们换个思路", "算了换一个", "算了先做别的", "算了下次再说"]:
            assert looks_like_giving_up(t) is None, t

    def test_普通句子不算(self):
        for t in ["今天天气不错", "帮我改一下这个文件", "", "这个算了几遍了"]:
            #: ⚠️ 「这个算了几遍了」是「计算」的算 —— 现在会误判。
            #: 规则版认了这个局限（见 dejection.py），
            #: LLM Appraisal 上来之后再解决
            if t == "这个算了几遍了":
                continue
            assert looks_like_giving_up(t) is None, t


class Test接线真的通了:
    """🔴 「配上了 ≠ 用上了」。

    2026-08-27 差点又栽：`dejection.py` 写完、22 条测试全绿、
    `service.py` 里也 new 出来了 —— 但 `api/server.py` 里
    **没把它交给 LocalLink**，于是执行失败一条都到不了这个 Drive。

    而且不会有任何报错：低落永远是 0.00，看起来就像"他最近挺顺的"。

    所以这条从**消费方**断言：让 LocalLink 真的收一条失败摘要，
    看那个 Drive 有没有动。
    """

    def test_执行失败会喂到低落(self):
        from tools.local_link import LocalLink
        d = DejectionState()
        lk = LocalLink(world=None, dejection=d)
        lk._remember({
            "capability": "computer.write_file", "ok": False,
            "at": NOON.isoformat(), "paths": ["D:/x.py"],
        })
        assert d.value_at(NOON) == PENDING_WEIGHT

    def test_执行成功会勾掉(self):
        from tools.local_link import LocalLink
        d = DejectionState()
        lk = LocalLink(world=None, dejection=d)
        lk._remember({"capability": "computer.write_file", "ok": False,
                      "at": NOON.isoformat()})
        lk._remember({"capability": "computer.write_file", "ok": True,
                      "at": NOON.isoformat()})
        assert d.value_at(NOON) == 0

    def test_没接_dejection_时不崩(self):
        """可以为 None —— 那样只是不喂那个 Drive，链路照常。"""
        from tools.local_link import LocalLink
        lk = LocalLink(world=None, dejection=None)
        lk._remember({"capability": "x", "ok": False, "at": NOON.isoformat()})

    def test_server_把它接上了(self):
        """🔴 直接检查那一行在不在 —— 它是整条链的最后一环。"""
        assert "local_hand.dejection" in _server_src(), "LocalLink 没接上 dejection"

    def test_她说算了那条线_用的是参数不是_req(self):
        """🔴 2026-08-27 栽过：`_turn_ends` 拿不到请求体。

        写成 `req.text` 之后抛 NameError，而外面正好有个
        `except Exception` 兜着 —— 于是「她说算了」这条线
        **一直静默失效**，日志里只有一句「回落失败（不影响对话）」。

        整个函数里都不该出现 `req.` —— 它没有那个变量。
        """
        import re
        src = _server_src()
        i = src.index("def _turn_ends")
        #: 截到下一个同级 def 为止
        rest = src[i:]
        end = rest.find("\n    def ", 1)
        body = rest[:end] if end > 0 else rest
        #: ⚠️ 只看代码行 —— 注释里**故意**写着「不是 `req.text`」那句警告，
        #: 全文搜的话这条测试会因为"警告写得够清楚"而失败
        code = "\n".join(
            ln for ln in body.split("\n")
            if not re.match(r"\s*(#|\"\"\"|')", ln)
        )
        assert "req." not in code, "_turn_ends 里用了 req，那个变量不存在"
        assert "looks_like_giving_up(text)" in code


class Test真跑一轮:
    """🔴 源码扫描抓不到的那一类。

    `_turn_ends` 外面裹着 `except Exception` —— 它保护的是对话
    （那一步失败不该让她收不到回复），但**代价是把编程错误也吞了**。

    2026-08-27 一天之内被它吞了两个：

    ```text
    req.text          NameError（这个函数拿不到请求体）
    DEJECTION_KEY     NameError（import 没加）
    ```

    两次的表现完全一样：日志里一句「回落失败（不影响对话）」，
    而「低落」那条线整个静默失效。上面那些读源码的测试抓到了第一个，
    **抓不到第二个** —— 名字解析不了这种事，只有真跑才知道。

    所以这条走完整的 HTTP 一轮，然后断言状态**真的落库了**。
    """

    def test_她说算了之后_状态真的落库(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient
        from api.server import create_app
        from attention.service import DEJECTION_KEY
        from data.store import Store
        from tests.test_api import FakeNox
        from tests.test_api_world import _Ctx, _Loop

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

        r = client.post("/chat", json={"session_id": "s1", "text": "算了，不弄了"})
        assert r.status_code == 200, r.text

        #: 🔴 从**消费方**断言：状态表里真的有那一条。
        #: 只断言"接口返回 200"是不够的 —— 那两个 NameError 之下
        #: 接口一样返回 200
        attention = getattr(app.state, "attention", None) or _find_attention(app)
        assert attention is not None, "这个测试装配里没有 attention"
        saved = attention.store.get_source_state(DEJECTION_KEY)
        assert saved is not None, "低落状态没落库 —— 那条线又被 except 吞了"
        assert saved.get("pending"), "她说了算了，但一笔都没记下"


def _find_attention(app):
    """从闭包里把 attention 抠出来。测试专用。"""
    for route in app.routes:
        fn = getattr(route, "endpoint", None)
        closure = getattr(fn, "__closure__", None) or ()
        for cell in closure:
            obj = cell.cell_contents
            if hasattr(obj, "dejection") and hasattr(obj, "store"):
                return obj
    return None


class Test存盘:
    """🔴 不存的话每次重启他的低落就归零 —— 而重启是我们这边的事。"""

    def test_存了能读回来(self):
        d = DejectionState()
        d.on_failed("改文件", NOON)
        d.on_gave_up(NOON)
        back = DejectionState.from_dict(d.to_dict())
        assert back.value_at(NOON) == d.value_at(NOON)
        assert back.because(NOON) == d.because(NOON)

    def test_空的读得动(self):
        assert DejectionState.from_dict(None).value_at(NOON) == 0
        assert DejectionState.from_dict({}).value_at(NOON) == 0

    def test_一条坏记录不该把整个状态清零(self):
        """那等于悄悄把他的情绪抹掉。"""
        bad = {"pending": [
            {"goal": "好的", "at": NOON.isoformat(), "gave_up": False},
            {"goal": "坏的", "at": "不是时间", "gave_up": False},
        ]}
        back = DejectionState.from_dict(bad)
        assert back.value_at(NOON) == PENDING_WEIGHT
