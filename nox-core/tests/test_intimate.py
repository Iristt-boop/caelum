"""语音条 / 设备 / Obsidian。

设备那几条测的是**兜底**：mode 是花样不是强度，模型偶尔会拿它当强度调，
那样发出去的是停止档 —— 表现成「他说加大了，实际停了」。
描述里写了不代表他一定照做，所以代码里也得挡一层。
"""

from __future__ import annotations

import pytest

from tools import context
from tools.bridge_client import BridgeResult
from tools.github_obsidian import GithubObsidianError
from tools.intimate import make_handlers


class FakeBridge:
    def __init__(self, responses: dict | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}

    def _reply(self, path):
        return self.responses.get(path, BridgeResult(True, {"ok": True}))

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._reply(path)

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        return self._reply(path)


def _h(**responses):
    b = FakeBridge(responses)
    return make_handlers(b), b


# ---------------------------------------------------------------- 语音条


def test_语音条挂进上下文():
    h, _ = _h()
    with context.scope() as ctx:
        out = h["send_voice_message"]({"en": "[softly] go to sleep", "zh": "快睡吧"})
    assert ctx.attachments == [
        {"type": "voice", "tts": "[softly] go to sleep", "zh": "快睡吧"}
    ]
    assert "已经发给她" in out


def test_空内容不发语音():
    h, _ = _h()
    with context.scope() as ctx:
        h["send_voice_message"]({"en": "  "})
    assert ctx.attachments == []


# ---------------------------------------------------------------- 设备


@pytest.mark.parametrize("bad", [2, 3, 5, 0, 9, None, "1"])
def test_花样只放行_1_和_4(bad):
    """2/3/5 是停止档、6+ 无效。放过去就是「说加大了实际停了」。"""
    h, b = _h()
    h["toy_set"]({"mode": bad, "intensity": 30})
    assert b.calls[0][2]["mode"] == 1


@pytest.mark.parametrize("mode", [1, 4])
def test_合法花样原样传(mode):
    h, b = _h()
    h["toy_set"]({"mode": mode, "intensity": 20})
    assert b.calls[0][2]["mode"] == mode


def test_强度夹在_0_到_100():
    h, b = _h()
    h["toy_set"]({"intensity": 500})
    assert b.calls[0][2]["intensity"] == 100
    h["toy_set"]({"intensity": -5})
    assert b.calls[1][2]["intensity"] == 0


def test_强度不是数字时不发指令():
    h, b = _h()
    assert "0-100" in h["toy_set"]({"intensity": "很大"})
    assert b.calls == []


def test_停止走_stop_不走花样():
    h, b = _h()
    h["toy_stop"]({})
    assert b.calls[0][2] == {"cmd": "stop", "mode": 0, "intensity": 0}


def test_设备指令失败要抛出去():
    h, _ = _h(**{"/api/toy/set": BridgeResult(False, error="连不上")})
    with pytest.raises(RuntimeError, match="设备指令发送失败"):
        h["toy_set"]({"intensity": 10})


def test_状态说清楚只是指令():
    h, _ = _h(**{"/api/toy/state": BridgeResult(True, {"cmd": "set", "mode": 1, "intensity": 30})})
    assert "强度=30" in h["toy_status"]({})


# ---------------------------------------------------------------- Obsidian


class FakeGithub:
    """记下写了什么。可以让它抛错，测「不能假装写好了」。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail

    def write(self, path, content, message=None):
        if self.fail:
            raise GithubObsidianError("GitHub 返回 403: 没有写权限")
        self.calls.append(("write", path, content))

    def append(self, path, content, message=None):
        if self.fail:
            raise GithubObsidianError("GitHub 返回 403: 没有写权限")
        self.calls.append(("append", path, content))


def _gh(fail: bool = False):
    b = FakeBridge()
    g = FakeGithub(fail)
    return make_handlers(b, g), b, g


def test_写笔记走_github_不碰_bridge():
    """2026-08-05 起只有 GitHub 这一条路 —— 本机 Agent 那条整个下线了。"""
    h, b, g = _gh()
    out = h["save_github_note"]({"path": "情书/2026-07-28.md", "content": "今天想你了"})
    assert g.calls == [("write", "情书/2026-07-28.md", "今天想你了")]
    assert b.calls == []          # 不该再往 bridge 发
    assert "情书/2026-07-28.md" in out


def test_追加走_append():
    h, _, g = _gh()
    h["append_github_note"]({"path": "a.md", "content": "x"})
    assert g.calls[0][0] == "append"


def test_github_失败必须抛_不能假装写好了():
    """吞掉的话他会说「写好了」，而实际什么都没写。"""
    h, _, _ = _gh(fail=True)
    with pytest.raises(RuntimeError, match="Obsidian 写入失败"):
        h["save_github_note"]({"path": "a.md", "content": "x"})


def test_没配仓库时抛错_不静默退回已删掉的_bridge_口():
    """原来这里会退回 bridge → 电脑 Agent。那个口已经删了，退回去只会 404。"""
    h, b = _h()                    # 不给 gh_obsidian
    with pytest.raises(RuntimeError, match="没配 GITHUB_OBSIDIAN_REPO"):
        h["save_github_note"]({"path": "a.md", "content": "x"})
    assert b.calls == []


def test_缺参数不发请求():
    h, b, g = _gh()
    assert "缺" in h["save_github_note"]({"path": "a.md", "content": ""})
    assert b.calls == [] and g.calls == []
