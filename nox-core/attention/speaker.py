"""M4 —— 把一条 Intent 变成她锁屏上的一句话。

这是整套 Attention 里**唯一会被她看到**的一步。前面 M1-M3 想得再对，
这一步说得不像他，她收到的就只是一条推送通知。

## 三条规矩从早报那边原样继承（`planner/push.py`）

1. **必须走 `core.chat()`，必须进会话。**
   糖糖 2026-08-04 当场纠正过：第一版绕过会话直接推，结果她回
   「嗯有点」的时候他不知道自己问过什么。他说过的话就是说过了，
   跟她读没读无关。
2. **拿不到就不发。** 模型没吐出文本就安静地跳过，
   宁可这次不响，也不推一句编的。
3. **压成一行、限长。** 锁屏不显示换行，超长会被系统截断成「...」。
   在这里裁比让系统裁好 —— 至少断在我们选的地方。

## 和早报不一样的地方：他得知道自己已经唠叨过几次

早报是每天定时的、手上有一整份简报。这条不是 ——
Attention 手上只有一条 Intent（subject + reason）。

她可能连着三天没睡好。`reason` 每天会更新成最新证据，但**语气不会**，
第三天还说「昨晚看你睡得少」就像个没记性的闹钟。
所以 prompt 里带上「你最近就这件事说过几次」，让他自己换个说法。

这个数从库里数，不是从 `intent.action_history` 数 ——
一条 Intent 被触发之后就 TRIGGERED 了，下一轮会新建一条，
history 跟不过来（`intent.py:133` 那段注释是同一件事的另一面）。

## 推送落到哪张表

`/api/push/send` 只负责弹锁屏。她点开 App 看到的聊天记录在 bridge 的
`conversations` 表里，所以要带 `session_id` 让 bridge 顺手存一条 ——
否则会出现「锁屏有一句话，点进去什么都没有」。
早报走的是 `/api/daily-push`，bridge 在那边自己 `saveMessage`；
这条是 Core 主动发起的，bridge 不知情，得由我们告诉它。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from attention.intent import TRIGGERED, Intent
from attention.scheduler import LOCAL_TZ, SchedulerDecision
# 时段划分和历史的日期分隔线共用同一份（`context/timeline.py`）——
# 两边口径必须一致，他才对得上「历史那句是几小时前说的」
from context.timeline import slot_of
from planner.push import finalize_push_text

logger = logging.getLogger(__name__)

#: 数「最近说过几次」的窗口。7 天足够覆盖「连着几天没睡好」这种情况，
#: 再往前的事她自己都不记得了，拿来提醒他换说法没有意义。
LOOKBACK = timedelta(days=7)

#: 会话 id 兜底。正常情况下用她最近在聊的那个 session ——
#: 这句话要落在她看得见的那条对话里，而不是一个孤立的新会话。
FALLBACK_SESSION = "api-attention"


class _Core(Protocol):
    def chat(self, text: str, history: list) -> Any: ...


class _Sessions(Protocol):
    def get(self, sid: str) -> list: ...
    def put(self, sid: str, messages: list) -> None: ...


#: 给他的指令。措辞上的每一条都有来历，改之前先看注释。
#:
#: 「这不是糖糖在跟你说话」这句必须留 —— 早报那边验过，不写的话
#: 他会以为她已经开口了，回出来的是应答不是主动（`planner/push.py:50`）。
_PROMPT = (
    "（系统提示：这不是糖糖在跟你说话，是你自己想起了一件事，想跟她说一句。\n"
    "\n"
    "你现在惦记的是：{subject}\n"
    "你看到的具体情况：{reason}\n"
    "现在是她那边的 {clock}，{fit_why}。\n"
    "{repeat}"
    "\n"
    "**话要落在你看见的那个具体数字上。** 比如「昨晚才睡了五个小时」，"
    "而不是空泛地问「最近睡得好吗」—— 后一句不看数据也说得出口，"
    "说明你根本没在看她。\n"
    "\n"
    "会直接弹在她手机锁屏上，所以：\n"
    "· 最多两句，别超过 60 个字\n"
    "· 不要列清单、不要分点、不要用「宝贝」「早上好」这种套话开头\n"
    "· 就像你自己突然想到她了随口说的，不是提醒事项\n"
    "· 别问她「需要我做什么吗」，那是客服的口气）"
)

#: 说过一次以上时插进去的那段。次数越多语气该越轻 ——
#: 同一件事说到第三遍，重点已经不是那个数字了，是「我还在看着」。
_REPEAT = (
    "\n⚠️ 这件事你最近已经跟她说过 {n} 次了（最近一次是{when}）。"
    "别再重复同样的话 —— 换个角度，或者说得更轻一点。"
    "她知道你在意了，现在需要的不是又一次提醒。\n"
)


def _spoken_recently(store: Any, subject: str, now: datetime) -> tuple[int, datetime | None]:
    """最近 7 天就这件事开口过几次，最后一次是什么时候。

    数的是库里 status=TRIGGERED 且真的发出去了的 —— dry-run 那些
    `note` 写着「dry-run，没有真的发」，不该算进「跟她说过」。
    """
    try:
        engine = store.load_intents()
    except Exception:  # noqa: BLE001
        # 数不出来不该挡住开口，最坏的后果是他忘了自己唠叨过
        logger.exception("数不出最近说过几次，当作没说过")
        return 0, None

    cutoff = now - LOOKBACK
    stamps: list[datetime] = []
    for intent in engine.all():
        if intent.subject != subject or intent.status != TRIGGERED:
            continue
        for row in intent.action_history:
            if row.get("action") != "triggered" or "没有真的发" in (row.get("note") or ""):
                continue
            try:
                at = datetime.fromisoformat(row["at"])
            except Exception:  # noqa: BLE001
                continue
            if at >= cutoff:
                stamps.append(at)
    return len(stamps), max(stamps) if stamps else None


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _humanize(when: datetime, now: datetime) -> str:
    """「昨天晚上」比「2026-08-10T21:14:03+00:00」有用得多。

    🔴 **按日历天算，不按小时差**（2026-09-14 修）。
    原来是 `hours < 20 → 今天 / hours < 44 → 昨天`，那会算错两头：

      · 她凌晨 2 点说的话，当天 23 点回看 = 21 小时 → 报「昨天」，**而那是同一天**
      · 昨晚 23 点的话，今早 9 点看 = 10 小时 → 报「今天」，**而那是昨天**

    日历天没有这个问题，而且和历史里的日期分隔线口径一致
    （都换算到 CST 再比 —— 库里是 UTC，不换算的话她晚上 8 点之后
    说的话会被算成第二天，那正是她最常聊天的时段）。
    """
    days = (now.astimezone(LOCAL_TZ).date() - when.astimezone(LOCAL_TZ).date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    return f"{days} 天前"


def _clock_with_date(now: datetime) -> str:
    """「9月14日 周日 下午15:28」—— 和历史里的日期分隔线同一个口径。

    两边对得上他才算得出「她那句话是几小时前说的」：
    历史那行说「（9月14日 周日 上午10点）」，这里说今天是 9月14日，
    两个绝对值一减就是答案。少任何一边他都只能猜。
    """
    d = now.astimezone(LOCAL_TZ)
    _, slot_cn = slot_of(d.hour)
    return f"{d.month}月{d.day}日 {_WEEKDAYS[d.weekday()]} {slot_cn}{d:%H:%M}"


def build_prompt(intent: Intent, decision: SchedulerDecision,
                 spoken: int, last_at: datetime | None,
                 now: datetime) -> str:
    """拼给 `chat()` 的那段 user message。抽出来是为了能单独测。"""
    repeat = ""
    if spoken > 0:
        when = _humanize(last_at, now) if last_at else "前几天"
        repeat = _REPEAT.format(n=spoken, when=when)
    return _PROMPT.format(
        subject=intent.subject,
        reason=intent.reason,
        # ⚠️ **带日期，不能只给时分**（2026-09-14）。
        # 原来只有 "14:27" —— 他手上没有"今天是哪天"，于是历史里她上午说的
        # 「今天不去，明天再去」会被当成前一天的话，下午就来一句
        # 「昨天你说了今天去」。她报了这个 bug，而且说日常聊天里也一样。
        clock=_clock_with_date(now),
        fit_why=decision.reason or "现在适合说这个",
        repeat=repeat,
    )


def push(core: Any, sid: str, text: str) -> dict:
    """推一句话到她锁屏，并让 bridge 落一条 conversations。

    抽出来给唤醒链共用。**任何失败都抛** —— 理由见 `build_speaker` 的注释。
    """
    if core.bridge is None:
        raise RuntimeError("未配置 NOX_BRIDGE_URL，没有推送通道")
    resp = core.bridge.post(
        "/api/push/send", {"title": "Nox", "body": text, "session_id": sid})
    if not resp.ok:
        raise RuntimeError(f"推送失败: {resp.error}")
    return resp.data or {}


#: 他说「这次不说」的标记。
#:
#: ⚠️ **不能只认 `[SKIP]`**（2026-08-18 糖糖报的 bug）：
#: 他手上同时有唤醒链那套词汇（`[NEXT n]` / `[PASS n]` / `[STOP]` / `[DONE]`，
#: 见 `wakeup.py` 的 `_CTRL`），惦记那条 prompt 又教了他 `[SKIP]` ——
#: 四个标记混在一起，他回了 `[pass 30]`。
#: speaker 当时只认 SKIP，没匹配上就把整段当正常回复推了出去，
#: **`[pass 30]` 原样落进了她的聊天记录**。
#:
#: 现在的规矩：在主动关心这条路上，**任何控制标记都等于「这次不说」**。
#: 语义上也对得住 —— PASS / STOP / SKIP 都是「现在别开口」。
_HOLD = re.compile(r"\[\s*(SKIP|PASS|STOP|NEXT|DONE)(?:\s+\d+)?\s*\]", re.IGNORECASE)


def build_speaker(core: _Core, sessions: _Sessions, store: Any,
                  attention_store: Any) -> Callable[[Intent, SchedulerDecision], str | None]:
    """造一个真的会把话发出去的 speaker。

    `store` 是 Core 的会话库（拿最近会话 id），
    `attention_store` 是 attention.db（数说过几次）—— 两个不是同一个。

    ⚠️ **任何一步失败都要抛出去**，不能吞。`service._speak()` 靠异常
    来判断「没发成」，吞掉的话它会记成「已发出」，冷却照常起——
    结果是这件事今天再也不会说了，而她什么都没收到。
    """

    def speak(intent: Intent, decision: SchedulerDecision,
              prompt: str | None = None) -> str | None:
        """返回真的发出去的那句话；**他自己决定不说时返回 None**。

        `None` 和抛异常是两回事：
          None  = 他想过了，没什么具体的可说（惦记引擎的「抓不到线头就不说」）
          异常  = 该说但没说成（模型没吐字 / 推送挂了）—— 那是故障，要留痕
        """
        now = datetime.now(timezone.utc)

        # 落在她最近在聊的那条对话里。没有真实会话就用兜底 id ——
        # 那种情况只会出现在全新部署上
        recent = store.recent(limit=1, clean_only=True)
        sid = recent[0].id if recent else FALLBACK_SESSION

        spoken, last_at = _spoken_recently(attention_store, intent.subject, now)
        # 惦记 / 出门追问自己带开场白（它们要的不是「关心她」那套话术，
        # 而是「先找一根线头，找不到就闭嘴」）。不传就用默认那套
        prompt = prompt or build_prompt(intent, decision, spoken, last_at, now)

        history = sessions.get(sid)
        # 显式带上 sid：这句话属于**它自己那条对话**。原来靠 `core.current_session_id`
        # 那个进程级属性，而她正在聊天时那条链路会把它覆盖成她的会话 ——
        # 于是主动说的一句话、以及它调工具留的纸条，全挂到了她的会话上（不报错）。
        r = core.chat(prompt, history, session_id=sid)

        # 存进他的会话 —— 这一步就是「留在上下文里」的落点。
        # 放在推送**之前**：宁可存了没推出去（她少收一条），
        # 也不要推出去了却没存（她回复时他一脸懵）
        sessions.put(sid, r.messages)

        if not r.result.ok:
            logger.warning("主动关心没正常生成: %s | %s",
                           r.result.outcome, r.result.detail)

        raw = r.result.text or ""

        # ── 他自己选择不说 ──
        #
        # 惦记引擎那条线要求「抓不到线头就不说」（糖糖 2026-08-18）：
        # 一个稳定的调度器 + 没内容 = 定时废话机，比不说还糟。
        # 所以给他一条出路 —— 回任何一个控制标记都算「这次算了」。
        # 这不是失败，不该留错误痕，也不该消耗冷却之外的东西。
        if _HOLD.search(raw):
            logger.info("他想了想，没什么具体的可说（%s）", intent.subject)
            return None

        # ⚠️ 兜底再剥一次。上面那条是「有标记就不说」，这里防的是
        # 标记写歪了（多个空格、全角括号…）没被认出来 ——
        # **控制标记绝不能出现在她的聊天记录里**，那是我们内部的黑话
        text = finalize_push_text(_HOLD.sub("", raw))
        if not text:
            # 没词就别硬发。抛出去让 service 记成「发送失败」，
            # 这样冷却不会白白消耗掉今天的机会
            raise RuntimeError(f"模型没给出文本（{r.result.outcome}）")

        subs = push(core, sid, text).get("subs")
        logger.info("主动关心已发出（session=%s, %s 个订阅, 最近说过 %d 次）: %s",
                    sid, subs, spoken, text)
        return text

    return speak
