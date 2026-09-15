"""Moments 的循环（T5）—— **这一 tick 发不发**：节奏、上限、间隔、随机。

规格见 `Caelum-Moments-设计.md` 第三节「节奏」和第七节「线上验证：两步走」。

## 这一层管什么、不管什么

    impulse.py   冲动值是多少（纯函数：内心 × 时机）
    record.py    一次判断怎么记（三段式，缺一段就构造不出来）
    writer.py    要说什么（utility 模型 → 正文 → bridge）
    loop.py      **此刻发不发** —— 闸门顺序、掷骰子、落盘、心跳

## 🔴 为什么「过了阈值」还不一定发：随机不是随机抽时间

过了阈值就发的话，每次 `MIN_GAP` 一到就立刻发一条，时间上**完全可预测** ——
那不是「随机发生在有理由的内在状态上」，是定时器（设计文档第三节）。

所以过了阈值之后**每一 tick 再掷一次骰子**，概率随冲动值线性升到 `P_MAX`：

    15 分钟一 tick、一天 96 tick，p_max=0.15 时冲动 0.62 大约 5 小时一条，
    刚好落在「一天 1~2 条」。

⚠️ **没过阈值连骰子都不掷**（`post_probability` 直接返回 0）—— 掷了的话
日志里会留下一堆「差点就发」，那是在给「随机抽时间」留后门。

## 🔴 闸门顺序就是 `reason` 的含义

    3 below_threshold  冲动没到阈值（绝大多数 tick 该是这个）
    4 daily_cap        今天发满了
    5 min_gap          距上一条还不到 MIN_GAP
    6 dice             骰子没中（过了阈值，这就是「随机」本身）
    7 shadow           算了、也想发，故意不落帖
    8 write_failed     生成或落库失败（要能和上面几条**分开数**）
    9 成功

顺序是**算得越便宜越靠前**：读状态、收信号、算冲动全是免费的，先算完再判断，
这样 shadow 记下来的分布才是「他一天想发几条」的真实分布，而不是
「今天还剩多少配额」的分布。

## 🔴 跨天只清 `count`，绝不清 `last_post_at`

清了的话，昨晚 23:50 发过一条、今天 00:05 还能再发一条 ——
`MIN_GAP` 在零点这一刻破一个洞，**而且不报错**。

## 边界（设计文档第一节）

  · **只读 drives**：走现成的 `attention.drives()`，绝不碰 Registry、
    绝不直接调 `resonance.snapshot`（那边三条边界）。
  · **R10：发帖不推送**。产出路径里不出现推送 / 通知的调用 ——
    发帖不是开口，不弹她的锁屏（字面量清单见 `scripts/check-boundaries.sh`）。
  · **R9：UTC+8 只在 `temporal` 里定义一次**，这里要本地日就 `to_local`。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from moments import writer
from moments.impulse import THRESHOLD, Signals, impulse
from moments.record import MODES, MomentRecord
from obs import heartbeat
from temporal import to_local

logger = logging.getLogger(__name__)

#: 一天最多两条（糖糖定）。
MAX_POSTS_PER_DAY = 2
#: 两帖之间至少隔多久（分钟）。3 小时 —— 避免一个下午连发
MIN_GAP_MIN = 180
#: 过了阈值之后，**每一 tick** 真的发出去的概率上限。
#:
#: 🔴 为什么要这个数：过了阈值就发的话，每次 MIN_GAP 一到就立刻发一条，
#: 时间上完全可预测 —— 那不是「随机发生在有理由的内在状态上」，是定时器。
#: 15 分钟一 tick、一天 96 tick，p_max=0.15 时冲动 0.62 大约 5 小时发一条，
#: 刚好落在一天 1~2 条。**这个数等 shadow 跑出真实分布再调**，别凭感觉改。
P_MAX = 0.15
#: source_state 里的键
STATE_KEY = "moments"
#: 循环节奏（秒）。和 attention_tick 同一个量级
TICK_SECONDS = 900
#: 心跳台账里的活计名。declare 过之后看门狗自动覆盖它
HEARTBEAT_JOB = "post_tick"

#: 开关。默认 off —— 先发代码零行为变化，再开 shadow 看节奏，节奏对了才 on。
#: 糖糖定的两步走（设计文档第七节）：**别合并**这三步。
#:
#:     off     不跑（默认）：`post_tick` 第一个判断就 return None
#:     shadow  跑，只记日志，不落帖（`record.py` 的三段式是它的出口）
#:     on      跑，接消费方（writer → bridge）
#:
#: ⚠️ 默认 off，和 `NOX_TEMPORAL` 同一个理由：它要花钱，而且行为还没被观察过。
ENV = "NOX_MOMENTS"


def mode() -> str:
    """逐字照抄 `temporal/extract.py` 的 `mode()` 形状 —— 两处的开关读法要一样。

    🔴 **每次现读**，不缓存：shadow 转 on 靠改环境变量 + 重启生效，
    启动时读一次存下来的话，循环里那行日志说的就是「启动那一刻是什么模式」，
    和此刻真正在做的事对不上。
    """
    v = os.getenv(ENV, "off").strip().lower()
    if v in ("1", "on", "true", "yes"):
        return "on"
    if v == "shadow":
        return "shadow"
    return "off"


# ---------------------------------------------------------------- 收信号
#
# 这四个都是**免费**的（一次内存读、一次本地查询），所以闸门顺序里它们在最前面：
# 先把这一 tick 的内心状态算完，再说发不发。反过来先看配额的话，shadow 记下来
# 的分布就成了「今天还剩多少」的分布，看不出他一天真正想发几条。


def _local_date(now: datetime) -> str:
    """她那边的今天（`YYYY-MM-DD`）。

    🔴 R9：UTC+8 只在 `temporal` 包里定义一次，这里只 `to_local` ——
    「今天」按她的日历算，不按服务器本地时区猜（`temporal/__init__.py` 的 F3）。
    """
    return to_local(now).date().isoformat()


def _minutes_since(when: Any, now: datetime) -> int | None:
    """某个时刻距现在几分钟。**拿不到就是 None，不猜。**

    None 在这里是有语义的值，不是缺失（`impulse.py`）：`last_contact=None`
    「不知道她多久没说话」不催，`last_post_at=None`「从来没发过」最该发。
    所以读不出来一律传 None，绝不填 0 —— 填 0 会把「不知道」说成「刚刚」。
    """
    if when is None:
        return None
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            logger.warning("Moments 状态里的时刻看不懂，当作没有：%r", when)
            return None
    if not isinstance(when, datetime) or when.tzinfo is None:
        #: naive 值不许猜（`temporal.to_local` 立过同一条规矩）。猜到 8 小时
        #: 以外的去会让「距上一条多久」凭空多出或少掉 8 小时，而且不报错。
        logger.warning("Moments 里的时刻没有时区，当作没有：%r", when)
        return None
    return int((now - when).total_seconds() // 60)


def _read_drives(attention: Any, now: datetime) -> dict[str, float]:
    """`attention.drives(now)` → `{name: intensity}`。**只读**。

    🔴 不许绕过它直接调 `resonance.snapshot()`（`attention/resonance.py`
    的三条边界），也不许回写 Registry —— 这一层只看不动。

    读不出来就当 `{}`（心里没事）：那会让冲动是 0、这一 tick 不发，
    这是**安全的**方向。反过来兜一个默认值上去，等于替他想了一件心事。
    """
    try:
        raw = attention.drives(now)
        return {name: float(drive.intensity) for name, drive in raw.items()}
    except Exception as exc:  # noqa: BLE001
        #: 这个函数跑在后台线程里，冒出去就是整条循环死掉（那才是最难查的坏）
        logger.warning("读 drives 失败，这一 tick 当作心里没事：%s: %s",
                       type(exc).__name__, exc)
        return {}


def _turns_today(sessions: Any, now: datetime) -> int:
    """今天两个人来回了几轮 —— 数 `role == "user"` 的，即她说了几句。

    `messages_between` 的边界是 **UTC ISO**（`data/store.py:399`：
    `created_at` 存的就是 UTC，字符串比较才成立），所以「本地今天 00:00」
    要先换算成 UTC 再传进去。两个边界都显式 `astimezone(utc)`：
    调用方万一把本地时间传进来，字符串比较会静默错位。
    """
    local_midnight = to_local(now).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_iso = local_midnight.astimezone(timezone.utc).isoformat()
    end_iso = now.astimezone(timezone.utc).isoformat()
    try:
        rows = sessions.messages_between(start_iso, end_iso)
        return sum(
            1 for m in rows
            if isinstance(m, dict) and m.get("role") == "user"
        )
    except Exception as exc:  # noqa: BLE001
        #: 数不出来当 0：`turns_today` 越大越**不**该发（今天聊够了），
        #: 所以 0 是最不容易误发的一个兜底，而且方向明确（不知道就当没聊）
        logger.warning("读今天的互动量失败，当作 0：%s: %s",
                       type(exc).__name__, exc)
        return 0


def _lead_drive(drives: Mapping[str, float]) -> str:
    """压着的那几件事里最重的那件 —— `writer.post` 要的是**名字**，不是数值。

    认不出来（空集）就回空串：这里**不编**一个名字出来。空集在冲动过阈值时
    不可能出现（`value = inner × timing`，inner 算法是空集 → 0），所以走到这儿
    空串成不了「有 drive 但说不出是哪条」的假象。
    """
    if not drives:
        return ""
    return max(drives.items(), key=lambda kv: kv[1])[0]


def post_probability(value: float, *, threshold: float = THRESHOLD,
                     p_max: float = P_MAX) -> float:
    """冲动值 → 这一 tick 发出去的概率。没过阈值就是 0（连骰子都不掷）。

    过了阈值之后**线性**升到 `p_max`：inner 越高，越可能在这一 tick 落定。

    🔴 为什么不是过了阈值必发：那样每次 `MIN_GAP` 一到就准点发一条，
    时间上完全可预测 —— 那不叫「随机发生在有理由的内在状态上」，叫定时器
    （设计文档第三节）。随机**必须**挂在冲动值上，所以概率是它的函数。
    """
    if value < threshold:
        return 0.0
    return p_max * (value - threshold) / (1.0 - threshold)


def declare_heartbeat() -> None:
    """登记这条循环该有的节奏。**启动时调一次**（T6 的接线里）。

    必须先 declare 再 beat：只在 beat 时才登记的话，一条**从没成功过**的
    循环在台账里根本不存在 —— 而那恰恰是最该看见的一种坏
    （`obs/heartbeat.py` 的原话）。`off` 的时候连 declare 都不该调：
    关掉的活计本来就不该占着看门狗的一行。
    """
    heartbeat.declare(HEARTBEAT_JOB, every_s=TICK_SECONDS)


def post_tick(*, mode: str, store: Any, attention: Any, sessions: Any,
              bridge: Any, adapter_ref: Any, now: datetime | None = None,
              rng: Any = None) -> MomentRecord | None:
    #: 🔴 `off` 是**第一个**判断，而且是彻底不做：不构造记录、不碰 store、
    #: 不打 bridge、**也不 beat 心跳** —— 关掉的活计不该在台账里装作活着
    #: （`obs/heartbeat.py` 的 stale 判定是「这条循环还在跑吗」，
    #: 关掉的时候它本来就不该在跑）。
    if mode == "off":
        return None
    #: ⚠️ 只认 `record.MODES`（shadow / on）里的值：写成「不是 off 都算」
    #: 的话，`NOX_MOMENTS=ON` 这种大写拼错就会让它真的开始发帖 ——
    #: 一个字母的差别，而日志里什么都不说。
    if mode not in MODES:
        logger.warning("Moments 的 mode 不认识：%r（只认 off / shadow / on），这一 tick 不做任何事",
                       mode)
        return None

    now = now or datetime.now(timezone.utc)
    rng = rng if rng is not None else random.Random()

    #: ② 收信号、算冲动。**全是免费的，所以先算**（顺序理由见文件头）
    state = store.get_source_state(STATE_KEY) or {}
    today = _local_date(now)
    #: 🔴 跨天只清 count：`last_post_at` 留着 —— 清了的话昨晚 23:50 发过一条、
    #: 今天 00:05 还能再发一条，MIN_GAP 在零点这一刻破一个洞，而且不报错
    posts_today = int(state.get("count") or 0) if state.get("date") == today else 0
    signals = Signals(
        drives=_read_drives(attention, now),
        minutes_since_contact=_minutes_since(
            getattr(getattr(attention, "longing", None), "last_contact", None), now),
        turns_today=_turns_today(sessions, now),
        minutes_since_last_post=_minutes_since(state.get("last_post_at"), now),
        posts_today=posts_today,
    )
    imp = impulse(signals)

    def _finish(record: MomentRecord) -> MomentRecord:
        """这一 tick 走完了（第 3~9 步的每条路都从这儿出去）。

        🔴 **心跳记的是「这一 tick 跑完了」，不是「这一 tick 发了帖」。**
        只在发帖成功时 beat 的话，一条三天没发的循环在 `/health` 里
        看起来是死的 —— 而它其实好好跑着，只是没想发；看门狗会去修
        一条没坏的线，几次之后就没人看它了。
        """
        heartbeat.beat(HEARTBEAT_JOB)
        return record

    #: ③ 没过阈值。**连骰子都不掷** —— 随机不是随机抽时间，是随机发生在
    #: 有理由的内在状态上（设计文档第三节）
    if not imp.wants_to_post():
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=None, dice_p=None, posted=False, post_id=None,
            reason="below_threshold",
            why_not_posted=f"冲动 {imp.value:.2f} 没到阈值 {THRESHOLD}：{imp.why}",
        ))

    #: ④ 今天发满了。计数从 `source_state` 来，所以**重启也记得** ——
    #: 只在内存里记的话，每次部署完都能再发一条
    if posts_today >= MAX_POSTS_PER_DAY:
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=None, dice_p=None, posted=False, post_id=None,
            reason="daily_cap",
            why_not_posted=(
                f"今天已经发了 {posts_today} 条，上限 {MAX_POSTS_PER_DAY} 条"
            ),
        ))

    #: ⑤ 距上一条还不到 MIN_GAP —— 避免一个下午连发。
    #: ⚠️ 判据是 `signals.minutes_since_last_post`（跨天**不清**的那个值），
    #: 不是「今天发过几条」：清了 last_post_at 的话，零点这一刻这个洞就开了
    #: （昨晚 23:50 发过，今天 00:05 还能再发，而且不报错）
    if (signals.minutes_since_last_post is not None
            and signals.minutes_since_last_post < MIN_GAP_MIN):
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=None, dice_p=None, posted=False, post_id=None,
            reason="min_gap",
            why_not_posted=(
                f"距上一条才 {signals.minutes_since_last_post} 分钟，"
                f"不到 {MIN_GAP_MIN} 分钟（{MIN_GAP_MIN // 60} 小时）"
            ),
        ))

    #: ⑥ 掷骰子。**到这一步才掷** —— 前面几条闸门用的都是「确定性信号」，
    #: 掷了骰子再回头判断的话，没过阈值的 tick 也会在日志里留下「差点就发」
    dice_p = post_probability(imp.value)
    dice = rng.random()
    if dice >= dice_p:
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=dice, dice_p=dice_p, posted=False, post_id=None,
            reason="dice",
            why_not_posted=(
                f"骰子 {dice:.2f} ≥ 这一 tick 的概率 {dice_p:.2f}"
                "（过了阈值，只是这一 tick 没轮到）"
            ),
        ))

    #: ⑦ shadow：算了、也想发，**故意不落帖**。骰子照样掷（不然 shadow 记下来的
    #: 分布不是他真正会发的分布），但绝不调 writer、绝不打 bridge、
    #: 绝不动计数 —— 动了就偷偷吃掉真实配额。
    if mode == "shadow":
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=dice, dice_p=dice_p, posted=False, post_id=None,
            reason="shadow",
            why_not_posted=(
                "shadow：这一刻本来会发（过了阈值、没到上限也没到间隔、骰子中了），"
                "只是影子模式故意不落帖"
            ),
        ))

    #: ⑧ 生成正文 → 落库。**两处都不许让异常往外冒**：这个函数跑在后台线程里，
    #: 冒出去就是整条循环死掉（`service.py` 那种「进程活着、日志不响、
    #: 而那件事已经不发生了」）。拿不到正文 / 拿不到 id 一律 `write_failed`，
    #: 它和「骰子没中」要能分开数 —— 一个是设计，一个是坏了。
    clock = to_local(now).strftime("%Y-%m-%d %H:%M")
    try:
        recent = writer.recent_posts(bridge)
        body = writer.generate(adapter_ref, signals.drives, recent, imp.why, clock)
    except Exception as exc:  # noqa: BLE001
        logger.warning("发帖生成炸了：%s: %s", type(exc).__name__, exc)
        body = None
    if body is None:
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=dice, dice_p=dice_p, posted=False, post_id=None,
            reason="write_failed",
            why_not_posted="正文没生成出来（模型没给 / 超长 / 调用炸了），这一轮不发",
        ))

    try:
        post_id = writer.post(bridge, body, _lead_drive(signals.drives), imp.why)
    except Exception as exc:  # noqa: BLE001
        logger.warning("发帖落库炸了：%s: %s", type(exc).__name__, exc)
        post_id = None
    if not post_id:
        return _finish(MomentRecord(
            at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
            dice=dice, dice_p=dice_p, posted=False, post_id=None,
            reason="write_failed",
            why_not_posted="正文没落到 bridge（没拿到 id），这一轮不发",
        ))

    #: ⑨ 成功。**先落盘再返回** —— 计数只在返回值里的话，下一次 tick 就忘了
    #: 这一条，上限和间隔一起失效。落盘失败照样返回：帖子**已经发出去了**，
    #: 瞒着不如记着（下次 MIN_GAP 会因此算错，日志里留一条能查）。
    try:
        store.set_source_state(STATE_KEY, {
            "date": today,
            "count": posts_today + 1,
            "last_post_at": now.astimezone(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Moments 状态落盘失败（帖子已经发出去了，计数可能不准）：%s: %s",
                       type(exc).__name__, exc)

    return _finish(MomentRecord(
        at=now, mode=mode, signals=signals, impulse=imp, threshold=THRESHOLD,
        dice=dice, dice_p=dice_p, posted=True, post_id=post_id,
        reason="", why_not_posted="",
    ))


# ---------------------------------------------------------------- 循环


async def run_post_loop(
    *,
    store: Any,
    attention: Any,
    sessions: Any,
    bridge: Any,
    adapter_ref: Any,
    interval_s: int = TICK_SECONDS,
) -> None:
    """FastAPI `lifespan` 用的后台循环。**形状照抄 `run_care_loop`。**

    `post_tick` 里有阻塞的 HTTP（打 bridge）和模型调用，所以丢线程池 ——
    不丢的话这条循环会卡住整个事件循环（`attention/service.py` 开头
    那条 async 边界的原话）。

    ## 🔴 每一轮现读 `mode()`，不在启动时读一次存下来

    shadow 转 on 靠「改环境变量 + 重启」生效；启动时读一次的话，
    循环里那行日志说的是启动那一刻的模式，而 `post_tick` 真正在做的事
    和它记下来的对不上。而且日志里要能看出**这一轮**是什么模式。

    ## 🔴 这里**不 beat 心跳**

    `post_tick` 内部已经 beat 过了（`_finish`，T5 的第 15 条盯着）。
    在这儿再打一次就是**双计**：看门狗判的是「这条循环的节奏还在不在」，
    计数翻倍会让那个判断跟着偏 —— 而它是**唯一**能看出「它是不是还活着」
    的线（`obs/heartbeat.py` 开头）。

    ## 边界（设计文档第一节）

    发帖**不是开口**：这条路径不走 `Orchestrator._deliver`、
    **不碰 push/send**、不占 Care 的每日配额、不吃 gate 冷却 ——
    发帖不推送、不弹她的锁屏。哨兵 R10 盯着（T7 加）。
    """
    logger.info("Moments 循环启动：mode=%s，每 %s 秒一 tick", mode(), interval_s)
    while True:
        try:
            await asyncio.sleep(interval_s)
            #: 🔴 `mode()` 写在 lambda **里面**：每一轮真的去读一次环境变量。
            #: 提到外面存成局部变量的话，这一行的语义就变了（见 docstring）
            record = await asyncio.to_thread(
                lambda: post_tick(
                    mode=mode(), store=store, attention=attention,
                    sessions=sessions, bridge=bridge, adapter_ref=adapter_ref,
                )
            )
            #: `None` = 这一轮什么都没做（off / mode 不认识），不必记
            if record is not None:
                record.log(logger)
        except asyncio.CancelledError:
            logger.info("Moments 循环停止")
            #: 原样往外抛 —— 吞了的话 `lifespan` 的 finally 等不到头，
            #: 关机就挂在这儿
            raise
        except Exception:  # noqa: BLE001
            #: 一轮炸了不该让这条线死掉：死了的表现是「他忽然不再想发帖了」，
            #: 进程活着、日志干净。留 traceback，下一轮接着来
            logger.exception("Moments 循环出错，下一轮继续")
