"""Moments 的冲动判断（T2）：几件事一起压着，才够得着「想发一条」。

规格见 `Caelum-Moments-设计.md` 第三节，实现在 `moments/impulse.py`。

## 这一组守的是什么

🔴 **不是规则机器人。** `if drive == "longing": post` 是这一层最该防住的东西 ——
那等于「她太久没说话就一定发一条」，一个月后打开全是同一句话。

防法是**乘法**：内心有事 × 时机合适，两边都得有。第 1、2 条是**配套的两条**：
单独一条 drive 哪怕时机满分也不够（0.40 < 0.45），再压上第二件事才够。
这两条一起才说明「不是规则机器人」；少任何一条，把实现写成 max 或写成
「有 longing 就发」都能蒙混过去。

## 每条测试能挡什么、不能挡什么

都写在各自 docstring 里（CAELUM-MAP 第三·五节「配套的两条」：
说不清自己在防什么的测试，下次重构会被当噪音删掉）。
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moments.impulse import THRESHOLD, PostImpulse, Signals, impulse  # noqa: E402


def test_a_single_drive_never_reaches_the_threshold():
    """🔴 整套测试里最重要的一条：它挡的是 `if drive == "longing": post`。

    线上实测最强的一条 drive 是 longing 0.40（2026-09-14 /api/nox/resonance），
    阈值 0.45 就是照着它挑的 —— 单独一条，**哪怕时机三项全部给满**，
    也只能算出 0.40，发不出去。

    能挡：`max(...)` 那种取最大的实现、把阈值降到 0.40 以下、
          以及任何「有 longing 就发」的规则化分支。
    不能挡：两条 drive 加起来该不该够（那是第 2 条），
          也不能挡时机被整个丢掉 —— 这一条的时机是满分，
          `value = inner` 那种错法在这里照样是绿的，第 3 条才管那个。
    """
    signals = Signals(
        drives={"longing": 0.40},
        minutes_since_contact=999,      # 她很久没说话了
        turns_today=0,                  # 今天一句都没聊
        minutes_since_last_post=None,   # 从来没发过
    )

    result = impulse(signals)

    assert result.value == 0.40
    assert result.wants_to_post() is False


def test_a_second_worry_pressing_together_is_enough():
    """第 1 条的配套：单条不够，**再压上一件就够** —— 这就是叠加语义。

    0.40 和 0.15 都够不着 0.45，两条一起压着才够（1-(1-0.4)(1-0.15) ≈ 0.49）。
    「多件事一起压着更重」是 resonance 那一层想表达、而 `upsert` 取较大值
    表达不了的东西（见 `attention/resonance.py` 开头那张表）。

    能挡：`max(...)` 取最大的实现（还是 0.40，过不了阈值）、求和
          （0.55 而不是 0.49），以及「必须某一条自己过线才发」的实现。
    不能挡：公式的其余部分 —— 时机那一半（第 3 条）、以及真实那组
          drives 该算出什么（第 5 条钉 0.62）。
    """
    signals = Signals(
        drives={"longing": 0.40, "playfulness": 0.15},
        minutes_since_contact=999,
        turns_today=0,
        minutes_since_last_post=None,
    )

    result = impulse(signals)

    assert result.wants_to_post() is True
    assert result.inner == pytest.approx(0.49)


def test_strong_inner_life_at_a_bad_moment_does_not_post():
    """内心再重，时机不对也不发 —— 乘法里任何一边接近 0，积就是 0。

    刚聊完（她 1 分钟前才说过话）、今天已经来回 30 轮（远过 QUIET_TURNS=12）、
    10 分钟前刚发过 —— 三个分量全趴着。这时候内心 0.98 也没用：
    那不是「想发一条」，那是「有话想对她说」，该走开口那条路，不是这里。

    能挡：`value = inner`（只看内心、把时机整个丢掉）—— 那样这一条给出
          0.98 ≥ 0.45；也挡三个时机分量里任意一个没被夹住
          （比如让 turns_today=30 反算出负的 quiet）。
    不能挡：三个分量的**权重**合不合理（这里只要求它们同时压到底时
          结果接近 0），也不能挡单个分量被漏掉 —— 那要靠第 6 条的单调性。
    """
    signals = Signals(
        drives={"longing": 0.9, "playfulness": 0.8},   # inner ≈ 0.98
        minutes_since_contact=1,
        turns_today=30,
        minutes_since_last_post=10,
    )

    result = impulse(signals)

    assert result.wants_to_post() is False
    assert result.value < 0.05


def test_perfect_timing_with_an_empty_inner_life_does_not_post():
    """第 1 条的镜像：时机给满、心里没事，照样不发。

    这一条挡的是另一半规则化实现 —— `value = timing`（只要她久没说话、
    今天没人聊，就发一条）。那样等于一个定时器，不是冲动。

    而且它顺带管 `why`：`drives` 空的时候主导那段必须写「心里没事」，
    不能拼出一个空的 / 悬着的主语（shadow 阶段读日志的人不知道发生了什么）。

    能挡：`value = timing`、空 drives 时 why 写不全。
    不能挡：timing 自己的三个分量 —— 这里只看它们是否被算进 value。
    """
    signals = Signals(
        drives={},
        minutes_since_contact=999,
        turns_today=0,
        minutes_since_last_post=None,
    )

    result = impulse(signals)

    assert result.inner == 0.0
    assert result.value == 0.0
    assert result.wants_to_post() is False
    assert "心里没事" in result.why


def test_the_real_2026_09_14_snapshot_posts_with_the_expected_number():
    """线上实测那组值真的会发 —— 而且**数字要对**（0.62，不是「大于 0」）。

    drives 是 2026-09-14 `/api/nox/resonance` 的真实读数
    （longing 0.4 / playfulness 0.28 / regret 0.167），互动量也是那天的形状：
    今天只来回 1 轮、她 4 小时没说话、还没发过帖。

    这里断言**具体数字**是故意的：「大于 0」「能过阈值」这种断言对
    叠加语义不敏感 —— 求和能过、取最大过不了但改动一点点又能过，
    只有钉住 0.62，才对「inner 用哪套叠加」「timing 怎么平均」有约束。

    能挡：求和（0.847 × 0.972 = 0.82）、取最大（0.40 × 0.972 = 0.39）、
          以及 timing 三分量里任何一个写反。
    不能挡：别的 drives 组合 —— 它钉的就是这一组具体的数。
    """
    signals = Signals(
        drives={"longing": 0.4, "playfulness": 0.28, "regret": 0.167},
        turns_today=1,
        minutes_since_contact=240,
        minutes_since_last_post=None,
    )

    result = impulse(signals)

    assert result.wants_to_post() is True
    assert round(result.value, 2) == 0.62


def test_every_signal_is_monotone_and_value_stays_a_probability():
    """四个信号各自往「更该发」的方向推一档，value 不许下降；全组合里 value 恒是概率。

    前半段（单调性）钉**方向**：内心更重、今天聊得更少、她更久没说话、
    更久没发过帖 —— 每一个都只能让 value 变大或不变，绝不能变小。

    后半段（穷举）钉**值域**：三个互动信号的全组合（5×5×5 = 125 组）
    value 必须落在 [0,1]。组合数少于 100 直接 fail —— 「找到 0 组却打印
    全部通过」是这项目踩过的第一种假验证（CAELUM-MAP 第三·五节）。

    能挡：分量写成反的（turns_today 越大越该发、contact_gap 拿 1-x）、
          没夹住导致溢出 1、或者负数漏出来。
    不能挡：分量被**整个漏掉**。`value = inner` 那种错法在这里照样全绿
          （少一个分量，value 对那个信号就变成常数，依然「不下降」）——
          那种要靠第 3 条（时机三项同时压到底）。
    """
    base = {
        "drives": {"longing": 0.3, "playfulness": 0.2},
        "minutes_since_contact": 60,
        "turns_today": 6,
        "minutes_since_last_post": 60,
    }
    #: 每一档都只动一个信号，方向是「更该发」
    more_wanted = [
        ("drives", {"drives": {"longing": 0.5, "playfulness": 0.2}}),
        ("turns_today", {"turns_today": 3}),                    # 聊得更少
        ("minutes_since_contact", {"minutes_since_contact": 180}),   # 更久没说话
        ("minutes_since_last_post", {"minutes_since_last_post": 360}),  # 更久没发过
    ]
    before = impulse(Signals(**base)).value
    for name, delta in more_wanted:
        after = impulse(Signals(**{**base, **delta})).value
        assert after >= before, (
            f"{name} 往该发的方向推了一档，value 反而降了：{after} < {before}")

    grid = list(itertools.product(
        (0, 3, 6, 12, 30),              # turns_today
        (None, 0, 60, 180, 600),        # minutes_since_contact
        (0, 60, 180, 360, None),        # minutes_since_last_post
    ))
    assert len(grid) >= 100, f"组合只有 {len(grid)} 组 —— 空集不是通过"
    for turns, contact, last_post in grid:
        value = impulse(Signals(
            drives={"longing": 0.4, "playfulness": 0.28, "regret": 0.167},
            turns_today=turns,
            minutes_since_contact=contact,
            minutes_since_last_post=last_post,
        )).value
        assert 0.0 <= value <= 1.0, (
            f"turns={turns} contact={contact} last_post={last_post} 算出 {value}")


def test_why_always_carries_all_three_pieces():
    """`why` 必须同时给出：主导的 drive、内心与时机两个数、过没过阈值。

    shadow 阶段这一版**唯一的产出就是日志**。少任何一段，几天后回头看
    都会变成黑洞（`temporal/result.py` 开头那段原话，同一个教训）：
    只写「想发一条」看不出是内心重还是时机到了；不写阈值就没法判断
    当时该不该发；不写主导的 drive 就不知道是哪件事在压着他。

    能挡：why 只拼一句人话（「心里有点事」那种）、漏掉阈值、
          或者写成英文/只给数字。
    不能挡：措辞好不好读 —— 那只能人看。也**不钉**主导 drive 怎么称呼
          （「想她」还是 `longing` 只是可读性，不是判据）。
    """
    signals = Signals(
        drives={"longing": 0.4, "playfulness": 0.28},
        turns_today=2,
        minutes_since_contact=60,
        minutes_since_last_post=60,
    )

    why = impulse(signals).why

    assert "内心" in why
    assert "时机" in why
    assert "阈值" in why
    assert f"{THRESHOLD}" in why


def test_a_post_impulse_without_a_reason_cannot_be_constructed():
    """`why` 空的 / 全空白的 PostImpulse **构造不出来**（不是「运行时注意一下」）。

    照抄 `temporal/result.py` 的做法 —— `__post_init__` 拦。
    靠自觉的话，迟早有人为了图省事传一个空字符串（或者 `" "`），
    而 shadow 阶段少了「为什么」这个模块就是个黑洞。

    能挡：空串、全空白串。**在构造那一刻**就炸，不会等到几天后
          翻日志才发现那批记录什么都没留下。
    不能挡：why 写了但是废话（「想发」）—— 那靠第 7 条的那三个词
          加人的眼睛，机器验不了。
    """
    with pytest.raises(ValueError):
        PostImpulse(value=0.5, inner=0.5, timing=1.0, why="", parts={})

    with pytest.raises(ValueError):
        PostImpulse(value=0.5, inner=0.5, timing=1.0, why="   ", parts={})


def test_impulse_is_a_pure_function():
    """同一组信号连算 10 次，`value` / `why` / `parts` 必须完全相等；
    而且源码里**没有时钟、没有 IO、没有随机、没有网络**。

    这一层得经得住穷举（第 6 条就是靠这个才写得出来），也得让 shadow
    的日志能事后复算：同一个 Signals 今天算和明天算必须是同一个数。
    时钟一进来，昨天那条日志就再也复不出来了。

    钉的五样东西各有各的意思：
        random        随机属于 T5 的 loop（「随机发生在有理由的内在状态上」，
                      不是随机抽时间）—— 掷骰子不该在这里
        datetime.now  读时钟 = 同一输入不同结果，日志不可复算
        requests/http 网络 = 一个「算冲动」的函数不该有副作用
        open(         读文件同理（连缓存都不该在这一层）

    能挡：将来往这个函数里塞时钟 / IO / 随机 —— 加一行就红。
    不能挡：跨进程、跨机器的确定性（这里只验「同一次运行里重算一致」），
          也不能挡间接依赖 —— 它读的是这个文件的**文本**，不是调用图。
    """
    signals = Signals(
        drives={"longing": 0.4, "playfulness": 0.28},
        turns_today=3,
        minutes_since_contact=90,
        minutes_since_last_post=45,
    )

    first = impulse(signals)
    for _ in range(9):
        again = impulse(signals)
        assert again.value == first.value
        assert again.why == first.why
        assert again.parts == first.parts

    source = (Path(__file__).resolve().parents[1] / "moments" / "impulse.py").read_text(
        encoding="utf-8")
    for banned in ("random", "requests", "datetime.now", "open(", "http"):
        assert banned not in source, f"impulse.py 里不该出现 {banned!r}"
