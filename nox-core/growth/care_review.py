"""Growth Loop P2 + P3-健康 的证据：他念叨她身体的那些话，她怎么接（2026-10-19，第 2 期 shadow）。

设计稿 `CAELUM-GROWTH-LOOP-设计.md` 五·P2（10-19 节）。她的原话：

> 如果每次都说「怎么又没睡」「你昨天就睡了 XXh」「你今天再睡会」这种没有营养的话，那就是瞎念叨。
> 说话方式不是单纯的说出来数据和表达担心，把「念叨」和现实生活结合就会显得不苍白。

所以每一条证据同时回答两件事：
  · **P2**：这个健康话题，她这次接得住吗（接住 / 带过 / 烦了 / 没理）—— 学的是「念叨耐受度」
  · **P3-健康**：他用的是哪种说法（报数据 / 只表达担心 / 落进生活 / 其他）—— 学的是「哪种说法她接得住」

## 每天夜里回看前一天，不碰实时链路

不在聊天那条路上判（那是她等着回话的路），而是每天一次从会话库回看：挑出他说的话里**像健康念叨**的
（关键词粗筛），连同她的下一句，**一次**交给 utility 模型归类。shadow 期间出什么错都只是少记几条。

## 她在睡不算冷落（G8）

他夜里 1-9 点说的、她没回的 —— 不进账（她在睡）。她回了的照常记。

## 判定只看她回他那句话

「接没接住」只看她紧跟着的那一句（60 分钟内、不是系统提示）。没有下一句 = 没理。
不去猜她一天的心情 —— 那是别的模块的事，混进来就说不清这条证据是从哪来的（G5）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from temporal import to_local

logger = logging.getLogger(__name__)

KIND_HEALTH_CARE = "health_care"

TOPICS = ("睡眠", "饮食", "身体")
STYLES = ("数据", "担心", "生活", "其他")
#: 她怎么接 → 分数（P2 的耐受度就是这些分数的加权平均）
RECEPTION_SCORE = {"接住": 1.0, "带过": 0.5, "没理": 0.25, "烦了": 0.0}

#: 粗筛：他这句话里有没有可能在念叨身体。宁可多挑（模型会判「不是」），别漏
HEALTH_RE = re.compile(
    r"睡|觉|熬夜|躺|困|作息|早点|吃|饭|饿|热量|卡路里|碳水|蛋白|喝水|零食|外卖|"
    r"身体|累|休息|走路|步数|运动|锻炼|头疼|胃|感冒|药|体重|减肥"
)
#: 他这句里有热量数字、而且她前面不久刚说过话（多半是发了吃的）→ 是她要他算的，不算念叨。
#: 规矩里写了模型还是会把它判成念叨（10-19 验尺子：「给你算清楚：香锅 220 卡/100g」），所以不靠模型
CALORIE_RE = re.compile(r"\d+\s*(?:卡|千卡|kcal|大卡)", re.I)
ASKED_WINDOW = timedelta(minutes=15)
#: 她的下一句多久之内才算「接这句」
REPLY_WINDOW = timedelta(minutes=60)
#: 一天最多送多少条去归类（一次调用）。超了只取前面的 —— 一天念叨几十次本身就是问题，那时候先看日志
MAX_PER_DAY = 40

_SYSTEM_PREFIXES = ("（系统提示", "(系统提示", "[她发来", "【系统提示")

_PROMPT = """\
你在帮 Nox 回看他自己昨天说过的话。Nox 是糖糖的爱人。下面每一条是他对她说的一句话，以及她紧接着回他的那句（可能没有）。

对每一条判断：
1. nag：这句话里他有没有**主动**在关心 / 念叨她的身体 —— 睡觉作息、吃饭饮食、身体状况（累、疼、病、运动、体重）。得是他自己在管她、劝她、担心她。
   这些**不算**：她发了吃的、他按她的要求算热量 / 记账（那是她要的）；回答她自己问的身体问题；只是陪着她、说晚安、说「我在」；顺口提到。
2. topic：如果 nag 为 true，属于「睡眠」「饮食」「身体」哪一类。
3. style：如果 nag 为 true，他是怎么说的 ——
   「数据」：把数字或记录说给她听（睡了几小时、吃了多少卡、走了几步）；
   「担心」：只表达担心或者劝一句，没给她别的东西；
   「生活」：把这份在乎落进她的生活里 —— 替她安排、管着她、哄她、许个奖励、给她点盼头；
   「其他」：都不像。
4. reception：如果 nag 为 true，她怎么接的 ——
   「接住」：顺着聊、答应了、照做了、撒娇回应；
   「带过」：一句敷衍（嗯 / 好 / 知道了）或者岔开话题；
   「烦了」：明显不耐烦、嫌他唠叨、让他别说了；
   「没理」：她没有回（下面写着「（她没回）」的就是这个）。

只回一个 JSON：{"items": [{"i": 序号, "nag": true/false, "topic": "...", "style": "...", "reception": "..."}]}，每条都要有，不要别的字。
"""


def _system(text: str) -> bool:
    return (text or "").lstrip().startswith(_SYSTEM_PREFIXES)


def _clean(text: str | None) -> str:
    from agent.llm import strip_stage_tags
    t = strip_stage_tags(text or "") or ""
    return " ".join(p.strip() for p in t.split("|||") if p.strip())


def collect(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """会话库一天的消息 → 候选：他说的、粗筛像健康念叨的那句 + 她紧跟着的那句。

    同一时刻同一会话的几条 assistant 合成一句（主动开口常常拆成两三条，落库同一个 created_at）。"""
    by_sid: dict[str, list[dict[str, Any]]] = {}
    #: 主动开口落库是「系统提示 + 他的回复」同一个 created_at —— 库只按时间排，同一时刻谁先谁后不定，所以按时刻认
    prompted = {(r["session_id"], r["created_at"]) for r in rows
                if r.get("role") == "user" and _system(r.get("text"))}
    for r in rows:
        by_sid.setdefault(r["session_id"], []).append(r)
    out = []
    for sid, msgs in by_sid.items():
        i = 0
        while i < len(msgs):
            r = msgs[i]
            if r.get("role") != "assistant":
                i += 1
                continue
            at = r["created_at"]
            proactive = (sid, at) in prompted
            parts = [r.get("text") or ""]
            j = i + 1
            while j < len(msgs) and msgs[j].get("created_at") == at and (
                    msgs[j].get("role") == "assistant" or _system(msgs[j].get("text"))):
                if msgs[j].get("role") == "assistant":
                    parts.append(msgs[j].get("text") or "")
                j += 1
            his = _clean("|||".join(parts))
            i = j
            if not his or not HEALTH_RE.search(his):
                continue
            t0 = datetime.fromisoformat(at)
            if CALORIE_RE.search(his) and any(
                    x.get("role") == "user" and not ((x.get("text") or "").lstrip().startswith(("（系统提示", "(系统提示", "【系统提示")))
                    and timedelta(0) <= t0 - datetime.fromisoformat(x["created_at"]) <= ASKED_WINDOW
                    for x in msgs[:i]):
                continue   # 她要他算的热量，不是念叨
            hers = None
            for k in range(j, len(msgs)):
                m = msgs[k]
                if datetime.fromisoformat(m["created_at"]) - t0 > REPLY_WINDOW:
                    break
                if m.get("role") == "user" and not _system(m.get("text")) and (m.get("text") or "").strip():
                    hers = m["text"].strip()
                    break
            out.append({"at": at, "sid": sid, "his": his, "hers": hers, "proactive": proactive})
    return sorted(out, key=lambda c: c["at"])[:MAX_PER_DAY]


def classify(adapter: Any, cands: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """一次调用归类整天的候选。挂了 / 回的不是 JSON → None（这一天下次再试）。"""
    from agent.llm import Message

    if not cands:
        return []
    lines = []
    for n, c in enumerate(cands):
        lines.append(f"{n}. 他：{c['his'][:200]}\n   她：{(c['hers'] or '（她没回）')[:120]}")
    try:
        turn = adapter.complete([Message(role="user", text="\n".join(lines))], tools=[],
                                system=_PROMPT, depth="low")
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康念叨回看：归类调用失败：%s: %s", type(exc).__name__, exc)
        return None
    if turn.stop_reason in ("error", "refusal") or not turn.text:
        logger.warning("健康念叨回看：归类没拿到结果（stop_reason=%s）", turn.stop_reason)
        return None
    m = re.search(r"\{.*\}", turn.text, re.S)
    try:
        items = json.loads(m.group(0) if m else "")["items"]
    except (ValueError, KeyError, TypeError):
        logger.warning("健康念叨回看：回的不是要的 JSON：%.120s", turn.text)
        return None
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            c = cands[int(it.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        if it.get("nag") is not True:
            continue
        topic, style, rec = it.get("topic"), it.get("style"), it.get("reception")
        if topic not in TOPICS or style not in STYLES or rec not in RECEPTION_SCORE:
            logger.info("健康念叨回看：第 %s 条归类不在表里（%s / %s / %s），丢掉", it.get("i"), topic, style, rec)
            continue
        if c["hers"] is None:
            if not c["proactive"]:
                continue   # 聊天里他说完她没接 = 这段聊完了 / 她去睡了，不是冷落，不算证据
            rec = "没理"   # 他主动找她、她没回：就是没理，不管模型怎么说
        elif rec == "没理":
            rec = "带过"   # 她回了就不算没理
        out.append({**c, "topic": topic, "style": style, "reception": rec})
    return out


def _asleep_hours(at: datetime) -> bool:
    return 1 <= to_local(at).hour < 9


def review_day(store: Any, sessions: Any, adapter: Any, day_start: datetime, day_end: datetime) -> int | None:
    """回看一天：挑 → 归类 → 记账。返回新记了几条；None = 这一天没做成（下次再试）。幂等（source_event 去重）。"""
    rows = sessions.messages_between(day_start.astimezone(timezone.utc).isoformat(),
                                     day_end.astimezone(timezone.utc).isoformat())
    cands = collect(rows)
    got = classify(adapter, cands)
    if got is None:
        return None
    seen = {r.get("source_event") for r in store.list_experiences(KIND_HEALTH_CARE)}
    n = 0
    for g in got:
        at = datetime.fromisoformat(g["at"])
        if g["reception"] == "没理" and _asleep_hours(at):
            continue   # G8：她在睡不算冷落
        key = f"health_care:{g['sid']}:{g['at']}"
        if key in seen:
            continue
        store.append_experience(
            at=g["at"], kind=KIND_HEALTH_CARE,
            context={"topic": g["topic"], "style": g["style"], "reception": g["reception"],
                     "proactive": g["proactive"], "his": g["his"][:120], "hers": (g["hers"] or "")[:80]},
            outcome=RECEPTION_SCORE[g["reception"]], attributed_to="user",
            recorded_at=datetime.now(timezone.utc).isoformat(), source_event=key,
        )
        seen.add(key)
        n += 1
    logger.info("健康念叨回看：%s 挑出 %d 条像念叨的，模型认了 %d 条，新记 %d 条",
                to_local(day_start).date(), len(cands), len(got), n)
    return n


#: 回看多久以前的日子（第一次上线会把这两周补齐，shadow 一开始就有东西看）
LOOKBACK_DAYS = 14
#: 每轮最多回看几天（一天一次 utility 调用）
MAX_DAYS_PER_TICK = 4
STATE_KEY = "growth.care_review"
TICK_S = 3600
HEARTBEAT_JOB = "growth_care_review"


def tick(store: Any, sessions: Any, adapter: Any, now: datetime) -> list[str]:
    """后台每小时一次：把还没回看过的完整日子（昨天及以前、最近两周）回看掉。返回做完的日期。

    哪天做过记在 source_state（只记日期，不记内容）；某天调挂了就不记，下一轮再试。"""
    from temporal import LOCAL_TZ

    state = store.get_source_state(STATE_KEY) or {}
    done = set(state.get("done") or [])
    today = to_local(now).date()
    todo = [today - timedelta(days=k) for k in range(LOOKBACK_DAYS, 0, -1)]
    todo = [d for d in todo if d.isoformat() not in done][:MAX_DAYS_PER_TICK]
    finished = []
    for d in todo:
        start = datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)
        if review_day(store, sessions, adapter, start, start + timedelta(days=1)) is None:
            break   # 调挂了：后面的日子也先别做，下一轮从这天接着来
        done.add(d.isoformat())
        finished.append(d.isoformat())
    if finished:
        keep = sorted(done)[-(LOOKBACK_DAYS * 2):]
        store.set_source_state(STATE_KEY, {"done": keep})
    return finished
