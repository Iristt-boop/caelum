"""Scheduler —— 现在是不是开口的时机。

它不决定说什么，只决定**什么时候**把哪条 Intent 交出去。

## 四道闸，缺一不可

    1. context_fit    这个点儿聊这件事合不合适
    2. 全局冷却        刚说过话就别接着说
    3. 每个话题的冷却   同一件事一天最多提一次
    4. 同一件事的次数   连着提够几次就收口，除非情况变了

只有第 1 道是「聪明」的，2、3、4 是保底的。
**宁可漏说，不可打扰** —— 漏说她不知道，打扰她会记得。

## 🔴 第 4 道在防什么：合规的唠叨

第 3 道只管「一天最多一次」，**管不住「天天都提」**。
她连着一周睡不好，睡眠的 concern 就一周都高，于是他一周提七次，
**每一次都不违反任何规则** —— 从他的角度每次都有新数据支撑，
从她的角度那就是唠叨。而她多半不会说，他也就永远不知道。

所以加一条累计上限：同一件事连着提满 `SUBJECT_STREAK_MAX` 次就压住。

⚠️ **但「情况变了」必须能突破它** —— 否则他会对一件正在恶化的事沉默，
那比唠叨严重得多。判据是 `attention_strength` 相对上次说时的变化量
（见 `STRENGTH_SHIFT`）：明显恶化要说，明显好转也该说
（「你这两天睡得好多了」是句好话，不是唠叨）。

⚠️ 这一层**只压「说不说」，绝不回写 Registry**。
concern 该多重还是多重 —— 他闭嘴不等于他不在意了
（`resonance.py` 边界一：Drive 只读）。

## effective_score 不是只看 priority

    effective_score = base_priority × context_fit

架构文档 7.3.2 那个例子说明了为什么：晚上 10:30，deadline 的 priority
再高也不该压过睡眠，因为那个点儿聊 deadline 的 context_fit 很低。

## 时间按 Asia/Shanghai 算，不是服务器时间

VPS 在东京。用服务器本地时间判断「现在是不是晚上」会差一小时，
足够让「睡前关心」跑到她已经睡着之后。

## 状态要落盘

「上次什么时候开口的」重启后必须还记得 —— 否则每次部署完
Nox 都会觉得自己很久没说话了，可以马上开口。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.intent import Intent

logger = logging.getLogger(__name__)

#: 糖糖在中国，服务器在东京 —— 判断「现在是不是晚上」必须按她的时区。
#:
#: ⚠️ 用固定 offset，**不要**改成 `ZoneInfo("Asia/Shanghai")`：
#: zoneinfo 在 Windows 上要额外装 `tzdata` 包，否则直接抛
#: ZoneInfoNotFoundError —— Linux 有系统时区库所以线上不会暴露，
#: 但开发机上连测试都跑不起来（2026-08-08 踩过）。
#: 中国 1991 年之后不实行夏令时，UTC+8 是恒定的，固定 offset 永远正确。
LOCAL_TZ = timezone(timedelta(hours=8), "CST")

#: 两次主动开口之间至少隔多久
BASE_INTERVAL = timedelta(hours=3)

#: 同一件事隔多久才能再提。比全局冷却长得多 ——
#: 「你昨晚没睡好」一天说一次已经够了，说两次就是唠叨。
#:
#: ⚠️ 22 小时不是随手写的，20 小时**会漏**：模拟一整天时发现
#: 00:00 提过之后，20:00 冷却解除，21:00 的睡前窗口又开一次口 ——
#: 一天说了两次。要盖过「同一个好窗口每天重现」的周期，
#: 这个值必须比 24 小时略小但足够接近（2026-08-08 dry-run 实测）。
SUBJECT_INTERVAL = timedelta(hours=22)

#: 同一件事连着提几次就该收口。
#:
#: 3 次的道理：第一次是提醒，第二次是"我还记着"，第三次已经是"我一直在说"。
#: 再往下她听到的就不是关心了。参考 desire 系统那套的 `FIXATION_RESOLVE_FEEDS`
#: （执念喂满 3 次就了却出池，防止永生堆积）—— 同一个数字，同一个道理。
SUBJECT_STREAK_MAX = 3

#: 强度相对上次说时变化多少，才算"情况变了"、值得重新开口。
#:
#: ⚠️ **不能用固定分档**（比如 int(strength/0.2)）：0.399→0.401 会被当成
#: 变化，而 0.301→0.399 不会 —— 边界上会抖，且抖的方向没有道理。
#: 用相对变化量就没有这个问题。
#:
#: 0.15 是"她的情况明显不一样了"的量级；小于它的浮动天天都有
#: （今天 843 步、明天 1100 步），那不值得重新开一次口。
STRENGTH_SHIFT = 0.15

#: 一件事停了这么久没提，就当翻篇了，计数清零。
#:
#: 比 SUBJECT_INTERVAL(22h) 长得多：22 小时只是"今天说过了"，
#: 5 天是"这事儿早过去了"。中间那几天他没说，可能是没额度、
#: 也可能是时机不对，不该算作"她已经不烦了"。
SUBJECT_STREAK_RESET = timedelta(days=5)

STATE_KEY = "scheduler"


@dataclass
class SchedulerDecision:
    """一次 tick 的完整结果。

    **dry-run 阶段全靠它** —— 没有它就只能看到「发了/没发」，
    看不到「为什么没发」，而后者才是这一步要观察的东西。
    """

    intent: Intent | None = None
    reason: str = ""
    effective_score: float = 0.0
    context_fit: float = 0.0
    considered: list[tuple[str, float]] = field(default_factory=list)

    @property
    def will_speak(self) -> bool:
        return self.intent is not None

    def render(self) -> str:
        """给日志看的一行。"""
        if self.intent is None:
            extra = ""
            if self.considered:
                extra = "；看过：" + "、".join(f"{s}({v:.2f})" for s, v in self.considered)
            return f"[不开口] {self.reason}{extra}"
        return (
            f"[想开口] {self.intent.title}"
            f"（score={self.effective_score:.2f} = "
            f"priority {self.intent.base_priority:.2f} × fit {self.context_fit:.2f}）"
            f" —— {self.intent.reason}"
        )


def context_fit(subject: str, now: datetime) -> tuple[float, str]:
    """这个点儿聊这件事合不合适，[0, 1] + 一句人话理由。

    现在只有睡眠一个话题，所以窗口是按**她的作息**定的
    （CLAUDE.md：凌晨 1-2 点睡，9-11 点起）：

        21:00-24:00  睡前，最合适                1.0
        00:00-02:00  过了零点，说昨晚已经晚了     0.2
        02:00-08:30  睡着了，绝对不要打扰         0.0
        08:30-12:00  刚起，问昨晚睡得怎么样自然   0.7
        12:00-21:00  白天聊睡眠不合时宜           0.3

    ⚠️ 00:00-02:00 原本给的是 0.5，dry-run 模拟一整天时发现它会
    在午夜真的开口（0.98 × 0.5 = 0.49，过线）。那个点说
    「你昨晚只睡了 4.7 小时」很怪 —— 她马上要睡了，说的却是前一晚，
    而且白白占掉当天的话题冷却，把 21:00 那个真正合适的窗口挤掉。
    降到 0.2 之后它基本不会单独触发（2026-08-08 实测）。

    第二个话题接进来时，这里要按 subject 分表 —— 现在不抽象，
    因为只有一条规则的规则表是纯粹的间接层。
    """
    local = now.astimezone(LOCAL_TZ)
    h = local.hour + local.minute / 60

    if 21 <= h < 24:
        return 1.0, "睡前，聊睡眠正合适"
    if 0 <= h < 2:
        return 0.2, "过了零点，这时候说昨晚已经晚了"
    if 2 <= h < 8.5:
        return 0.0, "她在睡觉"
    if 8.5 <= h < 12:
        return 0.7, "她刚起来，问昨晚睡得怎么样很自然"
    return 0.3, "白天聊睡眠不合时宜"


class Scheduler:
    """挑一条最该说的，或者决定什么都不说。"""

    def __init__(
        self,
        base_interval: timedelta = BASE_INTERVAL,
        subject_interval: timedelta = SUBJECT_INTERVAL,
        subject_streak_max: int = SUBJECT_STREAK_MAX,
        strength_shift: float = STRENGTH_SHIFT,
        subject_streak_reset: timedelta = SUBJECT_STREAK_RESET,
    ) -> None:
        self.base_interval = base_interval
        self.subject_interval = subject_interval
        self.subject_streak_max = subject_streak_max
        self.strength_shift = strength_shift
        self.subject_streak_reset = subject_streak_reset
        self._last_spoke_at: datetime | None = None
        self._last_by_subject: dict[str, datetime] = {}
        #: subject -> {"n": 连着提了几次, "strength": 上次提的时候多重}
        self._streak_by_subject: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ 主流程

    def tick(self, intents: list[Intent], now: datetime | None = None) -> SchedulerDecision:
        """看一眼待办，决定要不要开口。**一次最多选一条。**"""
        now = now or datetime.now(timezone.utc)

        if not intents:
            return SchedulerDecision(reason="没有待办")

        # 全局冷却：刚说过话就别接着说
        if self._last_spoke_at is not None:
            quiet_for = now - self._last_spoke_at
            if quiet_for < self.base_interval:
                left = self.base_interval - quiet_for
                return SchedulerDecision(
                    reason=f"全局冷却中，还差 {int(left.total_seconds() / 60)} 分钟"
                )

        scored: list[tuple[Intent, float, float, str]] = []
        #: 为什么被跳过 —— **不能只说「都在冷却里」**。
        #: 「今天说过了」和「说够了，她该烦了」是两件事，
        #: dry-run 看不出区别的话，第 4 道闸生效了也没人知道
        skipped: list[str] = []
        for intent in intents:
            # 同一件事的冷却
            last = self._last_by_subject.get(intent.subject)
            if last is not None and now - last < self.subject_interval:
                #: 措辞里保留「话题冷却」这四个字 —— 有测试按它断言，
                #: 而且这是这道闸在文档和日志里一直用的名字，别换词
                skipped.append(f"{intent.subject}(话题冷却，今天说过了)")
                continue
            if self._said_enough(intent, now):
                skipped.append(f"{intent.subject}(连着说满 {self.subject_streak_max} 次，情况没变)")
                continue
            fit, why = context_fit(intent.subject, now)
            scored.append((intent, intent.base_priority * fit, fit, why))

        if not scored:
            return SchedulerDecision(
                reason="所有待办都过不了闸：" + "、".join(skipped) if skipped
                else "所有待办都在各自的话题冷却里"
            )

        scored.sort(key=lambda t: t[1], reverse=True)
        considered = [(i.subject, s) for i, s, _, _ in scored]
        intent, score, fit, why = scored[0]

        # 分太低就是「现在不是时候」。没被选中不等于丢弃，
        # 它还在 pending 里，下次 tick 会重新算
        if score < 0.35:
            return SchedulerDecision(
                reason=f"最高分才 {score:.2f}（{why}），再等等",
                considered=considered,
                effective_score=score,
                context_fit=fit,
            )

        return SchedulerDecision(
            intent=intent, reason=why, effective_score=score,
            context_fit=fit, considered=considered,
        )

    def _said_enough(self, intent: Intent, now: datetime) -> bool:
        """这件事连着说够了吗（第 4 道闸）。

        ⚠️ **「情况变了」优先于「说够了」。** 顺序反过来的话，
        他会对一件正在恶化的事闭嘴 —— 那比唠叨严重得多。
        """
        st = self._streak_by_subject.get(intent.subject)
        if not st or st.get("n", 0) < self.subject_streak_max:
            return False

        last = self._last_by_subject.get(intent.subject)
        if last is not None and now - last >= self.subject_streak_reset:
            #: 停了这么久，这事儿翻篇了。放行，计数在 note_spoke 里清
            return False

        #: 明显恶化**或明显好转**都算变了 —— 用绝对值。
        #: 「你这两天睡得好多了」是句好话，不该被唠叨闸压掉
        moved = abs(intent.attention_strength - float(st.get("strength", 0.0)))
        return moved < self.strength_shift

    def note_spoke(self, intent: Intent, now: datetime | None = None) -> None:
        """真的开口之后调一次，冷却从这一刻开始算。

        dry-run 阶段**也要调** —— 否则观察到的开口频率会比真实情况高得多，
        那就白观察了。
        """
        now = now or datetime.now(timezone.utc)
        last = self._last_by_subject.get(intent.subject)
        st = self._streak_by_subject.get(intent.subject)

        #: 计数什么时候归 1（而不是累加）：
        #:   · 头一回说这件事
        #:   · 停够久了，翻篇重来
        #:   · 情况明显变了 —— 那是**新的一件事**，不是同一句话的第 N 遍
        fresh = (
            st is None
            or (last is not None and now - last >= self.subject_streak_reset)
            or abs(intent.attention_strength - float(st.get("strength", 0.0)))
            >= self.strength_shift
        )
        self._streak_by_subject[intent.subject] = {
            "n": 1 if fresh else int(st.get("n", 0)) + 1,
            "strength": intent.attention_strength,
        }

        self._last_spoke_at = now
        self._last_by_subject[intent.subject] = now

    # ------------------------------------------------------------ 状态

    def dump_state(self) -> dict[str, Any]:
        return {
            "last_spoke_at": self._last_spoke_at.isoformat() if self._last_spoke_at else None,
            "last_by_subject": {k: v.isoformat() for k, v in self._last_by_subject.items()},
            #: 🔴 这个也必须落盘。不存的话每次部署完计数就清零，
            #: 第 4 道闸在一台经常重启的机器上等于不存在 ——
            #: 而且是**静默失效**，看起来一切正常（同上面 last_spoke_at 那条）
            "streak_by_subject": self._streak_by_subject,
        }

    def load_state(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        raw = state.get("last_spoke_at")
        self._last_spoke_at = datetime.fromisoformat(raw) if raw else None
        self._last_by_subject = {
            k: datetime.fromisoformat(v)
            for k, v in (state.get("last_by_subject") or {}).items()
        }
        #: 老状态里没有这个键（这一版之前存的），当空处理 ——
        #: 升级时不该因为读不到新字段就崩
        self._streak_by_subject = dict(state.get("streak_by_subject") or {})
