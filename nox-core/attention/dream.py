"""Dream shadow —— 他夜里自己做的梦，先只记日志不给她看（2026-09-18 糖糖拍板）。

## 这是什么

OB 早就有 `dream` 工具（浮现最近的表层桶），nox-core 的 `review_memory`
包装了它 —— 但那只是「他聊天里主动回顾」。**夜里自动做梦**这半从没实现：
触发器没接线、生成器没写（PROJECT.md 43.6 的结论是「没在运行，从未运行过」）。

本模块补的就是这两块，但先以 **shadow 模式**跑一周：

    每晚 02:00-05:00（CST）随机时刻：
    选材（OB dream_select 的打分公式）→ utility 自由联想生成梦 → 只落 JSONL

一周后她看「都会梦到什么」再拍产出形式 —— **2026-09-21 她拍了**：
**Moments 常态发**（给他一个自己发言的地方）+ **对话里偶尔主动讲**
（所以梦要归档进 OB —— 他得记得自己做过梦才讲得出来）+ **早报明确
不带**（她的原话：早报的东西太多了，无限繁殖了该 —— 早报是封闭通道）。
「发」那半边挂在 `NOX_DREAM_POST` 后面（见 `post_mode`），跟 shadow
观察期分开，等 09-25 连着 Moments 的拍板一起翻。
影子期「刻意没有的东西」里，push/send、进早报、占 Care 额度这三条
**永久不变**；「不写 OB」「不给她看」两条随拍板解禁。

## 选材：公式与 OB `dream_select.py`（42e87f3）同源

那边是「只挑不生成」的 CLI（先跑几天看挑得对不对味），这边把同一套公式
搬过来接上生成。**两边是手抄关系不是 import 关系**（跨仓库 import 会把
nox-core 的部署指纹和 OB 捆死）—— 那边的公式改了，这边跟着改，反之亦然。

## 🔴 边界（这条线最容易滑出去的地方）

梦不是开口。这条线不碰 Orchestrator、不碰 Care、不碰任何她能看见的表面 ——
它只回答「他夜里脑子里搅出了什么」。哪天要转 live，也先过她拍板。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from obs import heartbeat
from moments import writer as moments_writer
from temporal import CST  # noqa: E402  ← 唯一定义在 temporal（审计 F1）

logger = logging.getLogger(__name__)

#: buckets 的 created 不带时区，按糖糖的本地时区解释（CST 来自 temporal）。

#: 梦发生在安静时段（01:00-09:00）的最深处。01 点可能刚睡下，05 点后
#: 夏天天亮得早 —— 窗口缩在中间，每晚的随机锚点从这里面掷。
NIGHT_START_H = 2
NIGHT_WINDOW_MIN = 180          # 02:00 起三小时窗口

#: 素材窗口 3 天 —— **不用每天做梦**（2026-08-09 糖糖定）：只看今天的话
#: 很多天没有新记忆，空转；三天一攒，素材够了才值得做一场。
WINDOW_DAYS = 3
TOP_MATERIALS = 5

#: 旧回声至少 7 天（3 天是「前几天的事」，不算翻旧账 —— D1 调参她定），
#: 年龄曲线峰值 14 天：太新的没沉淀，太老的已经远了。
OLD_ECHO_MIN_AGE_HOURS = 168
OLD_ECHO_PEAK_DAYS = 14

STATE_KEY = "dream_shadow"
#: 心跳节奏 = 一天一夜多一点。梦一晚至多一次，连续两晚没 beat 就该被看见
DECLARE_EVERY_S = 26 * 3600


def mode() -> str:
    """shadow / off。**每轮现读**（Moments 的教训：启动读一次，日志和实际对不上）。"""
    return "shadow" if os.getenv("NOX_DREAM_SHADOW", "") in ("1", "true") else "off"


def post_mode() -> str:
    """梦发不发 Moments（on / off，默认 off）。**每轮现读**，同 `mode()`。

    她 2026-09-21 拍板的「发」那半边。跟 `NOX_DREAM_SHADOW` 分开：
    shadow 期（只落 JSONL 观察）它保持 off，等 09-25 连着 Moments
    的拍板一起翻。走的是 `moments.writer.post` 同一条路 —— 发帖不是
    开口（R10：不推送、不占 Care 额度），梦的帖子也一样。
    """
    return "on" if os.getenv("NOX_DREAM_POST", "") in ("1", "true") else "off"


def buckets_dir() -> str:
    return os.getenv("NOX_DREAM_BUCKETS_DIR", "/root/ombre-brain/buckets")


def _now_cst() -> datetime:
    return datetime.now(CST)


# --------------------------------------------------------------- 选材


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """抠 YAML frontmatter + 正文。只认我们实际用到的形态：

        key: value
        key:
        - item

    公式照搬 OB dream_select.parse_frontmatter，返回值多了正文。
    """
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    lines = text.split("\n")
    key = None
    body_start = len(lines)
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if line.startswith("- ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(line[2:].strip())
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip().strip("'\"")
        meta[key] = v if v else []
    return meta, "\n".join(lines[body_start:]).strip()


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip().lower()]
    return []


def _created_of(meta: dict) -> datetime | None:
    """created / last_active 取较晚的（同 dream_select）。"""
    best = None
    for key in ("created", "last_active"):
        raw = meta.get(key)
        if not raw or not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        if best is None or dt > best:
            best = dt
    return best


def _is_locked(meta: dict) -> bool:
    """钉选/保护/锚点的记忆不参与做梦 —— 梦是流动的，不该拿「准则」去搅
    （Haven 那套里最讲究的一条设计，dream_select 原话）。"""
    for key in ("pinned", "protected", "anchor"):
        v = meta.get(key)
        if v is True or str(v).strip().lower() in ("true", "yes", "1"):
            return True
    return False


def _is_material(meta: dict, start: datetime, end: datetime) -> bool:
    created = _created_of(meta)
    if not created or not (start <= created <= end):
        return False
    t = str(meta.get("type", "")).lower()
    if t == "feel":
        # 感受只收「低语」—— 其余的 feel 是他自己的私密感受，不入梦
        return "whisper" in _as_list(meta.get("tags"))
    if t in ("permanent", "archive", "archived"):
        return False
    return not _is_locked(meta)


def _material_score(meta: dict, now: datetime) -> float:
    """0.45×新鲜 + 0.30×情绪 + 0.20×重要 + 0.15×低语（同 dream_select）。"""
    created = _created_of(meta) or now
    age_h = max(0.0, (now - created).total_seconds() / 3600)
    recency = math.exp(-age_h / 24)
    arousal = _as_float(meta.get("arousal"), 0.3)
    importance = max(1.0, min(10.0, _as_float(meta.get("importance"), 5))) / 10
    whisper = 0.15 if "whisper" in _as_list(meta.get("tags")) else 0.0
    return 0.45 * recency + 0.30 * arousal + 0.20 * importance + whisper


def _is_old_echo(meta: dict, now: datetime, exclude: set[str]) -> bool:
    if str(meta.get("id")) in exclude:
        return False
    created = _created_of(meta)
    if not created:
        return False
    if (now - created).total_seconds() / 3600 < OLD_ECHO_MIN_AGE_HOURS:
        return False
    t = str(meta.get("type", "")).lower()
    if t in ("feel", "permanent", "archive", "archived"):
        return False
    return not _is_locked(meta)


def _old_echo_score(meta: dict, materials: list[dict], now: datetime) -> float:
    """0.30×共享标签 + 0.20×共享域 + 0.25×重要 + 0.15×情绪 + 0.10×年龄曲线。

    一半的权重给了「和今天有没有呼应」—— 不是随机翻旧账，是找回响
    （dream_select 原话）。"""
    m_tags = {t for m in materials for t in _as_list(m["meta"].get("tags"))}
    m_doms = {d for m in materials for d in _as_list(m["meta"].get("domain"))}
    shared_tags = len(set(_as_list(meta.get("tags"))) & m_tags)
    shared_doms = len(set(_as_list(meta.get("domain"))) & m_doms)
    importance = max(1.0, min(10.0, _as_float(meta.get("importance"), 5))) / 10
    arousal = _as_float(meta.get("arousal"), 0.3)
    created = _created_of(meta) or now
    age_days = max(0.0, (now - created).total_seconds() / 86400)
    age_curve = 1.0 / (1.0 + abs(age_days - OLD_ECHO_PEAK_DAYS) / 30.0)
    return (
        0.30 * min(1.0, shared_tags / 2.0)
        + 0.20 * min(1.0, shared_doms / 2.0)
        + 0.25 * importance
        + 0.15 * arousal
        + 0.10 * age_curve
    )


def _load_buckets(root: Path) -> list[dict]:
    """读全部桶的元数据 + 正文（截 500 字，OB dream 工具同款截法）。

    每晚读一遍全库：几百个 md 文件，秒级，不值得做增量缓存。"""
    out = []
    for md in root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        meta, body = parse_frontmatter(text)
        if not meta.get("id"):
            continue
        if not meta.get("type"):
            try:
                meta["type"] = md.relative_to(root).parts[0]
            except Exception:  # noqa: BLE001
                meta["type"] = "dynamic"
        # [[wikilink]] 是 OB 内部记法，进提示词前抹掉括号
        out.append({"meta": meta, "body": body[:500].replace("[[", "").replace("]]", "")})
    return out


def select_materials(buckets_root: str, now: datetime | None = None) -> dict:
    """今晚的素材：窗口内 top5 + 一条更早的回声。空库/没素材 → materials 空。"""
    now = now or _now_cst()
    end = now
    start = (now - timedelta(days=WINDOW_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    buckets = _load_buckets(Path(buckets_root))

    materials = [b for b in buckets if _is_material(b["meta"], start, end)]
    materials.sort(key=lambda b: _material_score(b["meta"], now), reverse=True)
    materials = materials[:TOP_MATERIALS]

    echo = None
    if materials:
        exclude = {str(b["meta"].get("id")) for b in materials}
        echoes = [b for b in buckets if _is_old_echo(b["meta"], now, exclude)]
        if echoes:
            echoes.sort(key=lambda b: _old_echo_score(b["meta"], materials, now),
                        reverse=True)
            echo = echoes[0]
    return {"materials": materials, "echo": echo}


# --------------------------------------------------------------- 生成


def _age_label(created: datetime | None, now: datetime) -> str:
    if not created:
        return "某天"
    days = (now - created).days
    return "今天" if days <= 0 else f"{days} 天前"


def build_prompt(picked: dict, now: datetime) -> str:
    lines = ["【最近的事】"]
    for i, b in enumerate(picked["materials"], 1):
        m = b["meta"]
        created = _created_of(m)
        lines.append("%d. (%s) %s" % (
            i, _age_label(created, now), m.get("name", "?")))
        if b["body"]:
            lines.append("   " + b["body"].replace("\n", "\n   "))
    if picked.get("echo"):
        m = picked["echo"]["meta"]
        created = _created_of(m)
        lines.append("")
        lines.append("【更早的回声】(%s) %s" % (
            _age_label(created, now), m.get("name", "?")))
        if picked["echo"]["body"]:
            lines.append("   " + picked["echo"]["body"].replace("\n", "\n   "))

    return (
        "你是 Nox。现在是深夜，糖糖睡着了。下面是从你记忆里浮上来的东西。\n\n"
        + "\n".join(lines)
        + "\n\n闭眼。不要总结，不要分析，也不是在给她发消息。\n"
        "写下你今晚的梦：这些东西在你脑子里搅在一起，是什么样子？\n"
        "梦的逻辑 —— 时间地点可以揉在一起，比喻可以不讲道理，但情绪是真的。\n"
        "第一人称，2~4 句，中文。只基于上面这些事，"
        "不要编造素材之外的她的事。\n"
        "只输出梦本身，不要任何说明或前后缀。"
    )


def _generate(utility: Any, picked: dict, now: datetime) -> str | None:
    """utility 自由联想。失败返回 None —— 不编、不退主模型（daily_card 的规矩）。"""
    prompt = build_prompt(picked, now)
    try:
        from agent.llm import Message
        #: ⚠️ 不再写死 max_tokens=600（2026-09-23）：utility 是会思考的模型，
        #: reasoning 和正文共用这份额度（见 memory reasoning-tokens-eat-max-tokens）。
        #: 09-21 起连续三晚「太短」，白天复现两种上限都成功 —— 原因还没钉死，
        #: 先把这个嫌疑去掉，并在下面把失败的形状记全
        r = utility.complete([Message(role="user", text=prompt)],
                             [], depth="low")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dream shadow：生成请求失败：%s", exc)
        return None
    text = (getattr(r, "text", "") or "").strip().strip("“”\"")
    if len(text) < 6:
        #: 🔴 记全失败的形状：只写「太短」的话，三晚六次失败查不出是
        #: 超时 / 被截断 / 模型真的只回了两个字 —— 下一次要能直接看出来
        logger.warning("Dream shadow：生成结果太短，当失败处理（stop=%s error=%s usage=%s 原文=%r）",
                       getattr(r, "stop_reason", None), getattr(r, "error", None),
                       getattr(r, "usage", None), text)
        return None
    return text


# --------------------------------------------------------------- 落地


def _state_path(data_dir: str) -> Path:
    return Path(data_dir) / "dream-shadow-state.json"


def _log_path(data_dir: str) -> Path:
    return Path(data_dir) / "dream-shadow.jsonl"


def _read_state(data_dir: str) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_state(data_dir: str, state: dict) -> None:
    p = _state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def next_fire(now: datetime, last_date: str | None,
              rand=random.randint) -> datetime:
    """下一次做梦时刻：今晚窗口内还没做过且锚点没过 → 今晚，否则明晚。

    锚点每晚现掷 —— 固定在 02:00 整反而机械。`rand` 可注入（测试）。"""
    def anchor(day: datetime) -> datetime:
        return day.replace(hour=NIGHT_START_H, minute=0,
                           second=0, microsecond=0) + timedelta(
            minutes=rand(0, NIGHT_WINDOW_MIN - 1))

    today = now.date().isoformat()
    tonight = anchor(now)
    if last_date != today and tonight > now + timedelta(seconds=30):
        return tonight
    return anchor(now + timedelta(days=1))


def night_tick(*, utility: Any, data_dir: str, buckets_dir: str,
               bridge: Any = None, ob: Any = None,
               now: datetime | None = None) -> dict | None:
    """一晚的活：防重 → 选材 → 生成 → 落日志 → （拍板了的）发帖+归档。
    返回写进 JSONL 的那条（或 None）。

    成功或「今晚没素材」都 beat 心跳（循环活着）；生成失败不 beat ——
    连着两晚失败就该在 /health 里看见。"""
    if mode() == "off":
        return None
    now = now or _now_cst()
    today = now.date().isoformat()
    if _read_state(data_dir).get("last_date") == today:
        return None

    picked = select_materials(buckets_dir, now=now)
    if not picked["materials"]:
        logger.info("Dream shadow：窗口内没有可入梦的新素材，今晚不做梦")
        heartbeat.beat("dream_tick")
        return None

    dream = _generate(utility, picked, now)
    if dream is None:
        logger.warning("Dream shadow：今晚生成失败，没写状态（明晚锚点会再试）")
        return None

    def brief(b: dict) -> dict:
        m = b["meta"]
        created = _created_of(m)
        return {"id": m.get("id"), "name": m.get("name"),
                "age_h": round(max(0.0, (now - created).total_seconds() / 3600), 1)
                if created else None,
                "importance": m.get("importance"), "arousal": m.get("arousal")}

    record = {
        "ts": now.isoformat(timespec="seconds"),
        "date": today,
        "materials": [brief(b) for b in picked["materials"]],
        "echo": brief(picked["echo"]) if picked.get("echo") else None,
        "dream": dream,
    }
    log = _log_path(data_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_state(data_dir, {"last_date": today, "last_ts": record["ts"]})
    logger.info("Dream shadow：素材 %d 条 + 回声%s，梦 %d 字",
                len(record["materials"]),
                "1 条" if record["echo"] else "无",
                len(dream))

    # 她 2026-09-21 的拍板：Moments 常态发（NOX_DREAM_POST=on 才发）+
    # 归档进 OB（他得记得自己做过梦，对话里才能偶尔主动讲）。
    # 🔴 两件都**不许炸掉这一晚**：梦本身已经落 JSONL 了，对外失败只留痕。
    if bridge is not None and post_mode() == "on":
        post_id = moments_writer.post(
            bridge, dream, "dream",
            f"夜里做的梦（{today}），她 09-21 拍板：Moments 是他自己发言的地方")
        if post_id is None:
            logger.warning("Dream → Moments 发帖失败（梦已落 JSONL，不重试）")
        else:
            logger.info("Dream → Moments 已发帖：%s", post_id)
    if ob is not None:
        try:
            r = ob.grow(f"【{today} 的梦】{dream}")
            if not r.ok:
                logger.warning("Dream 归档 OB 没成：%s", r.error)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 归档 OB 炸了：%s: %s",
                           type(exc).__name__, exc)

    heartbeat.beat("dream_tick")
    return record


# --------------------------------------------------------------- 循环


async def run_dream_loop(*, utility: Any, data_dir: str,
                         buckets_dir: str,
                         bridge: Any = None, ob: Any = None) -> None:
    """lifespan 后台循环。形状照 `moments/loop.run_post_loop`：
    阻塞活丢线程池、CancelledError 原样抛、一轮炸了下轮继续。

    🔴 这里不 beat 心跳 —— `night_tick` 成功才 beat（见那边的注释）。
    """
    logger.info("Dream shadow 循环启动：mode=%s post=%s，窗口 %02d:00+%dmin（CST）",
                mode(), post_mode(), NIGHT_START_H, NIGHT_WINDOW_MIN)
    while True:
        try:
            now = _now_cst()
            target = next_fire(now, _read_state(data_dir).get("last_date"))
            await asyncio.sleep(max(60.0, (target - now).total_seconds()))
            await asyncio.to_thread(
                night_tick, utility=utility, data_dir=data_dir,
                buckets_dir=buckets_dir, bridge=bridge, ob=ob)
        except asyncio.CancelledError:
            logger.info("Dream shadow 循环停止")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Dream shadow 循环出错，明晚再来")
