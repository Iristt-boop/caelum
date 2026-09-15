"""Moments 的冲动判断 —— 「他现在想不想发一条」。

规格见 `Caelum-Moments-设计.md` 第三节。这一层是**纯函数**：
给一组信号，出一个数加一句 why。

## 为什么这一层存在：Moments 接的是掉在阈值底下的那些

系统里**已经有一整层「够不着开口阈值」的内心状态**，每一条都是**故意**
压在 0.55（`GENERATE_THRESHOLD`）之下的，理由都一样：

    attention/playfulness.py:24        「她心情好不该换来一次开口」
    attention/regret.py:31             「故意压在开口阈值之下」
    attention/sources/curiosity.py:36  ≤0.45，「不该变成一次打扰」
    attention/appraisal.py:223         规则版强度全部压在 0.55 之下

线上实测（`/api/nox/resonance`，2026-09-14）最强的一条是 longing 0.40 ——
四条**全都够不着 0.55**。每一条被压低的理由都一样：不该变成一次打扰她。

所以这里算的**不是「要不要开口」，是「要不要留一条痕迹」**。
开口要过 0.55、要抢每日配额、要吵醒她；留一条痕迹不用。

## 🔴 不是规则机器人：防法是乘法

`if drive == "longing": post` 是这个模块最该防住的东西 —— 那等于
「她太久没说话，就一定发一条」，一个月后打开全是同一句话。

形状是**乘法**：内心有事 × 时机合适，两边都得有 ——

    inner    几件事一起压着有多重（`combine` 叠加，不是取最大）
    timing   此刻合不合适（今天聊得少 + 她多久没说话 + 距上次发帖多久）
    value    inner × timing —— **任何一边是 0，冲动就是 0**

`THRESHOLD` 挑在 0.45 也是为这件事：它比任何**单独一条** drive 都高，
所以只有一件心事时、哪怕时机满分也发不出去，**必须有第二件事同时压着**。
这正是 resonance 的叠加语义（多件事一起压着更重）想表达的东西。

## 这里不做什么

无 IO、无时钟、无随机、无模型 —— 输入输出都是值，所以能穷举测。
「掷骰子、今天发过几条、两帖隔多久」是 `loop.py`（T5）的事：
随机属于 loop，不属于这个模块。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: 过这个数才算「想发」。
#: 🔴 0.45 这个数是**挑出来的，不是拍的**：线上实测最强的 drive 是 longing 0.40
#: （2026-09-14 /api/nox/resonance）。也就是说**任何单独一条 drive，
#: 哪怕时机满分，都够不着这个阈值** —— 必须有第二件事同时压着。
#: 这正是 resonance 的叠加语义（多件事一起压着更重）想表达的东西，
#: 也是「不是规则机器人」在数值上的落实。
THRESHOLD = 0.45

#: 今天来回几轮算「聊够了」。超过这个数，quiet 就是 0
QUIET_TURNS = 12
#: 她多久没说话算「满」。3 小时
CONTACT_FULL_MIN = 180
#: 距上次发帖多久算「满」。6 小时（= MIN_GAP 的两倍）
POST_FULL_MIN = 360


def clamp01(x: float) -> float:
    """压回 [0,1]。**唯一一处夹取**，别在别处再写一遍 min/max。"""
    return min(1.0, max(0.0, x))


def combine(xs: Iterable[float]) -> float:
    """几条强度叠成一条：`1 - Π(1 - x)`。

    抄 resonance 的语义（`attention/resonance.py` 的 `_combine`）——
    「多件事一起压着更重」。三种算法的差别：

        求和    0.40 + 0.28 + 0.167 = 0.85 —— 溢出的方向错，三件小事能压过一件大事
        取最大  0.40 —— 那就是「有没有哪一条自己够重」，回到规则机器人
        这个    0.64 —— 每件事**各自都还压着**，多几件更重，但永远够不到 1

    空集 → 0.0（Π 空积 = 1，所以 1-1 = 0）——「心里没事」就是 0，不是 1。
    """
    remain = 1.0
    for x in xs:
        remain *= 1.0 - clamp01(x)
    return 1.0 - remain


#: drive 名 → 人话。**只影响 why 怎么读**，一个字节都不参与算分。
#:
#: 和 `context/providers/resonance.py` 的 `_WORDS` 是同一批词，
#: 但故意不 import 那边 —— 思考层不该伸手进上下文组装层拿一张词表。
#: 认不出来的 drive **原样打印**：以后加新 drive 不用回来改这里，
#: 也不会因为漏了一条就拼出半句话。
_WORDS = {
    "longing": "想她",
    "playfulness": "想逗她",
    "regret": "过意不去",
    "dejection": "提不起劲",
    "concern": "担心她",
    "curiosity": "被一件事勾着",
}


def _lead(drives: Mapping[str, float]) -> str:
    """压着的几件事里最重的那件，怎么说成人话。

    ⚠️ 这是**给人读**的那一段，不是判据。判据是 `combine` 出来的 inner ——
    所以空集必须写「心里没事」，而不是拼出一个没有主语的字符串。
    """
    if not drives:
        return "心里没事"
    name, intensity = max(drives.items(), key=lambda kv: kv[1])
    return f"{_WORDS.get(name, name)} {intensity:.2f} 领头"


def _why(drives: Mapping[str, float], *,
         inner: float, timing: float, value: float) -> str:
    """把「为什么是这一刻」拼成一句中文。**三段都得有**，缺一段就是黑洞：

      ① 主导的那件事（哪一条 drive 在压着，值是多少）
      ② 两半各自多少（内心 × 时机 = 冲动值）—— 没有它分不清是内心重还是时机到了
      ③ 跟 `THRESHOLD` 比出来的结论 —— 没有它没法判断当时该不该发

    形如：`想她 0.40 领头（内心 0.64 × 时机 0.97 = 0.62），过阈值 0.45`

    ⚠️ 这一段是给日志和审计读的，**不参与判分** —— 别拿它当判据。
    """
    verdict = "过阈值" if value >= THRESHOLD else "没到阈值"
    return (f"{_lead(drives)}"
            f"（内心 {inner:.2f} × 时机 {timing:.2f} = {value:.2f}）"
            f"，{verdict} {THRESHOLD}")


@dataclass(frozen=True)
class Signals:
    """算一次冲动要的**全部**输入。纯值：没有时间戳、没有句柄、没有回调。"""

    #: drive 名 → intensity，如 {"longing": 0.4, "playfulness": 0.28}
    drives: Mapping[str, float]
    #: 她最后一次说话到现在几分钟。**None = 还不知道**
    minutes_since_contact: int | None
    #: 今天两个人来回了几轮
    turns_today: int
    #: 距上次发帖几分钟。**None = 还没发过**
    minutes_since_last_post: int | None
    #: 只作为 why 的材料，**不参与算分**（一天几条的上限归 loop 管）。
    #: ⚠️ 当前 why 只拼规格列出的三段（主导 drive / 两半的值 / 过没过阈值），
    #: 没有印这个数 —— 要印是这里一行的事，T3 的记录和 T5 的上限会读它
    posts_today: int = 0


@dataclass(frozen=True)
class PostImpulse:
    """这一刻的冲动，以及它为什么是这个数。

    ⚠️ `wants_to_post()` 只回答「过没过阈值」。**发不发还得 loop 再掷一次骰子**
    （设计文档第三节：「随机不是随机抽时间，是随机发生在有理由的内在状态上」）。
    """

    #: 冲动值 [0,1]
    value: float
    #: 内心那一半
    inner: float
    #: 时机那一半
    timing: float
    #: 中文一句。见 `_why`
    why: str
    #: {"quiet":…, "contact_gap":…, "since_post":…} —— 复算用
    parts: dict[str, float]

    def __post_init__(self) -> None:
        #: 照抄 `temporal/result.py`：做成「缺一段就构造不出来」，不靠自觉。
        #: shadow 阶段记录里少了「为什么」，这个模块就是个黑洞 ——
        #: 几天后翻回来只有一堆数字，说不出他当时为什么想发。
        if not self.why.strip():
            raise ValueError(
                "发帖冲动必须说得出为什么 —— 少了这一段，shadow 就是个黑洞")

    def wants_to_post(self) -> bool:
        return self.value >= THRESHOLD


def impulse(signals: Signals) -> PostImpulse:
    """此刻的冲动。**纯函数**：同一个 Signals 算几次都是同一个结果。"""
    inner = combine(signals.drives.values())

    #: 今天聊够了没有。聊得越少越该发，超过 QUIET_TURNS 就是 0
    quiet = clamp01((QUIET_TURNS - signals.turns_today) / QUIET_TURNS)

    #: 她多久没说话了。**None → 0.0**：刚醒过来 / 还没拿到这个数的时候，
    #: 没有依据就不催 —— 不知道她多久没说话，不能自己假设她很久没理他
    contact_gap = (
        0.0 if signals.minutes_since_contact is None
        else clamp01(signals.minutes_since_contact / CONTACT_FULL_MIN)
    )

    #: 距上次发帖多久。**None（从来没发过）→ 1.0**，和上面正好相反：
    #: 一条都还没发过，那这一个分量就该是满的 ——「从来没发过」
    #: 是最该发的情形，不是最不该发的
    since_post = (
        1.0 if signals.minutes_since_last_post is None
        else clamp01(signals.minutes_since_last_post / POST_FULL_MIN)
    )

    #: 🔴 三个分量平均，**不是加权**：现在没有任何数据说哪一半更重要，
    #: 编一组权重等于把噪声写成设计。等 shadow 跑出数据再说
    timing = (quiet + contact_gap + since_post) / 3

    #: 🔴 乘法。内心有事 × 时机合适，**两边都得有**
    value = inner * timing
    return PostImpulse(
        value=value,
        inner=inner,
        timing=timing,
        why=_why(signals.drives, inner=inner, timing=timing, value=value),
        parts={"quiet": quiet, "contact_gap": contact_gap, "since_post": since_post},
    )
