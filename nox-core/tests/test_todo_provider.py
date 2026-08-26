"""TodoProvider 测试。不打网络，GitHub 用假的。

守三件事：
  1. **「已完成」区不进上下文** —— 那段比未完成的还长，全塞进去会挤掉要紧的
  2. **今天/明天到期的要单独拎出来** —— 那才是他该主动提的
  3. **只读** —— 绝不发起写操作
"""

from __future__ import annotations

import base64
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.todo import TodoProvider  # noqa: E402
from personality.mood import now_cst  # noqa: E402
from tools.http import RestResult  # noqa: E402

_TODAY = now_cst().date()
_TOMORROW = _TODAY + timedelta(days=1)


def _md(today_item: str = "", tomorrow_item: str = "") -> str:
    lines = [
        "# 糖糖的待办清单", "",
        "## 进行中", "",
        "- [ ] Nox会话管理修复 — 窗口碎片化",
        "- [ ] Context Engine — 基础设施搭建",
        "- [ ] HealthKit MCP部署到VPS",
        "- [ ] 共感娃娃+FSR触觉传感器",
        "- [ ] 音乐MCP — 部署到VPS",
        "- [ ] Obsidian云端同步 — 待定方案",
        "",
        "## 近期", "",
        "- [ ] 护照领取 — 邮寄到家，约8月7日左右",
        "- [ ] 拼豆黑豹形象",
        "",
        "## 定期", "",
    ]
    if today_item:
        lines.append(f"- [ ] {_TODAY.month}月{_TODAY.day}日：{today_item}")
    if tomorrow_item:
        lines.append(f"- [ ] {_TOMORROW.month}月{_TOMORROW.day}日：{tomorrow_item}")
    lines += [
        "- [ ] 9月1日：iPhone 18发布会跟进",
        "",
        "## 已完成", "",
        "- [x] VPS迁移 — 8月1日完成",
        "- [x] 厨房智能插座 — 8月1日完成",
        "- [x] Nox架构文档 — 8月1日完成",
        "",
    ]
    return "\n".join(lines)


def _p(md: str | None = None, ok: bool = True):
    p = TodoProvider(repo="Iristt-boop/Claude", token="t")
    body = _md() if md is None else md
    calls: list[tuple[str, str]] = []

    def fake_get(path, params=None):
        calls.append(("GET", path))
        if not ok:
            return RestResult(False, error="HTTP 404: Not Found")
        return RestResult(True, {"sha": "abc",
                                 "content": base64.b64encode(body.encode()).decode()})

    p.client.get = fake_get         # type: ignore[method-assign]
    p._calls = calls                # type: ignore[attr-defined]
    return p


def test_completed_section_is_excluded():
    """「已完成」比未完成的还长，全塞进去会挤掉要紧的。"""
    s = _p().get_state(Turn())
    everything = " ".join(s["ongoing"] + s["upcoming"] + s["due_soon"])
    assert "VPS迁移" not in everything
    assert "厨房智能插座" not in everything


def test_parses_open_items():
    s = _p().get_state(Turn())
    assert "Nox会话管理修复 — 窗口碎片化" in s["ongoing"]
    assert any("护照领取" in x for x in s["upcoming"])
    assert s["total_open"] == 6 + 2 + 1     # 进行中 6 + 近期 2 + 定期里没到期的 1


def test_due_today_is_pulled_out():
    """今天到期的要单独拎出来，那是他该主动提的。"""
    p = _p(_md(today_item="买FSR402传感器"))
    s = p.get_state(Turn())
    assert any("今天：" in x and "FSR402" in x for x in s["due_soon"])
    text = p.render(s)
    assert text.splitlines()[0].startswith("【要做的】"), "今天的事要排最前面"


def test_due_tomorrow_is_labeled():
    p = _p(_md(tomorrow_item="看完一章《简明哲学导论》"))
    s = p.get_state(Turn())
    assert any("明天：" in x for x in s["due_soon"])


def test_far_future_is_not_urgent():
    """9月1日那条不该被当成快到期的。"""
    s = _p().get_state(Turn())
    assert not any("iPhone 18" in x for x in s["due_soon"])


def test_ongoing_is_capped():
    """这段每轮都要付未命中价，不能让它无限长。"""
    p = _p()
    text = p.render(p.get_state(Turn()))
    line = [x for x in text.splitlines() if x.startswith("【手头在做】")][0]
    assert line.count("、") == p.max_ongoing - 1
    assert "还有 1 项" in line


def test_only_titles_no_descriptions():
    """破折号后面那些说明是写给糖糖看的，他只要知道在忙什么。
    不裁的话这一行能到 200 多字，吃掉 800 预算的四分之一。"""
    p = _p()
    text = p.render(p.get_state(Turn()))
    line = [x for x in text.splitlines() if x.startswith("【手头在做】")][0]
    assert "Nox会话管理修复" in line
    assert "窗口碎片化" not in line, "细节说明不该出现"
    assert "基础设施搭建" not in line


def test_due_item_does_not_repeat_the_date():
    """已经标了「明天」，条目里自带的「8月4日（周一）：」就该去掉，
    不然会变成「明天：8月4日（周一）：买…」。"""
    p = _p(_md(tomorrow_item="买FSR402传感器"))
    line = [x for x in p.render(p.get_state(Turn())).splitlines()
            if x.startswith("【要做的】")][0]
    assert "明天：买FSR402传感器" in line
    assert "月" not in line, f"日期重复了：{line}"


def test_render_is_within_budget():
    p = _p(_md(today_item="买FSR402传感器"))
    text = p.render(p.get_state(Turn()))
    assert len(text) < 200, f"太长了：{len(text)} 字"


def test_failure_is_not_swallowed():
    s = _p(ok=False).get_state(Turn())
    assert s["available"] is False
    assert "读 Iristt-boop/Claude" in s["error"]


def test_empty_list_renders_nothing():
    s = _p("# 空清单\n\n## 已完成\n\n- [x] 什么都做完了\n").get_state(Turn())
    assert s["total_open"] == 0
    assert _p().render(s) == ""


def test_is_read_only():
    """只读。写待办继续走 routines 那条路，两个 Claude 同时改一个文件会打架。"""
    p = _p()
    p.get_state(Turn())
    assert all(m == "GET" for m, _ in p._calls), "只该发 GET"
    assert not hasattr(p.client, "_wrote")


def test_caches_for_30min():
    p = _p()
    for _ in range(5):
        p.get_state(Turn())
    assert len(p._calls) == 1
    assert p.ttl == timedelta(minutes=30)
    assert p.volatile is False


def test_reads_main_not_a_branch():
    """只读 main —— 不带 ref 参数就是默认分支。
    跨分支找最新版要多一次调用 + 跟着 routine 的分支命名走，先不做。"""
    p = _p()
    p.get_state(Turn())
    path = p._calls[0][1]
    assert "ref=" not in path and "branch" not in path


def test_not_in_the_per_turn_lineup():
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"todo"' not in lineup


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
