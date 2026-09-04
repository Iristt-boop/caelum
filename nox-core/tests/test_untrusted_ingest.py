"""摄入外部内容 → 自动交还 Work Grant（2026-09-04）。

守的是 prompt injection 链路上最后那个口子：

    Work Grant 开着  =  范围内改文件不弹窗问她
    读到投毒的网页    =  他的判断可能已经被带偏
    两个同时成立      =  没有第二道防线

所以只要在授权期间摄入了外部内容，就当场把授权交还。

## 🔴 这些测试为什么重要

这个机制**平时完全看不见** —— 不触发的时候和没有它一模一样。
所以它坏了也不会有人发现，只会在真出事那天才知道。
测试是唯一盯着它的东西。

最要紧的两条：
  1. 标记必须打在**代码路径**里，不是模型选择调用的
  2. 搜索**失败**时授权同样已经作废，通知不能漏
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.untrusted import UntrustedIngest  # noqa: E402


@pytest.fixture()
def ing():
    return UntrustedIngest()


# --------------------------------------------------------------- 核心行为

def test_授权开着时摄入外部内容会交还(ing):
    calls = []
    ing.set_revoker(lambda: calls.append("end"))
    ing.grant_opened()

    note = ing.mark("web_search")

    assert calls == ["end"], "应该调了一次交还"
    assert ing.grant_open is False
    assert note is not None


def test_没有授权时摄入不做任何事(ing):
    """先搜索、再开授权是正常流程，不该拦。

    开授权那一步她是看得见的（弹窗上有目标和范围），
    被带偏的请求她能拒绝。真正没防线的是「已经点过头之后才读到脏东西」。
    """
    calls = []
    ing.set_revoker(lambda: calls.append("end"))

    note = ing.mark("web_search")

    assert calls == []
    assert note is None


def test_交还之后再摄入不会重复调用(ing):
    """一次授权只交还一次，别把 work.end 打成连珠炮。"""
    calls = []
    ing.set_revoker(lambda: calls.append("end"))
    ing.grant_opened()

    ing.mark("web_search")
    ing.mark("computer_browse")
    ing.mark("web_search")

    assert calls == ["end"]


def test_交还后重开授权仍然会被再次拦住(ing):
    """她重新点头之后，这套机制要照常生效，不能只灵一次。"""
    calls = []
    ing.set_revoker(lambda: calls.append("end"))

    ing.grant_opened()
    ing.mark("web_search")
    ing.grant_opened()
    ing.mark("computer_browse")

    assert calls == ["end", "end"]


def test_手动交还后摄入不再触发(ing):
    calls = []
    ing.set_revoker(lambda: calls.append("end"))
    ing.grant_opened()
    ing.grant_closed()

    assert ing.mark("web_search") is None
    assert calls == []


# --------------------------------------------------------------- 告诉他为什么

def test_通知里要说清楚是被什么触发的(ing):
    """他得能跟她解释「我读了网页所以授权没了」，而不是一脸茫然地重试。"""
    ing.set_revoker(lambda: None)
    ing.grant_opened()
    note = ing.mark("web_search")
    assert "web_search" in note
    assert "重新调 computer_start_work" in note


def test_通知要拦住他默默重开(ing):
    """默默重开等于把这道闸变成一个减速带。"""
    ing.set_revoker(lambda: None)
    ing.grant_opened()
    note = ing.mark("computer_browse")
    assert "别默默重开" in note


# --------------------------------------------------------------- 坏掉的时候

def test_交还失败也要认为授权已经没了(ing):
    """⚠️ fail closed。

    交还调用炸了不代表授权还在（Gateway 那头会自己过期），
    但**我们这边必须当它没了** —— 状态留 True 的话，
    下一次摄入又会去调一个不存在的授权，而且他会以为还能免问改文件。
    """
    def boom():
        raise RuntimeError("链路断了")

    ing.set_revoker(boom)
    ing.grant_opened()

    note = ing.mark("web_search")     # 不抛

    assert ing.grant_open is False
    assert note is not None


def test_没装交还回调也不炸(ing):
    """测试里、或者手还没连上时就是没有回调。只记状态，不动作。"""
    ing.grant_opened()
    note = ing.mark("web_search")
    assert ing.grant_open is False
    assert note is not None


# --------------------------------------------------------------- 和搜索工具串起来

class _Res:
    def __init__(self, ok=True, data=None, error=""):
        self.ok, self.data, self.error = ok, data, error


class _FakeTavily:
    def __init__(self, res):
        self.res = res

    def post(self, path, body=None):
        return self.res


@pytest.mark.parametrize("res,where", [
    (_Res(True, {"results": [{"title": "t", "url": "u", "content": "c"}]}), "成功"),
    (_Res(False, error="HTTP 432"), "失败"),
])
def test_搜索无论成败都会交还并告知(monkeypatch, res, where):
    """⚠️ 失败那条最容易漏。

    标记是在**发请求之前**打的，所以搜索失败时授权其实也已经交还了。
    失败分支忘了带通知的话，他会以为授权还在，然后每次改文件都撞弹窗，
    自己也搞不清为什么 —— 这正是第一版写漏的地方。
    """
    import tools.search as search_mod

    probe = UntrustedIngest()
    calls = []
    probe.set_revoker(lambda: calls.append("end"))
    probe.grant_opened()
    monkeypatch.setattr(search_mod, "ingest", probe)

    handler = search_mod.make_handlers(_FakeTavily(res))["web_search"]
    out = handler({"query": "x"})

    assert calls == ["end"], f"{where}时也该交还"
    assert "授权已经自动交还" in out, f"{where}时漏了通知"


def test_空_query_不该作废授权(monkeypatch):
    """连请求都没发，不该有副作用。"""
    import tools.search as search_mod

    probe = UntrustedIngest()
    calls = []
    probe.set_revoker(lambda: calls.append("end"))
    probe.grant_opened()
    monkeypatch.setattr(search_mod, "ingest", probe)

    handler = search_mod.make_handlers(_FakeTavily(_Res(True, {})))["web_search"]
    handler({"query": "   "})

    assert calls == []
    assert probe.grant_open is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
