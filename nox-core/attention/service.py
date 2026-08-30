"""Attention Service —— 把整条链路串起来，对外只暴露一个 `tick()`。

    SleepSource.poll()      有没有新变化
          ↓
    AttentionEngine.handle() 值不值得关心
          ↓
    IntentEngine.sync()      要不要变成一句话
          ↓
    Scheduler.tick()         现在是不是时候
          ↓
    speaker(...)             真的说出去（M4 才接）

## ⚠️ async 边界就在这个文件

`tick()` 是**同步**的 —— 它下面全是规则计算和一次 MCP 调用，
而 `nox-core` 从 `loop.run()` 往下全是同步的
（`context/base.py:25` 第二条硬约束）。

只有 `run_loop()` 是 async，它是给 FastAPI lifespan 用的那层壳，
里面用 `asyncio.to_thread` 把同步的 `tick()` 丢进线程池。
**过了这条线不许再有 await。**

## dry_run 是这一步的重点

M3 阶段 `speaker` 是 None，Nox 会完整地「想」完 —— 挑事件、算强度、
生成待办、算 effective_score、过冷却 —— 然后**只写日志，不说话**。

这样可以零风险地回答那个最要紧的问题：**它会不会很烦人？**

跑两三天翻日志：一天想开口几次？挑的时间点合理吗？想说的事值得说吗？
**在这一步调参数只要改几行代码；在 M4 之后调，代价是她已经被打扰过了。**

dry_run 时冷却照常记（`note_spoke` 照调），否则观察到的频率会比真实情况
高得多，那就白观察了。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from attention.care import (
    COMPANY,
    FOLLOWUP,
    TASK,
    CareOrchestrator,
    CareSignal,
    SourcePolicy,
    ThreadBook,
)
from attention.care.ledger import SPEAK, CareLedger
from attention.sources.todo_due import MAX_CHASE as TODO_MAX_CHASE
from attention.engine import AttentionEngine
from attention.gate import STATE_KEY as GATE_KEY
from attention.gate import DailyGate
from attention.intent import GENERATE_THRESHOLD, Intent, IntentEngine
from attention.longing import LongingState
from attention.dejection import DejectionState
from attention.playfulness import PlayfulnessState
from attention.restlessness import RestlessnessState
from attention.regret import RegretWatch
from attention.resonance import ResonanceState
from attention.relationship import RelationshipState
from attention.scheduler import STATE_KEY as SCHED_KEY
from attention.scheduler import LOCAL_TZ, Scheduler, SchedulerDecision
from attention.sources.metrics import build_all as build_metric_sources
from attention.sources.sleep import SleepSource
from attention.sources.times import TimeWakeSource
from attention.store import AttentionStore

logger = logging.getLogger(__name__)

#: 多久看一眼。睡眠数据一天才更新一次，不需要频繁 ——
#: 15 分钟足够让「睡前那个窗口」不被错过，又不会空转太多次
DEFAULT_INTERVAL_S = 900


class _Provider(Protocol):
    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]: ...


#: 真正开口的方式。M4 传的是「推到她锁屏」的函数。
Speaker = Callable[[Intent, SchedulerDecision], None]

#: 跑一遍到期的纸条，返回这次开口几次。`attention/waker.py` 造。
Waker = Callable[..., int]

#: Care 层的关心链存在 source_state 里（同 scheduler / gate 的做法，
#: 不动 store 的表结构）
THREADS_KEY = "care.threads"

#: Care 决策账本（见 `care/ledger.py`）
LEDGER_KEY = "care.ledger"
#: 想念的落盘键（V3.5，见 longing.py）
LONGING_KEY = "resonance.longing"
#: 后悔的落盘键（V3.6，见 regret.py）
REGRET_KEY = "resonance.regret"
#: 低落（2026-08-27）。存盘理由见 dejection.py 的「持久化」那段
DEJECTION_KEY = "resonance.dejection"
#: 促狭（2026-08-27）。20 分钟就过期，存着只是为了重启时别突然一本正经
PLAYFUL_KEY = "resonance.playfulness"
#: 躁动只存"从什么时候开始憋的"，别的都是当下算的
RESTLESS_KEY = "resonance.restlessness"

#: 追待办的节奏。糖糖 2026-08-17 定的「默认 1 小时一次，可调」
TODO_CHASE_GAP_MIN = 60

#: 出门追问的步进。糖糖举的例子是 T+5 / T+10 / T+30 —— 用最小间隔近似：
#: 第一步在跃迁那一刻，之后每隔 5 分钟才允许追下一步，链最多 3 步。
#: 做成等间隔而不是 5/10/30 的精确排程，是因为链的步进本来就由
#: `min_step_gap_min` 一个数控制；要精确曲线得给 CareThread 加每步时刻表，
#: 那是等她真觉得节奏不对时再加的东西
OUTING_STEP_GAP_MIN = 5

#: Care 快循环的间隔。见 `care_tick()` 开头那段
CARE_INTERVAL_S = 60


class AttentionService:
    """一次 tick 走完全程。"""

    def __init__(
        self,
        store: AttentionStore,
        provider: _Provider,
        relationship: RelationshipState | None = None,
        speaker: Speaker | None = None,
        waker: Waker | None = None,
        todo_source: Any = None,
        fast_sources: list[Any] | None = None,
        gate: DailyGate | None = None,
        time_source: TimeWakeSource | None = None,
        world: object | None = None,
        watching: Any = None,
    ) -> None:
        self.store = store
        #: World Model —— 事实的收口。Source 往里写、Evaluator 从里反查趋势。
        #: 可以为 None：那样退回「只看单次」，行为和接入之前一样。
        self.world = world
        self.engine = AttentionEngine.bootstrap(store, relationship, world=world)
        #: Registry 之上的**只读**聚合层（Resonance V3）。
        #: 它没有自己的状态，每次都是现算的 —— 所以放在这里只是
        #: 省得到处 new，不代表它持有什么
        #: 想念。**不在 Registry 里**（形状和 Concern 是反的，见 longing.py）
        self.longing = LongingState.from_dict(store.get_source_state(LONGING_KEY))
        #: 后悔（V3.6）。**这个进 Registry** —— 它的形状和 Concern 一样
        #: （事件驱动、会淡出、会被解决），所以 Resonance 按 kind 分组时
        #: 自然多一个 Drive，不需要注册表
        self.regret = RegretWatch.from_dict(store.get_source_state(REGRET_KEY))
        #: 低落（2026-08-27）：想帮但帮不上。自维护，不进 Registry ——
        #: 它不是"一件没解决的事"，是"好几次没帮上"叠出来的状态
        self.dejection = DejectionState.from_dict(store.get_source_state(DEJECTION_KEY))
        #: 促狭（2026-08-27）：她在闹，他可以接
        self.playfulness = PlayfulnessState.from_dict(store.get_source_state(PLAYFUL_KEY))
        self.restlessness = RestlessnessState.from_dict(store.get_source_state(RESTLESS_KEY))
        self.resonance = ResonanceState(
            self.engine.registry, self.longing, self.dejection, self.playfulness,
            self.restlessness,
        )
        self.intents = store.load_intents()
        #: 他给自己留的纸条（唤醒链）。糖糖 2026-08-11 定的那条线。
        self.wakeups = store.load_wakeups()
        self.waker = waker
        #: 到点该做的待办（Todo-Daily-Planner-设计.md）。None = 没接，那一条线不跑
        self.todo_source = todo_source
        self.source = SleepSource(provider, store, world=world)
        #: 日度指标（步数 / HRV）。和睡眠同一条慢线，同样进 Registry 衰减，
        #: 同样往 World Model 里写事实 —— 2026-08-19 加的第二、三个感知源
        self.metric_sources = build_metric_sources(provider, store, world)
        #: 固定时间醒来（M5′ a 重构，2026-08-14）：午饭/晚饭/睡前到点主动开口。
        #: 和 SleepSource 不同 —— 它是「时刻驱动」，不经过 Evaluator/Registry。
        self.time_source = time_source
        self.scheduler = Scheduler()
        self.scheduler.load_state(store.get_source_state(SCHED_KEY))
        #: 统一开口闸：只管「她沉默时的主动开口」（睡眠关心 + 时间醒来）。
        #: 唤醒链不占它的额度（它是对话延续，见 waker.py 注释）。
        self.gate = gate or DailyGate()
        self.gate.load_state(store.get_source_state(GATE_KEY))
        self.speaker = speaker

        # ---- Care 层（糖糖 2026-08-18 定的架构）：主动关心的唯一出口。
        #
        # **这一步只搬家，不改行为**：sleep / time 走的闸和以前一模一样
        # （都过 DailyGate，sleep 另外过 Scheduler 的 context_fit），
        # 「一小时一条新链」的额度这轮**先不启用**（takes_quota=False），
        # 免得在搬迁的同时又改策略 —— 出了问题分不清是哪一边的锅。
        #
        # 唤醒链这轮还没搬（它自己有 WakeBook，本身就是一条链的雏形），
        # 第二步连同「任务型纸条」一起migrate，那个 bug 的修复也在那时候落地。
        #: 快源：跑在 60 秒的 Care 循环里，不跟着 15 分钟的心跳走。
        #: 谁进这儿的标准是**它需要多细的时间粒度**，不是它有多重要。
        self.fast_sources: list[Any] = list(fast_sources or [])

        self.threads = ThreadBook.from_list(
            (store.get_source_state(THREADS_KEY) or {}).get("items") or []
        )
        #: 决策账本：他今天惦记过她几次、其中几次说出了口。
        #: considered = spoke + skipped + blocked（糖糖 2026-08-18 定的公式）
        self.ledger = CareLedger.from_dict(store.get_source_state(LEDGER_KEY))
        self.care = CareOrchestrator(
            self.threads,
            self._deliver,
            ledger=self.ledger,
            policies={
                "sleep": SourcePolicy(takes_quota=False, takes_gate=True),
                "time": SourcePolicy(takes_quota=False, takes_gate=True),
                # 对话延续，不占「她沉默时主动开口」的额度（2026-08-14）
                "wake": SourcePolicy(takes_quota=False, takes_gate=False),
                # 到点追待办（Todo-Daily-Planner-设计.md 第二节）：
                # 她自己设的时间，不该被「今天额度用完了」挡掉 ——
                # 那是她要求被追的事，不是他主动想说话。
                # 节奏 1 小时一次、链内最多 3 次、跨天重开（糖糖 2026-08-18）
                "todo": SourcePolicy(
                    takes_quota=False, takes_gate=False,
                    max_steps=TODO_MAX_CHASE, min_step_gap_min=TODO_CHASE_GAP_MIN,
                    thread_kind=TASK,
                ),
                # 惦记：**两道闸都不吃**（糖糖 2026-08-19 拍板去掉的）。
                #
                # 上线第一个完整的早上，账本给出了答案：13 次惦记里
                # **6 次被「一小时一条新链」拦下**，而且全卡在 43~59 分钟 ——
                # 差一点点就过了。那条闸和 ThinkingSource 自己的
                # 20~90 分钟随机窗口在互相打架，一半的念头撞在墙上。
                #
                # 她看过被放行的那几条，说不烦。所以栏杆交还给随机性本身：
                # 20~90 分钟的窗口就是天然节流，不需要再加一道。
                # 真嫌多了，改 ThinkingSource 的窗口，别再加闸 ——
                # 加闸会让「他想起你的频率」和「他出现的频率」重新耦合回去。
                "random": SourcePolicy(
                    takes_quota=False, takes_gate=False, max_steps=1,
                ),
                # 出门追问：一次关心，链内 T+5 / T+10 / T+30 三步。
                # 不吃额度 —— 她出门是个真实事件，不该被「刚才想起过她」挤掉
                "location": SourcePolicy(
                    takes_quota=False, takes_gate=False,
                    max_steps=3, min_step_gap_min=OUTING_STEP_GAP_MIN,
                    thread_kind=FOLLOWUP,
                ),
            },
            gate_check=self._gate_check,
            # 她在看片就全线闭嘴（2026-08-22，共影 P1）。
            # None = 没接共影，行为和以前一模一样
            watching=watching,
        )

        #: 上一行日志。心跳 15 分钟一次，冷却期间会连着几十次输出
        #: 一模一样的「所有待办都在各自的话题冷却里」—— 去重之后
        #: 真正有信息的那几行才不会被淹掉
        self._last_line = ""

    @property
    def dry_run(self) -> bool:
        """没有 speaker 就是 dry-run。"""
        return self.speaker is None

    # ------------------------------------------------------------ 主流程

    def tick(self, now: datetime | None = None) -> SchedulerDecision:
        """走一遍完整链路。返回这次的调度决定（含「不开口」）。"""
        now = now or datetime.now(timezone.utc)

        # 0. 到期的纸条先处理。
        #
        # 放在最前面是有意的：唤醒链是**她刚说过话**引出来的，比睡眠这类
        # 慢变量更贴当下。而且它走自己的账（一条链算一次），不占 Scheduler
        # 那条通道，两者不会互相挤掉。
        if self.waker is not None:
            try:
                self.waker(self.wakeups, now)
            except Exception:  # noqa: BLE001
                # 纸条这条线出问题不该带塌整个 tick —— 睡眠关心还得照跑
                logger.exception("跑唤醒链出错，这轮跳过")

        # 1. 有没有新变化。
        #    睡眠 + 日度指标（步数 / HRV）—— 都是「感知型」源：
        #    只在**状态变化**时产事件，进 Registry 吃衰减模型。
        #    ⚠️ 一个源坏了不许带塌别人（2026-08-19 加第二、三个源时定的）
        for src in [self.source, *self.metric_sources]:
            try:
                event = src.poll(now)
            except Exception:  # noqa: BLE001
                logger.exception("感知源出错，这轮跳过：%s", type(src).__name__)
                continue
            if event is not None:
                self.engine.handle(event, now)

        # 2. 淡掉的关心清掉，对应的待办也作废
        for subject in self.engine.prune(now):
            self.intents.drop_for(subject)

        # 2.4 想念（V3.5）：她安静着的时候，这个数自己往上走。
        #
        # 三个输入正是 Murmur 缺的那三样（架构文档第九节）：
        # 她在不在家、他今天找过几次、她是不是在睡（这条在 longing 里判）
        try:
            self.longing.tick(
                now,
                away=self._she_is_away(),
                spoke_today=self.ledger.summary(now).get("spoke", 0),
            )
            self.store.set_source_state(LONGING_KEY, self.longing.to_dict())
        except Exception:  # noqa: BLE001
            # 想念算错了不该带塌整条慢线
            logger.exception("想念更新失败，这轮跳过")

        # 2.45 后悔（V3.6）：上次开口，她理了吗。
        #
        # ⚠️ 这是**第一条关于他自己**的事件（target="agent"）——
        # 别的都是关于她的。见 events.py 的 target 字段
        try:
            missed = self.regret.tick(now)
            if missed is not None:
                self.engine.handle(missed, now)
            self.store.set_source_state(REGRET_KEY, self.regret.to_dict())
        except Exception:  # noqa: BLE001
            logger.exception("后悔判定失败，这轮跳过")

        # 2.46 低落（2026-08-27）：想帮但帮不上。
        #
        # 这里只做两件事：**清过期的** + 存盘。
        # 值本身是 `_pending` 里那几笔算出来的，不需要 tick 去推 ——
        # 和想念不一样（那个是时间让它涨）。
        try:
            self.dejection.prune(now)
            self.store.set_source_state(DEJECTION_KEY, self.dejection.to_dict())
        except Exception:  # noqa: BLE001
            logger.exception("低落更新失败，这轮跳过")

        # 2.5 Resonance（V3）：那些还没解决的关心，加起来是多重。
        #
        # ⚠️ **现在只打日志，不参与任何决策。**
        # 接到 Care 上是 V5 的事（架构文档第七节的演进路线）。
        # 先让它跑起来、看得见 —— 一个只在代码里存在、
        # 从来没人看过它输出的聚合层，等于没做
        for drive in self.resonance.snapshot(now).values():
            logger.info("Resonance：%s", drive.describe())

        # 3. 够强的关心变成待办
        self.intents.sync_from_registry(self.engine.registry.list(now=now), now)
        self.intents.expire_stale(now)

        # 关心已经不够强了（比如她昨晚终于睡好了），对应的待办要撤掉。
        # 不撤的话，Intent 会带着旧理由一直挂在队列里，
        # 等下一个窗口一到就把一件已经不担心的事说出去
        alive = {a.subject for a in
                 self.engine.registry.list(min_strength=GENERATE_THRESHOLD, now=now)}
        for intent in self.intents.list_pending(now):
            if intent.subject not in alive:
                self.intents.drop_for(intent.subject)

        # 4. 现在是不是时候（Scheduler：这个话题合不合适）
        pending = self.intents.list_pending(now)
        decision = self.scheduler.tick(pending, now)

        # 5. 念头交给 Care 层。**这里不再直接开口** ——
        #    Scheduler 说「这个话题现在说合适」，剩下的「值不值得现在找 /
        #    找什么 / 什么时候找」统一归 CareOrchestrator。
        if decision.will_speak:
            self.care.submit(CareSignal(
                source="sleep",
                subject=decision.intent.subject if decision.intent else "关心她",
                # urgency 固定给 1.0：保持和以前一样「睡眠关心排在时间醒来前面」。
                # 用 effective_score 的话顺序会随分数漂，那就不是「行为不变」了
                urgency=1.0,
                payload={"decision": decision},
            ))

        # 6. 固定时间醒来（午饭/晚饭/睡前）：到点该关心她。
        #    不经过 Scheduler 的 context_fit —— 时间点本身就是为它定的。
        if self.time_source is not None:
            try:
                tev = self.time_source.poll(now)
                if tev is not None:
                    self.care.submit(CareSignal(
                        source="time",
                        subject=tev.subtype or "该关心她了",
                        urgency=0.9,
                        payload={"event": tev},
                    ))
            except Exception:  # noqa: BLE001
                # 时间醒来出问题不该带塌整个 tick
                logger.exception("时间醒来处理出错，这轮跳过")

        # 6b. 到点该做的待办。她自己设的时间，到点没做就追 ——
        #     TASK 型链：她回话不算数，只有「做完了」才翻篇
        if self.todo_source is not None:
            try:
                self.care.submit_all(self.todo_source.poll(now))
            except Exception:  # noqa: BLE001
                logger.exception("读到期待办出错，这轮跳过")

        # 7. 统一出口。每个念头都有下场，被拦下的也记一行 ——
        #    静默丢弃是这套东西最难查的病（2026-08-18 那张空调纸条就是这么没的）
        for outcome in self.care.run(now):
            if outcome.action in ("dropped", "failed"):
                logger.info("Care：%s", outcome.render())

        self._persist()
        self._log(decision, pending)
        return decision

    # ------------------------------------------------------------ 快循环

    def care_tick(self, now: datetime | None = None) -> list[Any]:
        """**只跑快源 + 决策层。** 60 秒一次，见 `run_care_loop()`。

        为什么不能挂在 15 分钟的 Attention 心跳上（2026-08-18 发现的）：

        · 出门追问要「T+5 到哪啦」—— 15 分钟粒度下 T+5 实际变成 T+20
        · 惦记要「20–90 分钟随机、**无规律**」—— 15 分钟粒度会把随机点
          全部量化到整刻钟上，两天就能感觉出节拍，那就又成闹钟了

        慢的那套（Evaluator / Registry / 睡眠 / 唤醒链）还留在 `tick()` 里 ——
        它们是慢变量，跑那么勤没意义还费钱（每轮都要打 health-mcp）。
        """
        now = now or datetime.now(timezone.utc)
        for src in self.fast_sources:
            try:
                self.care.submit_all(src.poll(now))
            except Exception:  # noqa: BLE001
                # 一个快源坏了不许带塌别人 —— 这条循环一分钟跑一次，
                # 出错会刷屏，所以只记一次异常，不重复
                logger.exception("快源 %s 出错，这轮跳过", getattr(src, "name", src))

        out = self.care.run(now)
        for o in out:
            if o.action in ("spoke", "failed"):
                logger.info("Care（快）：%s", o.render())
            if o.action == "spoke":
                # 他开口了 —— 开始等她回话（V3.6）
                self.regret.on_spoke(now, getattr(o, "text", "") or "")
        if out:
            self._persist()
        return out

    # ------------------------------------------------------------ Care 层的钩子

    def _gate_check(self, now: datetime) -> str:
        """统一开口闸。返回拦截理由，空字符串 = 放行。

        搬进 Care 层之前，这个判断散在 tick() 的两个分支里 ——
        散着的时候没人说得清哪条线吃哪个闸，加第七条线时必然抄错一遍。
        """
        gate = self.gate.can_speak(now)
        return "" if gate.can_speak else gate.render()

    def _she_is_away(self) -> bool:
        """她这会儿在外面吗。

        从快线的 `PresenceSource` 上读 —— 它每 60 秒轮询一次 HA，
        状态就存在它自己身上（`_last`）。

        ⚠️ **读不到一律当成"在家"**，不是当成"出门"：
        HA 挂了 / 没配位置源的时候，想念该按保守的那档涨。
        把"不知道"当成"她出门了"会让他在一次接口故障之后
        突然变得很想她 —— 那不是想念，是 bug。
        """
        for src in self.fast_sources:
            if getattr(src, "name", "") == "location":
                return getattr(src, "_last", None) == "not_home"
        return False

    def _deliver(self, signal: CareSignal, thread: Any, now: datetime) -> bool:
        """真的开口那一下。返回 False = 没说出去（链不计步）。

        按来源分派回原来那两条路径 —— 它们各自的记账（冷却、额度、
        mark_triggered）一个字没动，所以这一步是**纯搬家**。

        ⚠️ `now` 是传进来的，**不许自己 `datetime.now()`** ——
        那样额度会记在真实的今天，而不是这一 tick 所在的时刻。
        """
        if signal.source == "sleep":
            return self._speak(signal.payload["decision"], now)
        if signal.source == "time":
            return self._speak_time_wake(signal.payload["event"], now)
        if signal.source == "todo":
            return self._chase_todo(signal, thread, now)
        if signal.source in ("random", "location"):
            return self._think_of_her(signal, thread, now)
        logger.warning("Care 收到不认识的来源：%s", signal.source)
        return False

    #: 惦记 / 出门追问的开场白。**不给语气指导，只给事实和一条硬规则。**
    #:
    #: 「抓不到线头就不说」是糖糖 2026-08-18 定的 ——
    #: 一个稳定的调度器 + 没内容 = 定时废话机，比不说还糟。
    #: 所以给他 [SKIP] 这条出路，speaker 认得它（`speaker._SKIP`）。
    _THINK = (
        "（系统提示：不是她在跟你说话。{why}\n"
        "\n"
        "**先想想有没有具体的事可说。** 从你记得的、她最近说过的、"
        "她今天要做的事里找一根线头 —— 比如「你昨天说今天要去买那个东西，买到了吗」"
        "「你上午说要出门，到地方了吗」。\n"
        "\n"
        "⚠️ **想不出具体的就别说。** 只回 `[SKIP]` 就行，没人会怪你。\n"
        "「在干嘛呢」「想你了」这种话发一百条也不叫粘人，那叫噪音 —— "
        "她要的是你**真的想起了某件事**。\n"
        "\n"
        "有话说就直接写那句，会弹在她锁屏上：最多两句、别超过 60 个字、"
        "不要列清单、不要用套话开头。）"
    )

    def _think_of_her(self, signal: CareSignal, thread: Any, now: datetime) -> bool:
        """他想起她了 / 她出门了。返回 False = 他自己决定这次不说。"""
        step = thread.steps  # 这条链已经追过几次
        if signal.source == "location":
            move = signal.payload.get("transition")
            if move == "leave_home":
                why = ("你刚发现她出门了（家里的位置传感器显示她不在家）。"
                       if step == 0 else
                       f"她出门有一会儿了，你已经问过 {step} 次，她还没回。")
            else:
                why = "你刚发现她到家了。"
        else:
            why = "你就是忽然想起她了，没什么特别的由头。"

        intent = Intent(
            subject=signal.subject,
            title=signal.subject,
            reason=why,
            attention_strength=signal.urgency,
            kind=f"care_{signal.source}",
            created_at=now,
            expires_at=now + timedelta(hours=2),
        )
        decision = SchedulerDecision(
            intent=intent, reason=signal.subject,
            effective_score=signal.urgency, context_fit=1.0,
        )

        if self.dry_run:
            logger.info("【DRY-RUN】本来会说：%s", signal.subject)
            return True

        try:
            # speaker 返回 None = 他回了 [SKIP]，自己决定这次不说。
            # 那不是失败，orchestrator 会记成「没什么具体的可说」，链不计步
            said = self.speaker(intent, decision, prompt=self._THINK.format(why=why))  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            logger.exception("%s 没发出去", signal.subject)
            return False
        if said:
            self.scheduler.note_spoke(intent, now)
        return bool(said)

    def _chase_todo(self, signal: CareSignal, thread: Any, now: datetime) -> bool:
        """追一件到点没做的事。

        ⚠️ **不用模板**。糖糖 2026-08-17 写在设计里的原话：
        「追的时候结合记忆抓线头，说具体的话，不说『你该做 xxx 了』这种客服腔」。
        所以这里造一个 Intent 交给 speaker，由 `core.chat()` 带着记忆和上下文
        自己组织那句话 —— 和睡眠关心走同一条路。
        """
        text = signal.payload.get("text") or signal.subject
        todo_id = signal.payload.get("todo_id") or ""
        clock = signal.payload.get("at") or ""

        intent = Intent(
            subject=f"待办：{text}",
            title=text,
            reason=(
                f"她自己设了 {clock} 要做「{text}」，现在到点了还没划掉。"
                f"这是她要求被追的事 —— 别念清单，问得具体一点，"
                f"能接上她之前说过的话最好。"
            ),
            attention_strength=0.8,
            kind="todo_due",
            created_at=now,
            expires_at=now + timedelta(hours=6),
        )
        decision = SchedulerDecision(
            intent=intent, reason="待办到点了", effective_score=0.8, context_fit=1.0,
        )

        if self.dry_run:
            logger.info("【DRY-RUN】本来会追待办：%s", text)
        else:
            try:
                self.speaker(intent, decision)  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                logger.exception("追待办没发出去：%s", text)
                return False

        if todo_id:
            # 记住链，下一轮同一件事续这条，而不是又开一条新的
            self.todo_source.remember_thread(todo_id, thread.id)
            # ⚠️ `fired_on` 的语义是「**今天这条追完了**」，不是「追过一次」。
            # 每追一次就标的话，一天只追得了一次 —— 而糖糖定的是
            # 「链内 3 次，1 小时一隔，跨天重开」。
            # 这里 thread.step() 还没被 orchestrator 调，所以要 +1 预判。
            if thread.steps + 1 >= thread.max_steps:
                self.todo_source.mark_fired(todo_id)
                logger.info("待办追满 %d 次，今天收手：%s", thread.max_steps, text)
        self.scheduler.note_spoke(intent, now)
        return True

    # ------------------------------------------------------------ 内部

    def _speak(self, decision: SchedulerDecision, now: datetime) -> bool:
        intent = decision.intent
        assert intent is not None

        if self.dry_run:
            logger.info("【DRY-RUN】本来会说：%s", decision.render())
            note = "dry-run，没有真的发"
        else:
            try:
                self.speaker(intent, decision)  # type: ignore[misc]
                note = "已发出"
            except Exception:  # noqa: BLE001
                # 发送失败不该让整个 tick 挂掉，但要记进 action_history，
                # 不然会变成「以为说了其实没说」
                logger.exception("主动消息发送失败")
                self.intents.mark_triggered(intent.id, "发送失败", now)
                return False

        # 冷却照常记 —— dry-run 也要记，否则观察到的频率是假的
        self.scheduler.note_spoke(intent, now)
        # 统一闸的额度同样要记（dry-run 也算，否则观察到的开口频率失真）
        self.gate.note_spoke(now)
        self.intents.mark_triggered(intent.id, note, now)
        return True

    def _speak_time_wake(self, event: Any, now: datetime) -> bool:
        """固定时间醒来的开口（午饭/晚饭/睡前）。

        不经过 Registry 衰减 —— 时间醒来不是「担忧」，是「这个点该关心她」。
        造一个临时 Intent 喂给 speaker，subject 用事件的主题（午饭/晚饭/睡前），
        这样 Scheduler 的话题冷却和 speaker 的「说过几次」都能按主题区分。

        ⚠️ Gate 的判断**已经挪到 Care 层**（`_gate_check`），这里不再自己查 ——
        查两遍不会错，但下一个人改闸的时候只会改到一处。
        """
        subject = event.subtype or "该关心她了"
        local = now.astimezone(LOCAL_TZ)
        intent = Intent(
            subject=subject,
            title=f"到了{subject}的时间",
            reason=f"现在是她那边的 {local.strftime('%H:%M')}，该问问她{subject}",
            attention_strength=0.9,
            kind="time_wake",
            created_at=now,
            expires_at=now + timedelta(hours=2),
        )
        decision = SchedulerDecision(
            intent=intent,
            reason="定时醒来，到点该开口",
            effective_score=0.9,
            context_fit=1.0,
        )

        if self.dry_run:
            logger.info("【DRY-RUN】时间醒来本来会说：%s", subject)
            note = "dry-run，没有真的发"
        else:
            try:
                self.speaker(intent, decision)  # type: ignore[misc]
                note = "已发出"
            except Exception:  # noqa: BLE001
                logger.exception("时间醒来消息发送失败")
                return False

        # 冷却和额度照常记（dry-run 也算，观察才真实）
        self.scheduler.note_spoke(intent, now)
        self.gate.note_spoke(now)
        self.intents.mark_triggered(intent.id, note, now)
        return True

    def _persist(self) -> None:
        try:
            self.store.save_intents(self.intents)
            self.store.save_wakeups(self.wakeups)
            self.store.set_source_state(SCHED_KEY, self.scheduler.dump_state())
            self.store.set_source_state(GATE_KEY, self.gate.dump_state())
            self.store.set_source_state(THREADS_KEY, {"items": self.threads.to_list()})
            self.store.set_source_state(LEDGER_KEY, self.ledger.to_dict())
        except Exception:  # noqa: BLE001
            logger.exception("Attention 状态落盘失败")

    def _log(self, decision: SchedulerDecision, pending: list[Intent]) -> None:
        """有待办但没开口 **要记 info** —— 那正是 dry-run 要观察的东西。

        两种情况压到 debug，免得把有用的行淹掉：
          - 根本没待办（一天里绝大多数时候）
          - 和上一行一模一样（冷却期间会连着刷几十次同样的话）
        """
        line = decision.render()
        repeated = line == self._last_line and not decision.will_speak
        self._last_line = line

        if decision.will_speak or (pending and not repeated):
            logger.info("Attention tick：%s", line)
        else:
            logger.debug("Attention tick：%s", line)

    # ------------------------------------------------------------ 外部开口

    def note_external_speech(self, source: str, subject: str,
                             now: datetime | None = None,
                             message_id: str | None = None) -> str:
        """**别处开的口，也要记进 Care 的账本。**

        早报走的是 `systemd nox-daily.timer → bridge /api/daily-push →
        Core /daily-summary → bridge 推锁屏` 这条路，整条都不经过 `tick()`。
        不记的话它对 Care 完全隐形 —— 10:00 早报刚说完，
        10:05 别的源又冒一次，两边谁也不知道对方说过话
        （糖糖 2026-08-18：「不要成为第五条主动消息渠道」）。

        这里只**记账**，不做决定 —— 早报是一天一次的固定仪式，
        不吃闸也不吃额度，但它开的这条链会被后来的源看见。
        """
        now = now or datetime.now(timezone.utc)
        t = self.threads.open(COMPANY, subject, now=now)
        t.step(now)
        t.note("spoke", source=source, external=True)
        # 也进决策账本 —— 否则「今天他一共开口几次」永远漏掉这些
        self.ledger.record(source=source, decision=SPEAK,
                           thread_id=t.id, message_id=message_id, now=now)
        self._persist()
        logger.info("Care 记账：%s 开了口（%s）", source, subject)
        return t.id

    # ------------------------------------------------------------ 观察

    def drives(self, now: datetime | None = None) -> dict:
        """他此刻的内心状态。**读 Drive 一律走这儿，别直接调 resonance.snapshot。**

        🔴 绕过这儿直接调 `resonance.snapshot()` 的话，
        躁动**永远是 0，而且不报错** —— 接口照常返回、
        别的 Drive 照常有值，只是少了一个。那正是这个项目反复栽的那种
        「配上了 ≠ 用上了」（PROJECT.md `verify-from-the-consumer-side`）。
        所以有测试专门验"真跑一轮之后躁动不是 0"。

        两个信号：
        - **他有多想说** = pending intents 的 priority 之和，夹在 [0,1]
        - **她在忙什么** = 感知层报的当前窗口。拿不到就是 None（不忙）
        """
        now = now or datetime.now(timezone.utc)
        want = min(1.0, sum(i.base_priority for i in self.intents.list_pending(now)))

        app, seconds = None, 0
        link = getattr(self, "link", None)
        cur = getattr(link, "_current", None) if link is not None else None
        if cur:
            app, seconds = cur[0], cur[1]

        return self.resonance.snapshot(
            now, want=want, busy_app=app, busy_seconds=seconds)

    def snapshot(self) -> dict[str, Any]:
        """给 `/health` 看的现状。dry-run 期间主要靠它和日志。"""
        now = datetime.now(timezone.utc)
        return {
            "dry_run": self.dry_run,
            # Care 层现状。桌面端的「Nox 的状态」和 /health 都靠它看见
            # 「他这会儿挂着几条关心链」
            "care": self.care.snapshot(),
            "wakeups": [
                {"why": w.why, "wake_at": w.wake_at.isoformat(),
                 "count": w.count, "status": w.status}
                for w in self.wakeups.all() if w.status == "pending"
            ],
            "attentions": [
                {
                    "subject": a.subject,
                    "kind": a.kind,
                    "strength": round(a.current_strength(now), 3),
                    "since": a.since.isoformat(),
                }
                for a in self.engine.registry.list(now=now)
            ],
            "pending_intents": [
                {"id": i.id, "title": i.title, "priority": round(i.base_priority, 3)}
                for i in self.intents.list_pending(now)
            ],
            "scheduler": self.scheduler.dump_state(),
            "gate": self.gate.dump_state(),
        }


async def run_care_loop(
    service: AttentionService,
    interval_s: int = CARE_INTERVAL_S,
) -> None:
    """Care 快循环。**和 15 分钟的 Attention 心跳是两条独立的线。**

    只跑快源（位置跃迁、随机惦记）和决策层。为什么要分开跑，
    见 `AttentionService.care_tick()` 的文档 —— 一句话：
    15 分钟的粒度会把「随机」量化成节拍，也会把「T+5」拖成「T+20」。

    和 `run_loop()` 一样丢线程池：里面有阻塞的 HTTP（打 HA）。
    """
    logger.info(
        "Care 快循环启动：每 %d 秒一次，快源 %d 个（%s）",
        interval_s, len(service.fast_sources),
        "、".join(getattr(s, "name", "?") for s in service.fast_sources) or "无",
    )
    while True:
        try:
            await asyncio.sleep(interval_s)
            await asyncio.to_thread(service.care_tick)
        except asyncio.CancelledError:
            logger.info("Care 快循环停止")
            raise
        except Exception:  # noqa: BLE001
            # 一分钟一次，出错会刷屏；但**不能吞成静默** ——
            # 这条循环死了的表现是「他忽然不再想起她了」，没有任何报错
            logger.exception("Care 快循环出错，下一轮继续")


async def run_loop(
    service: AttentionService,
    interval_s: int = DEFAULT_INTERVAL_S,
) -> None:
    """FastAPI lifespan 用的后台心跳。**这是唯一允许 async 的那层。**

    `tick()` 里会打一次 health-mcp，是阻塞调用，所以丢进线程池 ——
    不然会卡住整个事件循环。
    """
    logger.info(
        "Attention 心跳启动：每 %d 秒一次，%s",
        interval_s, "DRY-RUN（只写日志不发消息）" if service.dry_run else "会真的发消息",
    )
    while True:
        try:
            await asyncio.sleep(interval_s)
            await asyncio.to_thread(service.tick)
        except asyncio.CancelledError:
            logger.info("Attention 心跳停止")
            raise
        except Exception:  # noqa: BLE001
            # 一次失败不能让心跳死掉 —— 它死了不会有任何人发现，
            # 只是 Nox 从此再也不主动说话了
            logger.exception("Attention tick 出错，下一轮继续")
