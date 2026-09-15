"""Moments 的一次发帖决策记录（T3）：三段齐全，缺一段就构造不出来。

规格见 `Caelum-Moments-设计.md` 第七节「线上验证：两步走」，
实现在 `moments/record.py`，结构照抄 `temporal/result.py`。

## 这一组守的是什么

🔴 **shadow 必须有出口，否则它不是实验，是黑洞**（糖糖 2026-09-14 的原话）。
理解层跑了一周多 shadow，回头看被丢掉的那些记录，日志里只有一个数字，
模型推断了什么一个字都没留 —— 那一周的观察给不出任何结论。

Moments 的 shadow 要看的是「他一天想发几条、为什么」，所以一条记录三段齐全：

    ① 输入       drives 快照 + 互动量 + 距上次发帖  —— 不记这个没法复算
    ② 算了什么   冲动值 + 内心/时机 + 当时的阈值 + 骰子 —— 认错和算错分得开
    ③ 发没发      posted + reason + why_not_posted  —— 少了这段就是黑洞

③ 最容易被省掉，而它恰恰是那个信息。

## 每条测试能挡什么、不能挡什么

都写在各自 docstring 里（CAELUM-MAP 第三·五节：说不清自己在防什么的测试，
下次重构会被当噪音删掉）。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moments.impulse import THRESHOLD, Signals, impulse  # noqa: E402
from moments.record import MODES, NOT_POSTED_REASONS, MomentRecord  # noqa: E402


def _record(**overrides) -> MomentRecord:
    """造一条**合法**记录：没发，原因是没到阈值。

    每条测试只改自己要测的那个字段 —— 默认值全都合法，
    所以测试红了就一定是被改的那个字段炸的，不会是别的字段的连带。
    """
    signals = Signals(
        drives={"longing": 0.4, "playfulness": 0.28, "regret": 0.167},
        minutes_since_contact=240,
        turns_today=1,
        minutes_since_last_post=None,
    )
    kwargs = dict(
        at=datetime(2026, 9, 14, 21, 30, tzinfo=timezone.utc),
        mode="shadow",
        signals=signals,
        impulse=impulse(signals),
        threshold=THRESHOLD,
        dice=None,
        dice_p=None,
        posted=False,
        post_id=None,
        reason="below_threshold",
        why_not_posted="没到阈值",
    )
    kwargs.update(overrides)
    return MomentRecord(**kwargs)


def test_a_complete_record_can_be_constructed():
    """三段齐全的记录构造得出来 —— 没发的和发了的两种形状各一条。

    这是本文件唯一的「正向」基线：它证明后面那些 `ValueError` 不是
    因为类根本写错了（比如字段名打错、少一个必填字段），
    而是**真的**由那一条检查拦下来的。

    能挡：把检查写成「什么都拦」（比如 `if True: raise`）—— 那样这一条先红。
    不能挡：拦得够不够 —— 那是 2~6 条的事。
    """
    not_posted = _record()

    assert not_posted.posted is False
    assert not_posted.mode == "shadow"

    on_mode = _record(
        mode="on",
        posted=True,
        post_id="abc",
        reason="",
        why_not_posted="",
        dice=0.71,
        dice_p=0.5,
    )

    assert on_mode.posted is True
    assert on_mode.post_id == "abc"
    assert on_mode.reason == ""


def test_a_mode_outside_the_whitelist_cannot_be_constructed():
    """`mode` 只认 `MODES` 里那两个，别的**构造不出来**。

    🔴 `off` 是这里最要紧的一个：关掉 Moments 的时候连算都不算，
    不该有记录 —— 硬塞一条 `mode="off"` 的进来就是在骗审计
    （几天后翻日志会以为「那天在 shadow，只是没想发」）。
    `"ON"` / `""` 是同一类错的另外两张脸：大小写、漏填。

    能挡：把模式写成自由字符串（大小写各异、`off` 混进来）。
    不能挡：`mode` 填对了但**行为**不对（比如 shadow 真落了帖）—— 那是第 5 条。
    """
    for mode in ("off", "ON", ""):
        with pytest.raises(ValueError):
            _record(mode=mode)

    #: 白名单本身钉死：少了 `shadow` 或 `on` 就没模式可用，
    #: 多一个（比如把 `off` 加回来）就等于允许「关掉的时候也留记录」
    assert MODES == {"shadow", "on"}


def test_a_reason_outside_the_whitelist_cannot_be_constructed():
    """没发的原因必须落在 `NOT_POSTED_REASONS` 里 —— 自由文本构造不出来。

    这是「几天后要能**数**」的那一半（另一半是第 9 条）：
    「多少次没到阈值、多少次骰子没中、多少次发满了」统计的判据是
    `reason` 这个**枚举值**，所以措辞不能漂。自由文本那份照样留
    （`why_not_posted`），它是给人读的。

    能挡：新写一个没进白名单的原因（比如把「骰子没中」直接写成一句中文），
          统计脚本会因此静默少一类。
    不能挡：白名单**自己**缺项 —— 有人删掉 `"dice"` 时这一条照样绿
          （它测的是「表外的进不来」，不是「表里的都在」）—— 那是第 9 条。
    """
    with pytest.raises(ValueError):
        _record(reason="随便写的")


def test_not_posting_without_a_readable_reason_cannot_be_constructed():
    """没发就必须留下一句人话 —— 空的 / 全空白的 `why_not_posted` 构造不出来。

    🔴 这一条就是「不许变成黑洞」。糖糖 2026-09-14：理解层跑了一周多
    shadow，回头看被丢掉的那些记录**只有数字，一个字都没留**，
    那一周的观察给不出任何结论。`"   "` 和 `""` 一样要拦 ——
    图省事的人传的正是空白串，`if not x` 拦得住 `""` 但挡不住 `"   "`。

    能挡：空串、全空白串。**在构造那一刻**就炸，不会等到几天后翻日志
          才发现那批记录什么都没留下。
    不能挡：写了但是废话（「没发」）—— 那只能靠人看。
    """
    for why in ("", "   "):
        with pytest.raises(ValueError):
            _record(why_not_posted=why)


def test_shadow_mode_can_never_hold_a_real_post():
    """🔴 `mode="shadow"` 且 `posted=True` **构造不出来** —— 这是结构闸门，不是自觉。

    糖糖定的两步走：shadow 只算冲动、只记日志、**不落帖**。
    「shadow 里写了一条真帖」是这个功能最坏的一种坏 —— 她会在 Moments
    里看到一整批本该不存在于那里的帖子，而且事后分不清哪些是实验产物。
    所以做成**构造不出来**，而不是靠 loop 里记得写个 `if`。

    断言异常消息里含 `shadow` 是故意的：这条检查必须**认得出自己在拦什么**，
    将来有人把五条检查的顺序/文案改坏时，看日志就能直接指到这一条。

    能挡：loop 在 shadow 分支里忘了 `return`、或者把 `posted` 写死成 True。
    不能挡：`mode` 填 `"on"` 但**实际**没落帖（或 shadow 落了但记录里
          谎称 `posted=False`）—— 记录只能拦住自相矛盾的写法，
          拦不住故意撒谎；那要靠 T5 的 loop 测试。
    """
    with pytest.raises(ValueError) as err:
        _record(
            mode="shadow",
            posted=True,
            post_id="abc",
            reason="",
            why_not_posted="",
        )

    assert "shadow" in str(err.value)


def test_a_posted_record_must_name_its_post_and_carry_no_reason():
    """发了的记录不许自相矛盾：既要知道是哪条，也不许带着「没发的原因」。

    两半挡的是两种相反的错，所以放在一起（都是 `posted=True` 的形状）：

    · `post_id` 空 —— **审计断链**。它是 bridge 落库返回的 id，也是这条
      记录和真实那条帖之间唯一的缝；少了它，几天后看到「他发了」却点不回
      那条帖，也没法核对生成的内容（T8/T9 的界面全靠这条链）。

    · `reason` 非空 —— **记录自相矛盾**，而且会让统计出错：
      loop 先填好 `reason="dice"`、后来掷中了却忘了清空，
      「多少次骰子没中」就把这些真发出去的也算进去了。

    ⚠️ 这里必须用 `mode="on"`：用 shadow 的话，第 4 条结构闸门会先炸，
    这一条就变成「测的是别人的检查」——一写就绿，什么都没验到。
    （`post_id=""` 和 `None` 都要拦：空串和 None 一样是断链。）

    能挡：漏传/传空 id、掷中后没清 `reason`。
    不能挡：id 是不是**那条**帖 —— 那要和 bridge 对账，测试里验不了；
          也管不住 `why_not_posted`（规格只钉了这五条，它不在其中）。
    """
    for bad_id in (None, ""):
        with pytest.raises(ValueError):
            _record(mode="on", posted=True, post_id=bad_id, reason="",
                    why_not_posted="")

    with pytest.raises(ValueError):
        _record(mode="on", posted=True, post_id="abc", reason="dice",
                why_not_posted="")


def test_to_dict_carries_exactly_the_specified_keys():
    """`to_dict()` 的键**完全等于**规格列的那 19 个 —— 不是「至少包含」。

    用 `==` 不用 `issubset` 是故意的：issubset 挡不住「少一个键」，
    而少一个键正是这一层最可能悄悄发生的事（比如漏掉 `threshold`，
    几天后阈值一调，那批记录就再也解释不了了）。多出来的键同样要红 ——
    多出来的键会悄悄进入落库/落盘的那份数据，格式就漂了。

    `at` 必须是字符串、`drives` 必须是 dict：这个 dict 是**扁平、可 JSON
    序列化**的，直接 `json.dumps` 就该过（落库和日志两条路都靠它）。

    能挡：少键、多键、嵌套没摊平、`at` 留着 datetime 对象（json 序列化炸）。
    不能挡：值的**语义**对不对（比如 `at` 的时区）—— 那要和 loop 对账。
    """
    rec = _record()

    d = rec.to_dict()

    assert set(d) == {
        "at", "mode", "drives", "turns_today", "minutes_since_contact",
        "minutes_since_last_post", "posts_today", "value", "inner", "timing",
        "parts", "why", "threshold", "dice", "dice_p", "posted", "post_id",
        "reason", "why_not_posted",
    }
    assert isinstance(d["at"], str)
    assert isinstance(d["drives"], dict)
    assert json.dumps(d, ensure_ascii=False)          # 真的能序列化，不只是「像个字典」
    #: 值也得真接上字段 —— 光键对、值全是 None 也照样能满足上面几条
    assert d["at"] == rec.at.isoformat()
    assert d["drives"] == dict(rec.signals.drives)
    assert d["value"] == rec.impulse.value
    assert d["why"] == rec.impulse.why
    assert d["threshold"] == THRESHOLD
    assert d["reason"] == "below_threshold"


def test_log_emits_exactly_one_info_line_carrying_the_numbers(caplog):
    """`log()` 是 shadow 的出口：**一次判断恰好一行 INFO**，数字都在里面。

    三件事一起钉：

    · **恰好多 1 条**（不是 0 条、也不是三条）—— 拆成三条的话
      journalctl 里会被别的日志冲散，拼不回来（`temporal/result.py` 的教训）；
    · **级别必须是 INFO** —— 这个项目里 WARN 比 INFO 更容易被吞，
      而 DEBUG 线上根本不打（`docs/LOGGING.md` 第 4 条 + `NOX_LOG_LEVEL` 的说明）。
      shadow 的记录要是打不出来，整个 shadow 阶段就白跑，所以用 WARN/DEBUG
      都算错；
    · **三个值都在行里**：`value` / `threshold` / `reason`（或者 `posted`）。
      只写一句人话的记录复算不出来 —— 那正是理解层上次栽的地方。

    ⚠️ 断言只用数值和 ASCII 的键（`0.62` / `min_gap`），**不断言中文措辞**：
    措辞一改、编码一变，判据就永不成立（CAELUM-MAP 第三·五节「判据在路上变形」）。
    唯一一处带中文的断言用的是**测试自己传进去的那个值**
    （`not_posted.why_not_posted in message`）—— 它比的是「这句人话有没有进日志」，
    不是「日志里必须写某几个字」，所以措辞改了它照样成立。

    `value=0.62` 这种带标签的写法是故意的 —— 光断言 `0.62` 的话，
    impulse 的 `why` 里也有同一个数，把 value 整段删掉照样绿。

    发了的记录走的另一个分支也一起验：那条行里得有 `post_id`，
    否则「发了，但翻日志看不出是哪条」—— 审计照样断链。

    能挡：级别被写成 DEBUG/WARN、一次打多行、三个值里漏掉任何一个、
          drives 快照被省掉、③ 那句人话被省掉（黑洞）、发了的那条不报 id。
    不能挡：行读起来顺不顺 —— 那只有人看得出来。
    """
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("moments.record")
    not_posted = _record(
        reason="min_gap",
        why_not_posted="距上次发帖还不到间隔",
        dice=0.80,
        dice_p=0.5,
    )

    before = len(caplog.records)
    not_posted.log(logger)

    added = caplog.records[before:]
    assert len(added) == 1, f"一次判断该恰好一行，实际 {len(added)} 条"
    line = added[0]
    assert line.levelno == logging.INFO
    message = line.getMessage()
    assert "0.62" in message                       # 冲动值
    assert "value=0.62" in message                 # 而且是 value 那一格，不是 why 里顺带的
    assert "threshold=0.45" in message             # 当时的阈值
    assert "min_gap" in message                    # 结构化原因
    assert str(dict(not_posted.signals.drives)) in message   # ① 输入快照也在
    assert not_posted.why_not_posted in message    # ③ 那句人话也在（黑洞那一关）

    posted = _record(
        mode="on", posted=True, post_id="abc", reason="", why_not_posted="",
        dice=0.71, dice_p=0.5,
    )

    before = len(caplog.records)
    posted.log(logger)

    added = caplog.records[before:]
    assert len(added) == 1
    assert added[0].levelno == logging.INFO
    assert "value=0.62" in added[0].getMessage()
    assert "abc" in added[0].getMessage()          # 发了就得看得出是哪条


def test_every_whitelisted_reason_works_and_the_whitelist_stays_six():
    """白名单里**每一个**原因都真的能用，而且**恰好 6 个**。

    分两半，缺一半都拦不住错误：

    · 逐个构造 —— 有人从表里删掉 `"dice"`、或者代码里把某个值写成
      表外字符串（比如 `reason="dice_miss"`）时，那个原因会**永远构造不出来**，
      运行到那一步才抛 ValueError。loop 那一层的失败会打断整个 tick，
      所以要在构造层就发现。
    · `len == 6` —— 「空集/半集不是通过」（CAELUM-MAP 第三·五节的第一种
      假验证）。光逐个构造的话，表被删到只剩一条也照样绿。
      **以后加原因必须回来改这个数，那是故意的。**

    能挡：表里少一项、表被清空、某个原因在代码里写成了别的字符串。
    不能挡：这 6 个名字**起得对不对**（`daily_cap` 该不该叫 `cap` 是命名问题，
          机器验不了），也挡不住 loop 把某个原因填错 —— 那是 T5 的事。
    """
    assert len(NOT_POSTED_REASONS) == 6, "空集/半集不是通过"

    for reason in sorted(NOT_POSTED_REASONS):
        rec = _record(reason=reason, why_not_posted=f"（测试）{reason}")
        assert rec.reason == reason
        assert rec.posted is False
