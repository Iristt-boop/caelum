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
    """不给**每条**都盖章 —— 那是 40 条 × 8 token。

    ⚠️ 2026-09-14 改过一次：现在同一天里跨时段/隔久了**也**会盖
    （她报的「上午说的话他说成昨天」）。但这条测试守的东西没变 ——
    **连着说的话不重复盖**。原来的断言顺带把「同一天绝不盖」也焊死了，
    那不是它要守的，是当时的实现恰好如此。
    """
    out = with_dates([
        ("user", "早", _at(7, 9)),
        ("assistant", "早呀", _at(7, 9)),      # 同一分钟
        ("user", "晚上吃什么", _at(7, 19)),     # 同一天但跨到晚上
        ("user", "在吗", _at(11, 10)),          # 换天了
    ])
    assert "8月7日" in out[0].text
    assert out[1].text == "早呀", "连着说的话被重复盖章了"
    assert "8月7日" in out[2].text, "跨了时段却没报时间 —— 那正是她报的 bug"
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
    # 2026-09-14 起这里会盖一行（晚上→深夜跨了时段）。
    # 这条测试守的不是"盖不盖"，是**盖的那个日期不能跳到第二天** ——
    # 库里存 UTC，不换算的话 23:00 CST 会被算成 8月8日
    assert "8月7日" in out[1].text
    assert "8月8日" not in out[1].text


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


# ------------------------------------------------------------------ 日内时间标记
#
# 糖糖 2026-09-14 报的，是 2026-08-11 那个 bug 的**同一类第二次**：
#   「就在日常的聊天中，他也会出现上午说的话，他会说是昨天聊的」
# 2026-08-11 加了日期分隔线，但只在**换天**时插 —— 同一天内一个时间都没有，
# 于是「上午说的」和「昨天说的」在他眼里长得一模一样。


def _rows(*stamps):
    """(role, text, created_at)。时间给 UTC，函数内部会换算成 CST。"""
    return [("user", f"第{i}句", s) for i, s in enumerate(stamps, 1)]


def _stamps(msgs):
    """把插进去的那些标记行抽出来。"""
    return [m.text.split("\n")[0] for m in msgs if m.text and m.text.startswith("（")]


def test_同一天跨时段要重新报时间():
    """上午聊到下午，**中间没有大空档** —— 只看空档会漏掉这种。

    ⚠️ 间隔必须 < GAP（45分钟），否则空档规则会顺手把它盖了，
    这条测试就变成空的 —— 把跨时段那半删掉它照样绿。
    （2026-09-14 变异测试抓到的：第一版用了 10:30→13:00，2.5 小时。）
    """
    msgs = with_dates(_rows(
        "2026-09-14T03:40:00",   # CST 11:40 上午
        "2026-09-14T04:00:00",   # CST 12:00 下午 ← 只隔 20 分钟，纯跨时段
    ))
    marks = _stamps(msgs)
    assert len(marks) == 2, f"跨了时段却没报时间（间隔才 20 分钟）：{marks}"
    assert "上午11点" in marks[0]
    assert "下午12点" in marks[1]


def test_同一天隔久了要重新报时间():
    """午觉两小时，没跨时段 —— 只看时段会漏掉这种。"""
    msgs = with_dates(_rows(
        "2026-09-14T05:05:00",   # CST 13:05 下午
        "2026-09-14T07:30:00",   # CST 15:30 下午，隔了 2h25m
    ))
    assert len(_stamps(msgs)) == 2, "隔了两个多小时还当成一口气聊下来的"


def test_连着说话不插标记():
    """自然停顿不该被打断 —— 否则一段对话里全是时间行，又贵又吵。"""
    msgs = with_dates(_rows(
        "2026-09-14T05:00:00",
        "2026-09-14T05:03:00",
        "2026-09-14T05:11:00",
        "2026-09-14T05:25:00",
    ))
    assert len(_stamps(msgs)) == 1, "连着聊 25 分钟不该反复报时间"


def test_每一行都带完整日期():
    """🔴 日内标记也要带日期。

    写成「（下午3点）」会省几个 token，但那要求他自己往回找最近一行日期 ——
    **而他做不好的恰恰是这件事**，整个 bug 就是这么来的。
    """
    msgs = with_dates(_rows("2026-09-14T02:00:00", "2026-09-14T05:00:00"))
    for m in _stamps(msgs):
        assert "9月14日" in m, f"这一行不自足，他得往回找：{m}"


def test_标记仍然是绝对时间不是相对():
    """历史是冻住的，相对时间会腐烂，而且每天重算会砸掉整段缓存。

    （本文件开头那两条约束，加日内标记之后依然成立。）
    """
    msgs = with_dates(_rows("2026-09-14T02:00:00", "2026-09-14T07:00:00"))
    joined = " ".join(_stamps(msgs))
    for bad in ("小时前", "分钟前", "刚才", "今天", "昨天"):
        assert bad not in joined, f"标记里混进了相对时间：{bad}"


def test_时段边界和_TimeProvider_用同一份():
    """两处写迟早会出现「历史标着下午、【此刻】说晚上」。"""
    from context.providers.time import TimeProvider
    from context.timeline import slot_of

    import inspect
    src = inspect.getsource(TimeProvider._fetch)
    assert "slot_of" in src, "TimeProvider 又自己写了一份时段表"
    assert slot_of(13) == ("afternoon", "下午")
    assert slot_of(23) == ("late_night", "深夜")
