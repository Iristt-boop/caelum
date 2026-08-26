"""控制标记绝不能出现在她的聊天记录里。

2026-08-18 糖糖报的：主动关心那条路，他回了 `[pass 30]`，
speaker 当时只认 `[SKIP]`，没匹配上就把整段当正常回复推了出去 ——
**`[pass 30]` 原样落进了她的 chat**。

根因是他手上有两套词汇：
  唤醒链  `[NEXT n]` / `[PASS n]` / `[STOP]` / `[DONE]`（wakeup.py 的 _CTRL）
  惦记    `[SKIP]`（speaker 的 _THINK prompt）

现在的规矩：**主动关心这条路上，任何控制标记都等于「这次不说」。**
"""

from __future__ import annotations

import re

from attention.speaker import _HOLD


def test_认得所有控制标记():
    for raw in ("[SKIP]", "[skip]", "[PASS 30]", "[pass 30]", "[STOP]",
                "[NEXT 60]", "[DONE]", "[ SKIP ]", "[PASS  45]"):
        assert _HOLD.search(raw), f"没认出 {raw}"


def test_正常的话不会被误判():
    for raw in ("今天写得怎么样了？", "你昨天说要去买那个东西，买到了吗",
                "[心疼]", "我看到你发的图了"):
        assert not _HOLD.search(raw), f"把正常的话当成标记了：{raw}"


def test_标记能被剥干净():
    """兜底路径：万一有标记没触发「不说」，也不能让它进聊天记录。"""
    assert _HOLD.sub("", "今天写得怎么样了？\n[NEXT 60]").strip() == "今天写得怎么样了？"
    assert _HOLD.sub("", "[pass 30]").strip() == ""


def test_pass30那个原始现场():
    """回归：糖糖 2026-08-18 在 chat 里看到的就是这一条。"""
    raw = "[pass 30]"
    assert _HOLD.search(raw), "这就是当时漏掉的那个 —— 只认 SKIP 匹配不上"
