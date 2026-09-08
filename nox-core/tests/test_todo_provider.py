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
#: 一个「既不是今天、也不是明天」的日子。
#
#  🔴 **不能写死日期，也不能简单地 +N 天。**
#  原来这里是硬编码的「9月1日」，于是每年 8/30~9/1 那几天它真的变成
#  "快到期"，`test_far_future_is_not_urgent` 无故变红 —— 2026-08-31
#  就红了一次，而那天的改动跟待办毫无关系。测试挂在日历上，
#  是一颗迟早会响的定时炸弹。
#
#  ⚠️ 改成 `_TODAY + 90 天` 也不行：provider 是按**当年**解析「M月D日」的
#  （`_match_date(item, today.year)`），跨年之后「2月13日」会被算成今年的
#  2 月 13 日 —— 那是过去，测的就不是"远期"了。
#  所以取一个**保证落在今年之内**、且离今天足够远的日子。
_OTHER = date(_TODAY.year, 12, 15) if _TODAY.month < 7 else date(_TODAY.year, 1, 15)


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
        f"- [ ] {_OTHER.month}月{_OTHER.day}日：iPhone 18发布会跟进",
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


def test_只有今天和明天才算快到期():
    """不是今天也不是明天的，一律不进 due_soon。

    这才是 provider 真正的规则（`(when - today).days in (0, 1)`）——
    原来那条测试叫「远期不算紧急」，但远近不是判据，**是不是今明两天**才是。
    照真规则写，测试就不会再被日历绊倒。
    """
    s = _p().get_state(Turn())
    assert not any("iPhone 18" in x for x in s["due_soon"])
    #: 它没消失，只是归到别处去了 —— 不进 due_soon 不等于被吞掉
    assert any("iPhone 18" in x for x in s["upcoming"] + s["ongoing"])


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


# ------------------------------------------------------- 本地清单那条路（2026-09-08）
#
# 🔴 上面所有测试走的都是 **GitHub 路径**，那条路的 `due_soon` 只收今明两天
# 到期的、通常一两条 —— 所以「不裁不限」看起来没问题，`test_render_is_within_budget`
# 也一直是绿的。
#
# 而线上早在 2026-08-18 就改读**本地清单**了，那条路的 `due_soon` 装的是
# **整个「进行中」分区**。换源时只改了取数没改渲染，于是：
#   `Context 超出 800 字预算，这轮略过: todo` —— 48 小时 61 次，
#   他每天有几十轮根本看不见她的待办，而且不报错。
#
# **测试覆盖的是没在跑的那条路，正在跑的那条一条没测。**
# 下面这几条补的就是这个。


class _FakeBridge:
    """假的 bridge，只回 `/api/todo/list`。"""

    def __init__(self, sections: dict) -> None:
        self.sections = sections

    def get(self, path, params=None):
        assert path == "/api/todo/list"
        return RestResult(True, {"ok": True, "sections": self.sections})


def _local(**sections):
    p = TodoProvider(bridge=_FakeBridge(sections))
    return p, p.get_state(Turn())


def test_本地清单的进行中全进了_due_soon():
    """先钉住这个**含义差异**本身 —— 它就是病根。

    走 GitHub 时 due_soon = 今明两天到期的；走本地清单时 = 整个「进行中」。
    以后谁再换源，这条会提醒他回去看渲染那一段。
    """
    _, s = _local(进行中=["每天 09:00 吃药", "9月8日 交房租"], 随时=["读尼采"])
    assert len(s["due_soon"]) == 2
    assert all(x.startswith("今天：") for x in s["due_soon"])


def test_今天要做的也要裁掉破折号后面的说明():
    """那些说明是写给糖糖看的，他只要知道是哪件事。"""
    p, s = _local(进行中=["写晨检卡 —— 要接日志摘要、还要处理静音名单和突增判据"])
    line = p.render(s)
    assert "写晨检卡" in line
    assert "静音名单" not in line, f"细节说明不该出现：{line}"


def test_今天要做的有条数上限():
    p, s = _local(进行中=[f"第{i}件事" for i in range(12)])
    line = [x for x in p.render(s).splitlines() if x.startswith("【要做的】")][0]
    assert line.count("、") == p.max_due - 1
    assert f"还有 {12 - p.max_due} 项" in line


def test_一整屏待办也不许吃光预算():
    """🔴 这条是这次事故的正身。

    12 条、每条都带一长段说明 —— 线上真实形状。修之前这里能渲染到
    **一千多字**，而整个 dynamic_system 的预算只有 800，
    于是 todo 每次都被 `ContextRegistry.render()` 整条丢掉。
    """
    p, s = _local(
        进行中=[f"第{i}件事 —— {'细' * 60}" for i in range(12)],
        随时=[f"挂着的{i} —— {'节' * 60}" for i in range(8)],
        近期=[f"近期{i} —— {'说' * 60}" for i in range(5)],
    )
    text = p.render(s)
    assert len(text) < 300, f"太长了：{len(text)} 字\n{text}"


def test_没有待办时什么都不说():
    """🔴 不许编一个「你今天没什么事」出来 —— 那和「我没查到」是两回事。"""
    p, s = _local(进行中=[], 随时=[], 近期=[])
    assert p.render(s) == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
