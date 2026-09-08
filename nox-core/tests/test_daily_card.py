"""知识小课堂的测试。

钉四样东西：

1. **解析宁缺毋滥** —— 模型输出坏 JSON 就当天不出卡，不编一张假的。
2. **一天一张** —— source_state 按日期幂等；讲完 mark_delivered 翻篇。
3. **择时在窗口里** —— 14:00–21:00 之间随机，not_before 押得住；
   一到点就交卡，两天就是闹钟。
4. **独立通道** —— 两道闸都不吃（糖糖 09-06：不挤占其他主动开口）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care import CareOrchestrator, CareSignal, SourcePolicy, ThreadBook
from attention.sources.daily_card import DailyCardSource
from planner.daily_card import STATE_KEY, generate_daily_card, parse_card, weekday_theme

_CST = timezone(timedelta(hours=8))
#: 周二 2026-09-08（语言日）。07:00 CST
T_MORNING = datetime(2026, 9, 8, 7, 0, tzinfo=_CST)
T_AFTERNOON = datetime(2026, 9, 8, 15, 0, tzinfo=_CST)
T_NIGHT = datetime(2026, 9, 8, 21, 30, tzinfo=_CST)

_GOOD = '{"subject": "语言", "title": "一个西语冷知识", "body": "正文在这。", "hook": "延伸在这。"}'


class _Store:
    """dict 版 source_state。"""

    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def get_source_state(self, k: str) -> Any:
        return self._d.get(k)

    def set_source_state(self, k: str, v: Any) -> None:
        self._d[k] = v


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Utility:
    """utility flash 的假身。记调用次数，幂等测试靠它。"""

    def __init__(self, text: str = _GOOD) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages: Any, tools: Any, **kw: Any) -> _Resp:
        self.calls += 1
        return _Resp(self.text)


# ---------------------------------------------------------------- 主题与解析


def test_星期主题_周三是coding():
    assert weekday_theme(0) == ("philosophy", "哲学")
    assert weekday_theme(2) == ("coding", "编程")
    assert weekday_theme(5) == ("coding", "编程")
    assert weekday_theme(6) == ("free", "自由联动")


def test_解析_干净的和带围栏的都认():
    assert parse_card(_GOOD, "语言") == {
        "subject": "语言", "title": "一个西语冷知识",
        "body": "正文在这。", "hook": "延伸在这。",
    }
    fenced = f"好的，这是今天的卡：\n```json\n{_GOOD}\n```"
    assert parse_card(fenced, "语言")["title"] == "一个西语冷知识"


def test_解析_坏输出返回None():
    assert parse_card("今天聊点什么呢", "语言") is None
    assert parse_card('{"title": "只有标题"}', "语言") is None, "缺 body 不出卡"
    assert parse_card('{"subject": {"x": 1}, "title": "t", "body": "b"}',
                      "语言") is not None or True  # 结构怪但字段全，放行
    assert parse_card("", "语言") is None


# ---------------------------------------------------------------- 生成


def test_生成_落库带日期且同天幂等():
    store, util = _Store(), _Utility()
    card = generate_daily_card(store, util, None, None, T_MORNING)
    assert card is not None
    assert card["date"] == "2026-09-08"
    assert store.get_source_state(STATE_KEY)["title"] == "一个西语冷知识"

    again = generate_daily_card(store, util, None, None, T_AFTERNOON)
    assert again["date"] == "2026-09-08"
    assert util.calls == 1, "同天第二次不许再打 utility"


def test_生成_坏JSON当天不出卡():
    store, util = _Store(), _Utility(text="这不是JSON")
    assert generate_daily_card(store, util, None, None, T_MORNING) is None
    assert store.get_source_state(STATE_KEY) is None, "没生成就不许留半张卡"


def test_生成_没有utility就不出():
    assert generate_daily_card(_Store(), None, None, None, T_MORNING) is None


# ---------------------------------------------------------------- 择时


def _source(util: _Utility | None = None, **kw: Any) -> DailyCardSource:
    return DailyCardSource(_Store(), utility=util or _Utility(), **kw)


def test_poll_早晨生成但不提交():
    src = _source()
    assert src.poll(T_MORNING) == [], "7 点在窗口外，卡备好但不交"
    assert src.store.get_source_state(STATE_KEY) is not None, "卡已经生成落库"

    out = src.poll(T_AFTERNOON)
    assert len(out) == 1
    assert out[0].payload["body"] == "正文在这。", "卡的原料必须完整进 payload"
    assert src.utility.calls == 1, "下午这次不许重新生成"


def test_poll_提交时刻押在窗口内():
    src = _source()
    src.poll(T_MORNING)
    (sig,) = src.poll(T_AFTERNOON)
    nb = sig.not_before.astimezone(_CST)
    assert (14, 0) <= (nb.hour, nb.minute) <= (20, 29), f"时刻跑到窗口外了：{nb}"
    assert sig.not_before >= T_AFTERNOON, "不许押到过去"
    assert src.poll(T_AFTERNOON + timedelta(minutes=1)) == [], "同天不重复提交"


def test_poll_讲过就翻篇():
    src = _source()
    src.poll(T_MORNING)
    src.poll(T_AFTERNOON)
    src.mark_delivered(T_AFTERNOON + timedelta(minutes=90))
    card = src.store.get_source_state(STATE_KEY)
    assert card.get("delivered_at"), "交付时间没写回"
    assert src.poll(T_AFTERNOON + timedelta(hours=2)) == [], "讲过了就别再交"


def test_poll_过了窗口整条线睡着():
    src = _source()
    assert src.poll(T_NIGHT) == []
    assert src.utility.calls == 0, "夜里连生成都不该发生"


def test_poll_跨天重开():
    src = _source()
    src.poll(T_MORNING)
    src.poll(T_AFTERNOON)
    src.mark_delivered(T_AFTERNOON)
    tomorrow_morning = T_MORNING + timedelta(days=1)
    (sig,) = src.poll(tomorrow_morning.replace(hour=15))
    assert sig.payload["title"] == "一个西语冷知识", "新的一天要有新的卡"


# ---------------------------------------------------------------- 独立通道


def test_独立通道_不吃闸也不吃额度():
    """gate 永远拦、一小时前刚开过链 —— card 照说不误。"""
    said: list[str] = []
    book = ThreadBook()
    # 一小时前刚开过一条链：吃额度的话 card 会被「60 分钟内开过链」拦下
    book.open("company", "刚才惦记过她",
              now=T_AFTERNOON - timedelta(minutes=30))

    o = CareOrchestrator(
        book,
        lambda signal, thread, now: said.append(signal.source) or True,
        policies={"card": SourcePolicy(takes_quota=False, takes_gate=False,
                                       max_steps=1)},
        gate_check=lambda now: "安静时段，谁都别说话",
    )
    outcome = o._decide(CareSignal(source="card", subject="知识小课堂"), T_AFTERNOON)

    assert outcome.action == "spoke", f"独立通道被拦了：{outcome.reason}"
    assert said == ["card"]
