"""表情包目录（2026-09-28 加呆猫八条 46 张，49 → 95）。

挡三件事：
- 名单里有、工具说明里没列 —— 他不知道有这个表情，等于没加
- 说明里写的数量和名单对不上 —— 他会以为名单不全、自己编
- 名单外的名字照样「发出去」—— 落了库前端查不到图，她那边什么都看不到
  （「摸摸头」「乖巧」「晚安亲亲」都这么消失过）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import MEME_TAGS  # noqa: E402
from tools import context  # noqa: E402
from tools.intimate import MEME_SPEC, make_handlers  # noqa: E402


def test_名单里每个表情都写进了他看的说明():
    desc = MEME_SPEC.description
    missing = [t for t in MEME_TAGS if t not in desc]
    assert missing == [], f"说明里没列（他不知道有）：{missing}"


def test_说明里写的数量对得上():
    n = int(re.search(r"（(\d+) 个）", MEME_SPEC.description).group(1))
    assert n == len(MEME_TAGS) == len(set(MEME_TAGS)), (n, len(MEME_TAGS))


def _send(monkeypatch, tag):
    sent = []
    ctx = type("C", (), {"attach_meme": lambda self, t, **kw: sent.append(t)})()
    monkeypatch.setattr(context, "current", lambda: ctx)
    out = make_handlers(bridge=None)["send_meme"]({"tag": tag})
    return out, sent


def test_名单外的名字退回去不发(monkeypatch):
    out, sent = _send(monkeypatch, "摸摸头")
    assert sent == [], "编出来的名字发出去了 —— 她那边会什么都看不到"
    assert "没发出去" in out


def test_名单里的照常发(monkeypatch):
    out, sent = _send(monkeypatch, "大手拍头")
    assert sent == ["大手拍头"] and "发给她了" in out
