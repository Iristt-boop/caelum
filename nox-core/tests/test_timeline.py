"""时间感知测试。

起因（糖糖 2026-08-11）：

> 「nox 现在对时间没有概念。我前几天做的美甲他会一直以为是今天做的。」

根因是 `data/store.py` 读历史时只 `SELECT role, text`，把 `created_at` 扔了。

这一批钉四件事：

1. **历史里只能有绝对日期** —— 相对时间会腐烂，也会砸缓存
2. **日期分隔线只在换天时出现** —— 每条盖章太贵
3. **记忆检索里绝对+相对都要有** —— 只给相对，明天再读就错了
4. **没写日期的记忆不许硬编一个**
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.timeline import humanize, relativize, with_dates  # noqa: E402

CST = timezone(timedelta(hours=8))
TODAY = date(2026, 8, 11)


def _at(day: int, hour: int = 12) -> str:
    """库里存的是 UTC，这里给的是中国时间，转过去。"""
    return datetime(2026, 8, day, hour, tzinfo=CST).astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------- 日期分隔线


def test_first_message_carries_its_date():
    out = with_dates([("user", "我今天去做美甲了", _at(7))])
    assert "8月7日" in out[0].text
    assert "我今天去做美甲了" in out[0].text


def test_first_line_does_not_claim_to_be_the_session_start():
    """⚠️ 历史只取最近 40 条，第一条是**窗口**起点不是**会话**起点。

    2026-08-11 部署时实测：8月6日开的会话，窗口第一条是 8月7日的，
    标成「这段对话开始于 8月7日」就是在骗他，而且骗得理直气壮。
    会话真正的起点由 `nox._session_span()` 讲，那个读 sessions.created_at。
    """
    out = with_dates([("user", "窗口第一条", _at(7))])
    assert "开始于" not in out[0].text


def test_separator_only_on_day_change():
    """同一天的消息不重复盖章 —— 每条都盖是 40 条 × 8 token。"""
    out = with_dates([
        ("user", "早", _at(7, 9)),
        ("assistant", "早呀", _at(7, 9)),
        ("user", "晚上吃什么", _at(7, 19)),
        ("user", "在吗", _at(11, 10)),     # 换天了
    ])
    assert "8月7日" in out[0].text
    assert out[1].text == "早呀"            # 同一天，不盖
    assert out[2].text == "晚上吃什么"
    assert "8月11日" in out[3].text         # 换天，盖


def test_history_never_contains_relative_time():
    """⚠️ 这条是本文件最重要的断言。

    历史消息是冻住的 —— 今天写进去的「4天前」，后天再读还是「4天前」，
    而实际已经六天前了。而且相对时间每天变一次，会让整段历史的缓存失效
    （DeepSeek 是自动前缀匹配，见 `context/providers/time.py:78`）。
    """
    out = with_dates([("user", "做美甲", _at(7)), ("user", "在吗", _at(11))])
    joined = " ".join(m.text or "" for m in out)
    for bad in ("天前", "昨天", "前天", "上周", "个月前"):
        assert bad not in joined, f"历史里不该出现相对时间：{bad}"


def test_late_night_counts_as_the_same_day_in_china():
    """库里是 UTC。不换算的话她晚上 8 点后说的话会被算成第二天 ——
    而那正是她最常聊天的时段。"""
    out = with_dates([
        ("user", "在吗", _at(7, 20)),
        ("user", "睡了", _at(7, 23)),
    ])
    assert out[1].text == "睡了"            # 同一天，不该多一行


def test_missing_timestamp_is_tolerated():
    """少一个日期，也比整段历史读不出来强。"""
    out = with_dates([("user", "没时间戳", None), ("user", "有的", _at(11))])
    assert out[0].text == "没时间戳"
    assert "8月11日" in out[1].text


def test_broken_timestamp_does_not_raise():
    out = with_dates([("user", "坏的", "不是时间")])
    assert out[0].text == "坏的"


# ---------------------------------------------------------------- 相对时间


def test_humanize():
    assert humanize(0) == "今天"
    assert humanize(1) == "昨天"
    assert humanize(2) == "前天"
    assert humanize(4) == "4天前"
    assert humanize(9) == "上周"
    assert humanize(20) == "2周前"
    assert humanize(45) == "上个月"
    assert humanize(90) == "3个月前"


def test_relativize_keeps_the_absolute_date():
    """**补，不是替换。** 绝对日期必须留着 ——
    相对的会腐烂，而且她问「具体哪天」时他还得答得出来。"""
    out = relativize("2026-08-08 夜，糖糖立规矩：睡前问关不关灯", TODAY)
    assert "2026-08-08" in out
    assert "3天前" in out


def test_relativize_handles_every_format_seen_in_production():
    """这四种写法是从线上记忆库里抓出来的，不是编的。"""
    cases = {
        "2026-08-08": "3天前",
        "2026.08.09": "前天",
        "2026年8月7日": "4天前",
        "8月10日": "昨天",              # 没写年份 → 按当年算
    }
    for text, expect in cases.items():
        assert expect in relativize(text, TODAY), f"{text} 没标出 {expect}"


def test_no_date_means_no_guess():
    """线上很多桶压根没写日期（「糖糖睡前撒娇要拥抱」）。
    硬安一个只会让他说错。"""
    text = "糖糖睡前撒娇要拥抱"
    assert relativize(text, TODAY) == text


def test_bucket_id_is_not_mistaken_for_a_date():
    """`[bucket_id:45a0cb11647a]` 里有一串数字，别切进去。"""
    text = "[bucket_id:45a0cb11647a] 📌 记忆桶: 睡前撒娇要拥抱"
    assert relativize(text, TODAY) == text


def test_impossible_date_is_left_alone():
    assert relativize("2月30日", TODAY) == "2月30日"
    assert relativize("13月1日", TODAY) == "13月1日"


def test_undated_future_rolls_back_a_year():
    """12 月的记忆在 1 月读到 —— 没写年份时该算成去年，不是「以后」。"""
    out = relativize("12月25日", date(2026, 1, 5))
    assert "上周" in out          # 11 天前
    assert "以后" not in out


def test_emotion_score_is_not_a_date():
    """`[情感:V0.9/A0.3]` 里也有斜杠和数字。"""
    text = "[主题:睡眠, 恋爱] [情感:V0.9/A0.3]"
    assert relativize(text, TODAY) == text
