"""OB 客户端的解析与退化行为 —— 不打网，只喂假 breath 返回。

为什么这个文件值得存在（审计 8.4 点名的第一个硬缺口）：
`ob_client.py` 是边界法则 R4 指定的**唯一一条记忆咽喉**，全部长期记忆
都从这里进出，而在 2026-09-12 之前它一行测试都没有。

这里守的核心是一条**不对称**：
  · `recall()`   切分失败可以退回「全部当动态」—— 最坏多付一轮钱
  · `core_principles()` **绝对不许**退回 —— 那会把随机浮现的记忆冻成
    静态前缀里的「核心准则」，变成他往后每一轮的自我指令
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.ob_client import (  # noqa: E402
    MemoryResult,
    OmbreBrain,
    split_breath,
)

CORE_H = "=== 核心准则 ==="
DYN_H = "=== 浮现记忆 ==="


class FakeOB(OmbreBrain):
    """只替掉 breath 这一层 —— 上面的解析逻辑全部走真代码。"""

    def __init__(self, reply: MemoryResult) -> None:
        super().__init__("http://fake/mcp")
        self.reply = reply
        self.queries: list[str] = []

    def breath(self, query: str = "", **kw) -> MemoryResult:  # type: ignore[override]
        self.queries.append(query)
        return self.reply


# ---------------------------------------------------------------- split_breath

def test_split_both_sections():
    core, dyn = split_breath(f"{CORE_H}\n准则一\n{DYN_H}\n今天吃了面")
    assert core == "准则一"
    assert dyn == "今天吃了面"


def test_split_core_only():
    core, dyn = split_breath(f"{CORE_H}\n准则一")
    assert core == "准则一" and dyn == ""


def test_split_without_any_header_falls_back_to_dynamic():
    """没有头 = 全部当动态。这个退化本身是对的，错的是谁去用它。"""
    core, dyn = split_breath("一段没有任何标记的文字")
    assert core == ""
    assert dyn == "一段没有任何标记的文字"


# ------------------------------------------------------------ core_principles

def test_core_principles_normal():
    ob = FakeOB(MemoryResult(True, text=f"{CORE_H}\n准则一\n准则二"))
    r = ob.core_principles()
    assert r.ok and r.text == "准则一\n准则二"
    assert ob.queries == [""]          # 不带 query 才只返回钉选桶


def test_core_principles_never_falls_back_to_dynamic(caplog):
    """🔴 这条是 4.4 的核心：没有核心准则头时必须返回空。

    原来写的是 `core or dynamic`，于是：
      一个钉选桶都没有 → 没有头 → 整段归 dynamic → 被当成核心准则
      → 冻进带缓存的静态前缀 → **成为他往后每一轮的自我指令**
    """
    leaked = "他今天觉得糖糖大概是不想理他了"
    ob = FakeOB(MemoryResult(True, text=leaked))

    with caplog.at_level(logging.WARNING):
        r = ob.core_principles()

    assert r.ok
    assert r.text == ""                       # 空，不是 leaked
    assert leaked not in r.text
    # 缺失必须看得见 —— 否则「前缀为空」就成了另一个静默失败
    assert any("核心准则" in rec.message or CORE_H in str(rec.args)
               for rec in caplog.records), "没有头的时候必须留一条 WARNING"


def test_core_principles_propagates_failure():
    ob = FakeOB(MemoryResult(False, error="连不上"))
    r = ob.core_principles()
    assert not r.ok and r.error == "连不上"


# -------------------------------------------------------------------- recall

def test_recall_may_fall_back_to_dynamic():
    """recall 这边退化是安全的 —— 它进的是这一轮的上下文，不是静态前缀。"""
    ob = FakeOB(MemoryResult(True, text="一段没有标记的浮现"))
    assert ob.recall("面").text == "一段没有标记的浮现"


def test_recall_skips_empty_query():
    ob = FakeOB(MemoryResult(True, text="不该被用到"))
    r = ob.recall("   ")
    assert r.ok and r.text == ""
    assert ob.queries == []                   # 空 query 连网都不打


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
