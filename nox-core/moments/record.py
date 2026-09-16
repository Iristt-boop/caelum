"""MomentRecord —— 一次发帖决策的完整记录，以及 shadow 的出口。

规格见 `Caelum-Moments-设计.md` 第七节「线上验证：两步走」。

## 🔴 shadow 必须有出口，否则它不是实验，是黑洞

糖糖 2026-09-14 的原话。理解层跑了一周多 shadow，回头看被丢掉的那些记录，
**日志里只有一个数字，模型推断了什么一个字都没留** ——
那一周的观察给不出任何结论。

Moments 的 shadow 要看的是**「他一天想发几条、为什么」**。
所以一条记录**必须三段齐全**：

    ① 输入是什么         drives 快照 + 互动量 + 距上次发帖   —— 不记这个没法复算
    ② 算出来什么         冲动值 + 内心/时机两半 + 当时的阈值 + 骰子
                                                          —— 认错和算错要分得开
    ③ 最后发没发、为什么   posted + reason + why_not_posted  —— 少了这段就是黑洞

③ 最容易被省掉，而它恰恰是那个信息。只记①②的话，几天后看到一堆漂亮的
冲动值，仍然不知道**真发出去会发生什么**。

## 做成「缺一段就构造不出来」

照抄 `temporal/result.py`：`posted=False` 时 `reason` 必须在白名单里、
`why_not_posted` 必填（且不许是全空白），`__post_init__` 拦。
靠自觉写日志的话，迟早有人为了图省事传个空字符串。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from moments.impulse import PostImpulse, Signals

#: 🔴 没发的原因钉成白名单（照抄 `temporal/result.py` 的 `TODO_MATCH_STATUSES`）。
#:
#: 为什么不能只留一句自由文本：几天后翻 200 条记录，要能**数**出
#: 「多少次是没到阈值、多少次是骰子没中、多少次是发满了」——
#: 自由文本数不出来，而且措辞一改统计就全错（判据不能挑文本）。
#: 自由文本那份照样留（`why_not_posted`），它是给人读的。
NOT_POSTED_REASONS = frozenset({
    "below_threshold",  # 冲动没到阈值 —— 绝大多数 tick 都该是这个
    "dice",             # 过了阈值但骰子没中。**随机是设计的一部分，不是失败**
    "daily_cap",        # 今天发满了（MAX_POSTS_PER_DAY）
    "min_gap",          # 距上次发帖不到 MIN_GAP
    "shadow",           # shadow 模式：算了、也想发，但故意不落帖
    "write_failed",     # 生成或落库失败（这条要能和上面几条分开数）
})

#: 两种会构造记录的模式。`off` 不在里面 —— 关掉的时候连算都不算，
#: 不该有记录。硬塞一条 mode="off" 的记录进来就是在骗审计
MODES = frozenset({"shadow", "on"})


@dataclass(frozen=True)
class MomentRecord:
    """一次发帖判断的三段式。"""

    #: ① 这一 tick 的时刻（UTC）
    at: datetime
    #: 必须在 `MODES` 里
    mode: str
    #: ① 输入快照
    signals: Signals
    #: ② 算出来什么（value/inner/timing/why/parts）
    impulse: PostImpulse
    #: ② 🔴 当时的阈值也要记 —— 常量会被调，
    #: 记录不自带的话过去那些行就再也解释不了了
    threshold: float
    #: ② 掷出来的数。None = 没掷（没过阈值就不掷）
    dice: float | None
    #: ② 当时的发帖概率。None = 没掷
    dice_p: float | None
    #: ③ 最后发没发
    posted: bool
    #: ③ 发了的话是哪条（bridge 返回的 id）
    post_id: str | None
    #: ③ 结构化原因；`posted=True` 时必须是空串
    reason: str
    #: ③ 给人读的一句中文；`posted=False` 时必填
    why_not_posted: str

    def __post_init__(self) -> None:
        #: ① 挡「模式写成自由字符串」：`off` 混进来会骗审计
        #: （关掉的时候连算都不算，不该有记录），大小写各异的同样不行。
        if self.mode not in MODES:
            raise ValueError(f"表外的 mode：{self.mode!r}")
        #: ② 挡「没发的原因是一句自由文本」：那样几天后翻 200 条记录
        #: 数不出「多少次没到阈值 / 骰子没中 / 发满了」，措辞一改统计全错。
        if not self.posted and self.reason not in NOT_POSTED_REASONS:
            raise ValueError(f"表外的没发原因：{self.reason!r}")
        #: ③ 挡「黑洞」：没发却没留下一句人话。用 `.strip()` 而不是 `if not`，
        #: 因为图省事的人传的正是 `"   "` 这种空白串。
        if not self.posted and not self.why_not_posted.strip():
            raise ValueError(
                "没发就必须说明为什么 —— 少了这一段，shadow 就是个黑洞")
        #: ④ 🔴 **结构闸门**：shadow 只算冲动、只记日志、不落帖。
        #: 「shadow 里写了一条真帖」是这个功能最坏的一种坏 —— 她会在
        #: Moments 里看到一整批本该不存在于那里的帖子，而且事后分不清
        #: 哪些是实验产物。所以让它**构造不出来**，而不是靠 loop 里
        #: 记得写个 `if`。
        if self.mode == "shadow" and self.posted:
            raise ValueError(
                "shadow 模式不许落帖 —— shadow 只算冲动、只记日志。"
                "要在 shadow 里记一条真帖，说明循环那里写错了分支")
        #: ⑤ 拦「发了却不知道是哪条」和「发了还带着没发的原因」。
        #: 前半截：`post_id` 是 bridge 落库返回的 id，也是这条记录和真实
        #: 那条帖之间唯一的缝 —— 空着就是审计断链，几天后点不回那条帖。
        #: 后半截：`posted=True` 还留着 `reason="dice"` 会让
        #: 「多少次骰子没中」的统计把这些真发出去的也算进去。
        if self.posted:
            if self.reason:
                raise ValueError(
                    f"发了就不该带着没发的原因：{self.reason!r}")
            if not self.post_id:
                raise ValueError("发了就必须记下是哪条（bridge 返回的 post_id）")

    def to_dict(self) -> dict[str, Any]:
        """**扁平、可 JSON 序列化**的一份 —— 落库和日志都走它。

        `signals` / `impulse` 这两层在这里被摊平：三段各自的值各占一个键
        （输入 7 个 + 算了什么 5 个 + 发没发 4 个 + 阈值/骰子 3 个），
        否则几天后要复算时得先把嵌套拆开才看得懂。

        ⚠️ 键名是**固定**的：统计脚本按这些键取值，改名等于把那批历史
        记录读废。要加键就往回加，别改已有的。
        """
        return {
            "at": self.at.isoformat(),
            "mode": self.mode,
            "drives": dict(self.signals.drives),
            "turns_today": self.signals.turns_today,
            "minutes_since_contact": self.signals.minutes_since_contact,
            "minutes_since_last_post": self.signals.minutes_since_last_post,
            "posts_today": self.signals.posts_today,
            "value": self.impulse.value,
            "inner": self.impulse.inner,
            "timing": self.impulse.timing,
            "parts": self.impulse.parts,
            "why": self.impulse.why,
            "threshold": self.threshold,
            "dice": self.dice,
            "dice_p": self.dice_p,
            "posted": self.posted,
            "post_id": self.post_id,
            "reason": self.reason,
            "why_not_posted": self.why_not_posted,
        }

    # ------------------------------------------------------------ 出口

    def log(self, logger: logging.Logger) -> None:
        """shadow 的出口。**三段一行打完**，别分三条 —— 分开的话
        journalctl 里它们会被别的日志冲散，拼不回来。

        🔴 级别必须是 **INFO**：这个项目里 WARN 比 INFO 更容易被吞，
        而 DEBUG 线上根本不打（`docs/LOGGING.md` 第 4 条）。
        shadow 阶段唯一的产出就是这一行 —— 打不出来，整个 shadow 就白跑。
        """
        #: ③ 发没发、为什么。发了就报 id（审计要顺着它点回那条帖），
        #: 没发就报白名单里的那个原因（几天后要能按它**数**）**和那句人话**
        #: —— 只报枚举值就成了「有数字没上下文」，正是理解层上次栽的形状。
        outcome = (
            f"posted=True post_id={self.post_id}"
            if self.posted
            else f"posted=False reason={self.reason}｜{self.why_not_posted}"
        )
        #: 没掷（没过阈值就不掷）和骰子是 0 是两回事，别让 `None` 混进数字格式
        dice = "没掷" if self.dice is None else f"{self.dice:.2f}"
        dice_p = "没掷" if self.dice_p is None else f"{self.dice_p:.2f}"
        logger.info(
            "Moments｜%s｜mode=%s｜drives=%s｜value=%.2f inner=%.2f timing=%.2f"
            "｜threshold=%.2f dice=%s p=%s｜%s｜%s",
            self.at.isoformat(),
            self.mode,
            dict(self.signals.drives),
            self.impulse.value,
            self.impulse.inner,
            self.impulse.timing,
            self.threshold,
            dice,
            dice_p,
            outcome,
            self.impulse.why,
        )
