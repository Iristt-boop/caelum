#!/usr/bin/env python3
"""Dream 选材 —— 只挑，不生成，只打日志。

## 这一步在做什么

我们的 OB 已经有 `dream` 工具（`server.py:1302`），但它只是
「浮现最近的 dynamic 桶让模型自省」—— 是**回顾**，不产生新东西，
也看不见两周前的事。

这个脚本补的是选材那一层：**今晚该梦见什么**。
两个打分函数照搬 Haven-Ombre 的算法（只搬公式，不搬代码），
跑在我们自己的 buckets 上。

## 为什么先只打日志

和 Attention 的 M3 dry-run 同一个道理：
**选材挑错了的话，后面让 LLM 写得再美也是错的。**
先跑几天看它挑出来的东西对不对味，再谈生成。

## 零依赖

自己解析 YAML frontmatter，不 import yaml —— 这脚本要能在任何一台
装了 python3 的机器上直接跑，不关心 OB 的运行环境装了什么。
字段就那么几个，手写解析比引依赖划算。
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 糖糖在中国。buckets 里的 created 不带时区（'2026-07-24T23:53:26'），
# 只能按她的本地时区解释。
#
# ⚠️ 用固定 offset，不要 ZoneInfo("Asia/Shanghai") ——
# zoneinfo 在 Windows 上要额外装 tzdata，开发机会直接抛
# ZoneInfoNotFoundError（2026-08-08 在 scheduler.py 上栽过一次）。
# 中国 1991 年后不实行夏令时，UTC+8 恒定。
LOCAL_TZ = timezone(timedelta(hours=8), "CST")

BUCKETS = Path("/root/ombre-brain/buckets")

# 旧回声至少要多老才算「旧」。
#
# ⚠️ 原本设的 72 小时（3 天），实测偏短：一条「4 天前」的记忆拿了 0.905 的高分
# 挤进候选前三 —— 但 4 天前算不上翻旧账，那是「前几天的事」。
# 提到 7 天之后，能进候选的才真有「翻出来」的意味（2026-08-09 糖糖定）。
OLD_ECHO_MIN_AGE_HOURS = 168

# 旧回声的年龄曲线在这里取最大值。
# 不是越老越好也不是越新越好 —— 太新的还没沉淀，太老的已经远了。
OLD_ECHO_PEAK_DAYS = 14


# --------------------------------------------------------------- 读盘

def parse_frontmatter(path: Path) -> dict | None:
    """抠出 YAML frontmatter。只认我们实际用到的几种形态：

        key: value
        key:
        - item
        - item

    **不读正文** —— 选材只看元数据，正文是她的私事，没必要加载进内存。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---"):
        return None

    lines = text.split("\n")
    meta: dict = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
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
    return meta


def load_buckets() -> list[dict]:
    out = []
    for md in BUCKETS.rglob("*.md"):
        meta = parse_frontmatter(md)
        if not meta or not meta.get("id"):
            continue
        # type 字段有时缺，用所在目录兜底（buckets/dynamic/... → dynamic）
        if not meta.get("type"):
            try:
                meta["type"] = md.relative_to(BUCKETS).parts[0]
            except Exception:
                meta["type"] = "dynamic"
        out.append({"path": md, "meta": meta})
    return out


# --------------------------------------------------------------- 工具

def created_of(b: dict) -> datetime | None:
    """取创建时间。created 和 last_active 取较晚的那个（同 Haven）。"""
    best = None
    for key in ("created", "last_active"):
        raw = b["meta"].get(key)
        if not raw or not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        if best is None or dt > best:
            best = dt
    return best


def as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip().lower()]
    return []


def is_locked(meta: dict) -> bool:
    """钉选 / 保护 / 锚点的记忆**不参与做梦**。

    梦是流动的东西，不该拿「准则」去搅 —— 这是 Haven 那套里
    我觉得最讲究的一条设计。
    """
    for key in ("pinned", "protected", "anchor"):
        v = meta.get(key)
        if v is True or str(v).strip().lower() in ("true", "yes", "1"):
            return True
    return False


# --------------------------------------------------------------- 选材

def is_material(b: dict, start: datetime, end: datetime) -> bool:
    meta = b["meta"]
    created = created_of(b)
    if not created or not (start <= created <= end):
        return False
    t = str(meta.get("type", "")).lower()
    if t == "feel":
        # 感受只收「低语」—— 其余的 feel 是他自己的私密感受，不入梦
        return "whisper" in as_list(meta.get("tags"))
    if t in ("permanent", "archive", "archived"):
        return False
    if is_locked(meta):
        return False
    return True


def material_score(b: dict, now: datetime) -> float:
    """0.45×新鲜 + 0.30×情绪 + 0.20×重要 + 0.15×低语

    新鲜度权重最高、情绪唤醒度次之 —— 刚发生的、心里有波动的，
    最容易入梦。这个排序很像真的。
    """
    meta = b["meta"]
    created = created_of(b) or now
    age_h = max(0.0, (now - created).total_seconds() / 3600)
    recency = math.exp(-age_h / 24)                      # 一天半衰
    arousal = as_float(meta.get("arousal"), 0.3)
    importance = max(1.0, min(10.0, as_float(meta.get("importance"), 5))) / 10
    whisper = 0.15 if "whisper" in as_list(meta.get("tags")) else 0.0
    return 0.45 * recency + 0.30 * arousal + 0.20 * importance + whisper


def is_old_echo(b: dict, now: datetime, exclude: set[str]) -> bool:
    meta = b["meta"]
    if str(meta.get("id")) in exclude:
        return False
    created = created_of(b)
    if not created:
        return False
    if (now - created).total_seconds() / 3600 < OLD_ECHO_MIN_AGE_HOURS:
        return False
    t = str(meta.get("type", "")).lower()
    if t in ("feel", "permanent", "archive", "archived"):
        return False
    return not is_locked(meta)


def old_echo_score(b: dict, materials: list[dict], now: datetime) -> float:
    """0.30×共享标签 + 0.20×共享领域 + 0.25×重要 + 0.15×情绪 + 0.10×年龄曲线

    **一半的权重给了「和今天有没有呼应」** —— 它不是随机翻旧账，
    是在找回响：今天聊的事，勾起两周前某件相关的。
    """
    meta = b["meta"]
    m_tags = {t for m in materials for t in as_list(m["meta"].get("tags"))}
    m_doms = {d for m in materials for d in as_list(m["meta"].get("domain"))}

    shared_tags = len(set(as_list(meta.get("tags"))) & m_tags)
    shared_doms = len(set(as_list(meta.get("domain"))) & m_doms)
    importance = max(1.0, min(10.0, as_float(meta.get("importance"), 5))) / 10
    arousal = as_float(meta.get("arousal"), 0.3)

    created = created_of(b) or now
    age_days = max(0.0, (now - created).total_seconds() / 86400)
    age_curve = 1.0 / (1.0 + abs(age_days - OLD_ECHO_PEAK_DAYS) / 30.0)

    return (
        0.30 * min(1.0, shared_tags / 2.0)
        + 0.20 * min(1.0, shared_doms / 2.0)
        + 0.25 * importance
        + 0.15 * arousal
        + 0.10 * age_curve
    )


# --------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="Dream 选材（只挑不生成）")
    ap.add_argument("--date", help="按哪天算，YYYY-MM-DD，默认今天")
    ap.add_argument("--top", type=int, default=5, help="最多列几条素材")
    # ⚠️ 默认 3 天而不是 1 天：**不用每天做梦**（2026-08-09 糖糖定）。
    # 只看今天的话，很多天会因为没有新记忆而空转；三天一攒，
    # 素材够了才值得做一场梦。
    ap.add_argument("--window-days", type=int, default=3,
                    help="素材窗口天数，默认 3")
    args = ap.parse_args()

    now = datetime.now(LOCAL_TZ)
    if args.date:
        day = datetime.fromisoformat(args.date).replace(tzinfo=LOCAL_TZ)
        now = day.replace(hour=23, minute=59)
    end = now
    start = (now - timedelta(days=args.window_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    buckets = load_buckets()
    print("=" * 62)
    print("Dream 选材 · %s" % now.strftime("%Y-%m-%d %H:%M"))
    print("素材窗口：%s ~ %s" % (start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M")))
    print("库里一共 %d 条记忆" % len(buckets))
    print("=" * 62)

    materials = [b for b in buckets if is_material(b, start, end)]
    materials.sort(key=lambda b: material_score(b, now), reverse=True)

    if not materials:
        print("\n今天没有可入梦的新记忆 —— 今晚不做梦。")
        print("（这不是故障：钉选/固化/归档的记忆本来就不参与做梦）")
        return 0

    print("\n【今天的素材】%d 条，按分数取前 %d" % (len(materials), args.top))
    for i, b in enumerate(materials[:args.top], 1):
        m = b["meta"]
        c = created_of(b)
        print("  %d. [%.3f] %s" % (i, material_score(b, now), m.get("name", "?")))
        print("       %s | 重要度 %s | 唤醒 %s | %s" % (
            "/".join(as_list(m.get("domain"))) or "无域",
            m.get("importance", "?"), m.get("arousal", "?"),
            c.strftime("%m-%d %H:%M") if c else "?"))

    picked = materials[:args.top]
    exclude = {str(b["meta"].get("id")) for b in picked}
    echoes = [b for b in buckets if is_old_echo(b, now, exclude)]

    print("\n【两周前的回声】候选 %d 条" % len(echoes))
    if not echoes:
        print("  （没有够老的记忆可翻 —— 库还年轻）")
    else:
        echoes.sort(key=lambda b: old_echo_score(b, picked, now), reverse=True)
        for i, b in enumerate(echoes[:3], 1):
            m = b["meta"]
            c = created_of(b)
            age = (now - c).days if c else -1
            mark = "★ 选中" if i == 1 else "  备选"
            print("  %s [%.3f] %s" % (mark, old_echo_score(b, picked, now), m.get("name", "?")))
            print("       %d 天前 | %s | 重要度 %s" % (
                age, "/".join(as_list(m.get("domain"))) or "无域", m.get("importance", "?")))

    print("\n" + "=" * 62)
    print("以上就是今晚会被搅在一起的东西。现在只挑，不生成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
