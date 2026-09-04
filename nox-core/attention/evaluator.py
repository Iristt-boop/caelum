"""Attention Evaluator —— 这个事件该不该关心、有多关心。

## 为什么必须有这一层

没有它，每个 Source 都会自己决定「我是 concern 还是 goal」、自己定强度。
那样一来「睡眠为什么算 concern 不算 goal」这种问题散落在十个文件里，
而且两个 Source 对同一件事给出矛盾的判断时，没有地方能仲裁。

**Source 只负责说「发生了什么」，不许碰 Registry。**
（架构设计第十五节列的风险之一。）

## 这一轮是纯规则

不上 LLM。上之前要先能说出「具体哪一类事件判错了」——
说不出来就说明规则还没到瓶颈，那时候上 LLM 只是花钱买不确定性。
（v1.3 第 14.3 节）

## 判断只在这里，不进 dynamic_system

`context/providers/health.py:25` 立过一条规矩：Provider 只摆数字，
不把「异常」两个字塞给模型 —— 因为那会污染他自己的观察。

这条规矩这里**依然有效**：Evaluator 的判断是 Nox 的**内部状态**
（决定要不要开口），不会作为结论写进给模型看的上下文。
模型最终怎么说，还是他看着原始数字自己决定。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from attention.appraisal import SUBJECT, Appraiser, RuleAppraiser
from attention import regret as regret_mod
from attention.sources import curiosity as curiosity_mod
from attention.events import ExperienceEvent
from attention.registry import AttentionRegistry
from attention.relationship import RelationshipState

logger = logging.getLogger(__name__)

#: 睡眠事件的严重度 → 基础强度。
#: 这是「普通人遇到这种情况该有多担心」，还没算进关系。
_SLEEP_BASE = {
    "short": 0.55,      # 比她自己的基线明显少
    "very_short": 0.75,  # 少得多
}

#: 关系加成的上限。care_weight=1.0 时最多把基础强度抬到 1.3 倍。
#: 不让它无限放大 —— 否则 care_topic 里的事情永远压着其它所有事。
_MAX_BOOST = 1.3


@dataclass(frozen=True)
class AttentionDecision:
    """Evaluator 的输出。Engine 照着它去改 Registry。"""

    action: str          # "upsert" / "weaken" / "ignore"
    subject: str
    kind: str
    strength: float
    decay: str
    reason: str          # 人话，进日志。将来要能回答「他凭什么关心这个」
    summary: str         # 存进 evidence 的一句话
    #: action="weaken" 时用：削到原来的百分之多少
    factor: float = 1.0

    @property
    def should_apply(self) -> bool:
        return self.action != "ignore"


def _ignore(subject: str, reason: str) -> AttentionDecision:
    return AttentionDecision(
        action="ignore", subject=subject, kind="concern",
        strength=0.0, decay="slow", reason=reason, summary="",
    )


#: 日度指标的规则表（2026-08-19 加步数和 HRV 时才抽的）。
#:
#: ⚠️ 上一版这里有句注释：「**第二个 Source 接进来之前不要抽象成规则表** ——
#: 只有一条规则的规则引擎，是纯粹的间接层」。现在第二、第三个来了，
#: 而且三条的形状一模一样（等级 → 基础强度 → 关系加成 → 趋势加成），
#: 所以到点了。**再加第四个之前先看它像不像这个形状**，不像就别硬塞。
_METRIC_RULES = {
    # 事件类型 → (话题, avoid/care 用的关键词, {等级: 基础强度}, 恢复时的话)
    "sleep_quality_changed": (
        "糖糖的睡眠", "睡眠",
        {"short": 0.55, "very_short": 0.75},
        "她睡好了一晚，这份关心可以松一些",
    ),
    "activity_changed": (
        "糖糖的活动量", "运动",
        # 比睡眠低一档：一天没怎么走动，远没有睡不够那么要紧
        {"low": 0.40, "very_low": 0.60},
        "她今天动起来了，这份关心可以松一些",
    ),
    "recovery_changed": (
        "糖糖的状态", "身体",
        # HRV 走低是「累」的信号，不是病 —— 别渲染成大事
        {"low": 0.45, "very_low": 0.65},
        "她的状态缓回来了，这份关心可以松一些",
    ),
}

def _chat_concern_on() -> bool:
    """对话产生关心这条线开着吗（Resonance V2）。

    ⚠️ **默认开**，`NOX_CHAT_CONCERN=0` 单独关掉。

    为什么要单独一个开关：线上 `NOX_ATTENTION_LIVE=1`，他是真会开口的。
    这条线一上，她说一句「压力好大」就可能换来一次主动关心 ——
    万一太吵，她得能只关这一条，而不是把整个 `NOX_ATTENTION` 关掉
    （那会连睡眠关心一起停）。

    这是 `_build_attention` 里那条设计哲学的延续：
    **「彻底不跑」和「跑但不出声」是两件该分别控制的事。**

    关掉之后事件照样流动、日志照样打（V1 的管道不受影响），
    只是不产生 Concern —— 观察数据还在。
    """
    return os.getenv("NOX_CHAT_CONCERN", "1").lower() not in ("0", "false", "off")


#: 后悔的锚点。
#: ⚠️ 不能和那几条「糖糖的…」撞 —— 那些是关于她的，这条是关于他自己的
REGRET_SUBJECT = "他挑的说话时机"

#: 连续几天低于基线才算「趋势」。
#: 3 天：少于这个数很可能只是偶然熬一次夜，够不上「一直」。
_TREND_DAYS = 3
#: 趋势加成。连着几天不好，比单独一晚更该担心 —— 但也不能翻倍，
#: 否则一周没睡好就顶满，后面再糟也表达不出来了。
_TREND_BOOST = 1.25


class AttentionEvaluator:
    """规则版分类器。认识睡眠、活动量、状态，以及她说的话。"""

    def __init__(
        self,
        relationship: RelationshipState,
        world: object | None = None,
        appraiser: Appraiser | None = None,
    ) -> None:
        self.relationship = relationship
        #: World Model。**可以为 None** —— 没接上时退回单次判断，
        #: 行为和以前完全一样。趋势是增强，不是依赖。
        self.world = world
        #: 对话的 Appraisal 产生者（Resonance V2）。
        #: 换 LLM 版时只换这一个对象，下面 `_evaluate_conversation` 不用动
        self.appraiser = appraiser or RuleAppraiser()

    def evaluate(
        self,
        event: ExperienceEvent,
        registry: AttentionRegistry,
        now: datetime | None = None,
    ) -> AttentionDecision:
        """把一个事件变成一个决定。"""
        if event.source == "health" and event.type == "sleep_quality_changed":
            return self._evaluate_sleep(event, registry, now)
        if event.source == "health" and event.type in _METRIC_RULES:
            return self._evaluate_metric(event, now)
        if event.source == "chat" and event.type == "message":
            return self._evaluate_conversation(event)
        if event.source == regret_mod.SOURCE and event.type == regret_mod.TYPE:
            return self._evaluate_regret(event)
        if event.source == curiosity_mod.SOURCE and event.type == curiosity_mod.TYPE:
            return self._evaluate_curiosity(event)

        return _ignore(f"{event.source}.{event.type}", "没有对应的规则")

    # ------------------------------------------------------------ 他自己的事

    def _evaluate_regret(self, event: ExperienceEvent) -> AttentionDecision:
        """他开口了，她没理（Resonance V3.6）。

        ## 🔴 这是第一条 `target="agent"` 的规则

        上面那几条全是关于**她**的（她没睡好、她说难受），方向是凑过去。
        这一条是关于**他自己**的，方向是收回来。
        `events.py` 里 `target` 字段那段注释解释了为什么要分。

        ## 强度故意压在开口阈值之下

        `GENERATE_THRESHOLD` 是 0.55，这里给 0.35 —— **够不着**。

        因为他后悔的正是"上次打扰了她"，如果这份后悔又变成一次主动开口，
        那就荒唐了。这个 Drive 是用来让他**下次晚一点再说**的
        （V5 接 Care 时用），不是用来让他现在说点什么。
        """
        if event.target != "agent":
            #: 防呆：这类事件必须是关于他自己的。
            #: 哪天有人复制这段去处理别的事件，这行会拦住
            return _ignore(REGRET_SUBJECT, f"target={event.target!r}，不是他自己的事")

        waited = event.payload.get("waited_hours") or 0
        what = event.payload.get("what") or ""
        summary = (f"他说了「{what}」之后，{waited:g} 小时没等到回话"
                   if what else f"他开口后 {waited:g} 小时没等到回话")

        logger.info("Attention 决定：%s ← %s", REGRET_SUBJECT, summary)
        return AttentionDecision(
            action="upsert", subject=REGRET_SUBJECT, kind="regret",
            #: 压在 GENERATE_THRESHOLD(0.55) 之下，够不着开口
            strength=0.35,
            #: normal（2 天）。够久到影响明后天的时机判断，
            #: 又不至于让他为上周的一次沉默一直缩着
            decay="normal",
            reason=summary,
            summary=summary,
        )

    def _evaluate_curiosity(self, event: ExperienceEvent) -> AttentionDecision:
        """池子里有条料勾住他了（2026-09-04）。

        ## 🔴 这是第一条和她无关的规则

        上面 regret 那条已经是 `target="agent"` 了，但它仍然是**关于她**的
        （他后悔打扰了她）。这一条是他自己的事，从头到尾跟她没关系 ——
        糖糖 2026-09-04：「不单单是因为我」。

        ## 强度压在开口阈值之下

        `GENERATE_THRESHOLD` 0.55，这里最高 0.45 —— **够不着**。
        他对一篇论文好奇不该变成一次主动开口，那会变成"他一好奇就凑上来"。
        真要聊走 `topic_pool/care.py` 的 `TopicSource`，那条有自己的额度。

        ## decay 用 fast

        好奇是**会过去的**：今天觉得有意思的东西，三天后多半不惦记了。
        睡眠用 slow（关心一个人的睡眠该慢慢淡），这个正相反。
        """
        if event.target != "agent":
            #: 防呆，同 regret 那条
            return _ignore("他好奇的东西", f"target={event.target!r}，不是他自己的事")

        title = (event.payload.get("title") or "").strip()
        hook = (event.payload.get("hook") or "").strip()
        what = title or hook
        if not what:
            #: 说不出"因为什么"就不要 —— resonance.py 边界三
            return _ignore("他好奇的东西", "这条料没有标题也没有钩子")

        relevance = float(event.payload.get("relevance") or 0.5)
        strength = max(
            curiosity_mod.MIN_STRENGTH,
            min(curiosity_mod.MAX_STRENGTH, relevance * curiosity_mod.MAX_STRENGTH),
        )
        summary = hook or title

        logger.info("Attention 决定：好奇 ← %s（%.2f）", what[:40], strength)
        return AttentionDecision(
            action="upsert",
            #: subject 用**具体那条东西**，不是一个笼统的"他好奇的东西" ——
            #: Registry 按 subject 去重，用笼统的会让所有料挤成一条，
            #: 而 Drive 的 `because` 正是从 subject 来的（那句话要能读）
            subject=what[:40],
            kind="curiosity",
            strength=strength,
            #: 好奇会过去。6 小时半衰 —— 隔天就淡得差不多了
            decay="fast",
            reason=summary,
            summary=summary,
        )

    # ------------------------------------------------------------ 她说的话

    def _evaluate_conversation(self, event: ExperienceEvent) -> AttentionDecision:
        """她说的一句话（Resonance V2）。

        ## 分工和其它规则一致

        Appraisal 只回答「这句话是什么性质、多重」，**关系加成留在这里算** ——
        和睡眠/活动量那几条走同一条路，不在 Appraisal 里各算各的。

        ## 🔴 为什么大多数话都该被忽略

        她一天说几十上百句，绝大多数不是"值得记挂的事"。
        `appraise()` 返回 None 是**常态**，不是失败。

        真正的风险不是漏掉一句，是**把普通的话读成心事** ——
        那样他会念叨一件她根本没说过的事，而她无从知道他为什么这么想。
        """
        if not _chat_concern_on():
            return _ignore(SUBJECT, "对话产生关心这条线关着（NOX_CHAT_CONCERN=0）")

        text = (event.payload.get("text") or "").strip()
        appraisal = self.appraiser.appraise(text)
        if appraisal is None:
            return _ignore(SUBJECT, "这句话没有需要记挂的信号")

        if self.relationship.is_avoided(appraisal.topic):
            return _ignore(
                appraisal.subject,
                f"{appraisal.topic} 在 avoid_topics 里，她明确说过不要问",
            )

        if appraisal.valence == "relief":
            # 她自己说缓过来了 —— 这是**现实给的答案**，比时间衰减更该算数。
            # 对齐睡眠 recovered 那条的教训：不 weaken 的话，slow 半衰期
            # 7 天，他会在她说"好多了"之后继续担心好几天
            return AttentionDecision(
                action="weaken", subject=appraisal.subject, kind="concern",
                strength=0.0, decay="slow", factor=0.4,
                reason=f"她说「{appraisal.cue}」，这份记挂可以松一些",
                summary="",
            )

        # 🔴 **她心情好 / 在跟他闹 —— 不是「担心」。**
        #
        # 2026-08-27 加 playful/warm 这两档时差点栽在这儿：下面那段
        # 无条件 `upsert ... kind="concern"`，于是「她说哈哈哈」
        # 会被记成一条**担心**，还会衰减、还会攒着、还可能触发他来关心。
        #
        # 那是彻底反的 —— 她笑了，他跟过来问「你还好吗」。
        #
        # 这两档的意义是**改变他回话的方式**（Resonance 那边读），
        # 不是往 Registry 里塞东西。所以在这里就地返回，不进 Registry。
        if appraisal.valence in ("playful", "warm"):
            return _ignore(
                appraisal.subject,
                f"她「{appraisal.cue}」—— 心情是好的，不用记挂",
            )

        weight = self.relationship.care_weight(appraisal.topic)
        strength = min(1.0, appraisal.intensity * (1.0 + (_MAX_BOOST - 1.0) * weight))

        # ⚠️ summary 用**她的原话**。Intent 的 reason 直接取最新 evidence，
        # 他开口时说的就是基于这句 —— 写成「她说她累」不如原样留着
        summary = f"她说：{appraisal.quote}"
        reason = (
            f"命中「{appraisal.cue}」，基础强度 {appraisal.intensity:.2f} "
            f"→ {strength:.2f}（{appraisal.topic} 权重 {weight:.1f}）"
        )

        logger.info("Attention 决定：%s ← %s", appraisal.subject, reason)
        return AttentionDecision(
            action="upsert", subject=appraisal.subject, kind="concern",
            #: 🔴 **normal（2 天半衰期），三档里唯一合适的那档。**
            #:
            #: 算过：0.62 的强度掉到开口阈值 0.55 需要
            #:   fast(6h)    1.0 小时  ← 她晚上说完就睡，第二天他已经忘了，等于白记
            #:   normal(2d)  8.3 小时  ← 晚上说的，第二天早上还惦记着
            #:   slow(7d)   29.0 小时  ← 一句话不是持续状态，惦记两天太久了
            #:
            #: 健康数据是连着几天的趋势，配 slow；一句话的证据强度弱得多，
            #: 但也不该像"一闪而过的念头"那样一小时就没
            strength=strength, decay="normal", reason=reason, summary=summary,
        )

    # ------------------------------------------------------------ 日度指标

    def _evaluate_metric(
        self,
        event: ExperienceEvent,
        now: datetime | None,
    ) -> AttentionDecision:
        """步数 / HRV。和睡眠同一个形状，只是没有 World Model 的趋势反查 ——
        那一段等这两个指标攒够历史再加（现在加了也数不出连续几天）。
        """
        subject, topic, base_map, recovered_line = _METRIC_RULES[event.type]

        if self.relationship.is_avoided(topic):
            return _ignore(subject, f"{topic} 在 avoid_topics 里，她明确说过不要问")

        severity = event.subtype or ""
        if severity == "recovered":
            # 同睡眠：关心的对象好转了，就该明显松一口气，而不是慢慢忘
            return AttentionDecision(
                action="weaken", subject=subject, kind="concern",
                strength=0.0, decay="slow", factor=0.4,
                reason=recovered_line, summary="",
            )

        base = base_map.get(severity)
        if base is None:
            return _ignore(subject, f"{severity!r} 不需要关心")

        weight = self.relationship.care_weight(topic)
        strength = min(1.0, base * (1.0 + (_MAX_BOOST - 1.0) * weight))

        value = event.payload.get("value")
        baseline = event.payload.get("baseline")
        unit = event.payload.get("unit", "")
        # ⚠️ summary 里带上基线 —— 「今天 900 步」单独看没有意义，
        # 「今天 900 步，她平时 2300」才是他该有的判断依据
        summary = f"今天 {value:g}{unit}，她平时 {baseline:g}{unit}"
        reason = (
            f"{summary}，{topic} 在 care_topics 里权重 {weight:.1f}，"
            f"基础强度 {base:.2f} → {strength:.2f}"
        )

        logger.info("Attention 决定：%s ← %s", subject, reason)
        return AttentionDecision(
            action="upsert", subject=subject, kind="concern",
            strength=strength, decay="slow", reason=reason, summary=summary,
        )

    # ------------------------------------------------------------ 睡眠

    def _evaluate_sleep(
        self,
        event: ExperienceEvent,
        registry: AttentionRegistry,
        now: datetime | None,
    ) -> AttentionDecision:
        subject = "糖糖的睡眠"

        if self.relationship.is_avoided("睡眠"):
            # 她说过「别老问我睡觉的事」。这条比什么阈值都优先。
            return _ignore(subject, "睡眠在 avoid_topics 里，她明确说过不要问")

        severity = event.subtype or ""

        if severity == "recovered":
            # ⚠️ 这里原本是 ignore，想着「让 Concern 自己按半衰期淡掉」。
            # dry-run 模拟三天时发现那是错的：slow 的半衰期是 7 天，
            # 睡好一晚只掉 9%，强度还有 0.73 —— 照样过 Intent 阈值，
            # Nox 会在她睡好的那天继续念叨前天的事（2026-08-08 实测）。
            #
            # 关心的对象好转了，关心本来就该明显松一口气，而不是慢慢忘。
            return AttentionDecision(
                action="weaken", subject=subject, kind="concern",
                strength=0.0, decay="slow", factor=0.4,
                reason="她睡好了一晚，这份关心可以松一些",
                summary="",
            )

        base = _SLEEP_BASE.get(severity)
        if base is None:
            return _ignore(subject, f"睡眠状态 {severity!r} 不需要关心")

        weight = self.relationship.care_weight("睡眠")
        strength = min(1.0, base * (1.0 + (_MAX_BOOST - 1.0) * weight))

        hours = (event.payload.get("sleep_min") or 0) / 60
        baseline_h = (event.payload.get("baseline_min") or 0) / 60

        # 反查 World Model：这是偶然一次，还是一直这样？
        # 没有 World Model 之前这里只能看单次 —— 「昨晚睡得少」和
        # 「这周一直在往下掉」在他眼里是同一件事（架构文档 10.1）
        streak = self._short_streak(baseline_h)
        if streak >= _TREND_DAYS:
            strength = min(1.0, strength * _TREND_BOOST)
            summary = f"已经连着 {streak} 天没睡够了，最近一晚 {hours:.1f} 小时"
            trend_note = f"，**连续 {streak} 天低于基线**（查了 World Model 的历史）"
        else:
            summary = f"{event.payload.get('sleep_date') or '最近一觉'}只睡了 {hours:.1f} 小时"
            trend_note = ""

        reason = (
            f"{summary}（她自己的基线是 {baseline_h:.1f} 小时）{trend_note}，"
            f"睡眠在 care_topics 里权重 {weight:.1f}，基础强度 {base:.2f} → {strength:.2f}"
        )

        logger.info("Attention 决定：%s ← %s", subject, reason)
        return AttentionDecision(
            action="upsert",
            subject=subject,
            kind="concern",
            strength=strength,
            # 关心一个人的睡眠该慢慢淡，不是睡好一晚就一笔勾销
            decay="slow",
            reason=reason,
            summary=summary,
        )

    def _short_streak(self, baseline_h: float) -> int:
        """从最近往回数，连续几天低于基线。查不到就返回 0。

        ⚠️ **只数连续的**，一天补回来就断。「上周有三天没睡好」和
        「连着三天没睡好」是两件事，后者才值得多担心一点。

        World Model 没接上、查询失败、数据不够 —— 一律返回 0 退回单次判断。
        趋势是增强，**不能因为它挂了就让他哑掉**。
        """
        if self.world is None or baseline_h <= 0:
            return 0
        try:
            rows = self.world.query("sleep_duration", days=14, limit=14)
        except Exception:  # noqa: BLE001
            logger.exception("反查睡眠历史失败，退回单次判断")
            return 0

        n = 0
        for ev in rows:            # query 返回的是新→旧
            v = (ev.raw or {}).get("value")
            if v is None or v >= baseline_h:
                break
            n += 1
        return n
