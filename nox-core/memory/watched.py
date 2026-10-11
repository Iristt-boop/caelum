"""共影的剧情记忆（2026-10-11）。

糖糖 10-11：「看完一部片子后，Nox 没有整段片子的内容整理汇总，导致他知道看了什么，却不知道剧情。」
10-10 一起看《风吹半夏》第三集，他没有前两集的记忆，只能对着当前画面回话。

画面其实都在：每次换场 / 她暂停，bridge 把一段画面描述（常带字幕）发给他，作为【共影】消息
落在他的会话里。缺的是**看完一集没人把它收成「这集讲了什么」**。所以：

1. 一场看完（`SharedActivitiesSource` 第一次记下这场的 watching_session）→ 后台跑 `summarize_episode`：
   这场时间段里的画面 + 字幕 + 两个人的对话，加上网上**这一集**的分集剧情（糖糖 10-11 选 B），
   交给便宜模型整理成「剧情」和「我们一起看的」两份
2. 存两处，各管各的（糖糖 10-11 定）：
   - **World Model** `watched_episode`：完整档案，前情从这里**精确**取（按剧名 + 集数），不衰减
   - **Ombre**：只 hold「我们一起看的」—— 那是两个人的回忆，平时聊天要能被想起；
     网上的剧情**不进 OB**（外面的文字会跟真回忆抢检索位置）
3. 下次打开同一部剧（工牌入口 = 共影）→ `render_previously` 给他【前情】

🔴 不剧透：只查**看过的这一集**；网上资料对不上这部剧（片名不在里面 / 模型判不匹配）就只用画面。
🔴 编不出来的不编：画面断档的地方就是断档。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

TYPE = "watched_episode"
SOURCE = "cowatch"

#: 素材上限 —— 一集 40 多分钟，换场 10~20 次；每段画面描述原文几百字，截一截够用了
FRAME_CHARS = 320
LINE_CHARS = 160
MAX_FRAMES = 40
MAX_LINES = 40
SYNOPSIS_CHARS = 3000

#: 前情的总预算（字符）。最近一集给得多，越早越短
PREV_BUDGET = 900
PREV_LATEST = 420
PREV_OLDER = 180

_CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not s or any(c not in _CN_NUM and c not in "百" for c in s):
        return None
    if "百" in s:
        return None   # 一百多集的剧先不认，真遇到再说
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN_NUM[s[1]]
    if len(s) == 2 and s[1] == "十":
        return _CN_NUM[s[0]] * 10
    if len(s) == 3 and s[1] == "十":
        return _CN_NUM[s[0]] * 10 + _CN_NUM[s[2]]
    if len(s) == 1:
        return _CN_NUM[s]
    return None


@dataclass(frozen=True)
class Episode:
    series: str
    episode: int
    season: int = 1

    @property
    def label(self) -> str:
        return f"S{self.season:02d}E{self.episode:02d}" if self.season > 1 or self._en else f"第{self.episode}集"

    _en: bool = False


_EXT = re.compile(r"\.(mp4|mkv|avi|mov|flv|rmvb|webm|ts)$", re.I)
_NOISE = re.compile(r"[\(（\[【][^\)）\]】]*[\)）\]】]|\b(1080p|720p|2160p|4k|web-?dl|bluray|x26[45]|hevc|aac)\b.*", re.I)


def parse_title(title: str | None) -> Episode | None:
    """从文件名认出「哪部剧第几集」。认不出就 None —— 电影、`01.mp4` 这种不硬认。

    风吹半夏第三集.mp4 → 风吹半夏 / 3；摩登家庭S01E01 (AAC音轨).mkv → 摩登家庭 / S1 / E1
    """
    t = _EXT.sub("", (title or "").strip())
    t = _NOISE.sub("", t).strip(" ._-")
    if not t:
        return None
    m = re.search(r"^(.*?)[\s._-]*[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})", t)
    if m and m.group(1).strip(" ._-"):
        return Episode(m.group(1).strip(" ._-"), int(m.group(3)), int(m.group(2)), _en=True)
    m = re.search(r"^(.*?)[\s._-]*第([0-9零〇一二两三四五六七八九十百]+)[集话話]", t)
    if m and m.group(1).strip(" ._-"):
        n = _cn_to_int(m.group(2))
        if n:
            return Episode(m.group(1).strip(" ._-"), n)
    m = re.search(r"^(.*?)[\s._-]*(?:EP|E)(\d{1,3})$", t, re.I)
    if m and m.group(1).strip(" ._-"):
        return Episode(m.group(1).strip(" ._-"), int(m.group(2)), _en=True)
    return None


# ---------------------------------------------------------------- 素材

@dataclass
class Material:
    frames: list[str] = field(default_factory=list)   # 「[06:14] 画面描述（含字幕）」
    talk: list[str] = field(default_factory=list)     # 「她：…」「他：…」

    @property
    def empty(self) -> bool:
        return not self.frames


_FRAME_HEAD = re.compile(r"【共影[^】]*】[^\n]*?(?:第|在)\s*([\d.]+)\s*秒")


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def collect(rows: list[dict]) -> Material:
    """把这场时间段里的会话消息分成「画面」和「对话」。rows = `Store.messages_between` 的形状。"""
    from agent.llm import strip_stage_tags
    from data.origin import her_words

    mat = Material()
    for r in rows:
        text = r.get("text") or ""
        role = r.get("role")
        if role == "user" and text.startswith("【共影"):
            m = _FRAME_HEAD.search(text)
            sec = float(m.group(1)) if m else None
            desc = text.split("【当前画面】", 1)[1] if "【当前画面】" in text else ""
            ask = ""
            if text.startswith("【共影】") and "\n\n" in desc:
                # 她暂停问的那句在最后一段（bridge 拼的形状：…画面描述…\n\n能看到画面不）
                desc, ask = desc.rsplit("\n\n", 1)
            stamp = f"[{int(sec // 60):02d}:{int(sec % 60):02d}] " if sec is not None else ""
            if desc.strip():
                mat.frames.append(stamp + _clip(desc, FRAME_CHARS))
            if ask.strip():
                mat.talk.append("她：" + _clip(ask, LINE_CHARS))
        elif role == "user":
            her = her_words(r.get("session_id"), text)
            if her:
                mat.talk.append("她：" + _clip(her, LINE_CHARS))
        elif role == "assistant" and text.strip():
            said = strip_stage_tags(text) or ""
            said = re.sub(r"<心里>.*?</心里>", "", said, flags=re.S).replace("|||", " ")
            if said.strip() and not said.strip().startswith("[SKIP"):
                mat.talk.append("他：" + _clip(said, LINE_CHARS))
    mat.frames = mat.frames[:MAX_FRAMES]
    mat.talk = mat.talk[-MAX_LINES:]
    return mat


# ---------------------------------------------------------------- 网上的分集剧情

def search_synopsis(web_search: Callable[[dict], str] | None, ep: Episode) -> str | None:
    """只查**这一集**（不剧透）。结果里连剧名都没有 → 当没查到。"""
    if web_search is None:
        return None
    q = (f"{ep.series} 第{ep.season}季第{ep.episode}集 剧情" if ep.season > 1
         else f"{ep.series} 第{ep.episode}集 分集剧情")
    try:
        raw = web_search({"query": q}) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("共影剧情：网上查《%s》%s 失败，只用画面：%s", ep.series, ep.label, exc)
        return None
    if ep.series not in raw:
        logger.info("共影剧情：网上结果里没有《%s》，当没查到", ep.series)
        return None
    return raw[:SYNOPSIS_CHARS]


# ---------------------------------------------------------------- 整理

_PROMPT = """你在帮 Nox 整理他和糖糖刚一起看完的一集剧。输出 JSON，不要别的。

素材有三种：
1. 【画面】观影时按换场截下的画面描述（视觉模型写的，常带字幕）——这是他们**真的看到的**
2. 【对话】看的时候两个人说的话
3. 【网上的分集剧情】搜索结果，**可能对不上**这部剧/这一集

规则：
- 先判断【网上的分集剧情】是不是**这部剧的这一集**：剧名对得上，而且讲的人物/事件和画面能对上，才算 web_matches=true
- plot：这一集讲了什么（250~450 字，第三人称，写人物名字和关系变化）。web_matches 时以网上剧情为骨架、用画面补细节；
  不匹配就**只用画面**，看不出来的地方不要编，可以写「中间有一段没看清」
- 🔴 只写这一集。网上资料里出现后面几集的内容一律不要写（她还没看，不能剧透）
- moments：他们一起看时的 2~5 个瞬间，**只能来自【对话】**（她问了什么、笑了什么、他说了什么），每条一句话；没有就空数组
- characters：这一集出现的主要人物名（最多 6 个）

{{"web_matches": true/false, "plot": "...", "moments": ["..."], "characters": ["..."]}}"""


def summarize(adapter: Any, ep: Episode, mat: Material, synopsis: str | None) -> dict | None:
    from agent.llm import Message

    body = "\n".join([
        f"剧名：《{ep.series}》{ep.label}",
        "",
        "【画面】", *(mat.frames or ["（没有）"]),
        "",
        "【对话】", *(mat.talk or ["（没有）"]),
        "",
        "【网上的分集剧情】", synopsis or "（没查到）",
    ])
    try:
        # 🔴 会思考的模型 reasoning 和正文抢 max_tokens（记忆 reasoning-tokens-eat-max-tokens）—— 给够
        turn = adapter.complete([Message(role="user", text=body)], tools=[],
                                system=_PROMPT, depth="low", max_tokens=4000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("共影剧情：整理调用失败：%s: %s", type(exc).__name__, exc)
        return None
    raw = getattr(turn, "text", "") or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not str(d.get("plot") or "").strip():
        logger.warning("共影剧情：整理结果不是可用的 JSON：%.120s", raw)
        return None
    return {
        "web_matches": bool(d.get("web_matches")) and synopsis is not None,
        "plot": str(d["plot"]).strip()[:900],
        "moments": [str(x).strip()[:120] for x in (d.get("moments") or []) if str(x).strip()][:5],
        "characters": [str(x).strip()[:20] for x in (d.get("characters") or []) if str(x).strip()][:6],
    }


# ---------------------------------------------------------------- 一场看完：整条流水线

def summarize_episode(
    *,
    title: str,
    started: datetime,
    ended: datetime,
    watch_id: str,
    store: Any,
    world: Any,
    adapter: Any,
    web_search: Callable[[dict], str] | None = None,
    ob: Any = None,
) -> dict | None:
    """一场共影 → watched_episode（+ OB 里一条回忆）。认不出是剧 / 没画面 → None，不硬写。"""
    ep = parse_title(title)
    if ep is None:
        logger.info("共影剧情：《%s》认不出是哪部剧第几集，不整理", title)
        return None
    utc = timezone.utc
    rows = store.messages_between(
        (started - timedelta(minutes=2)).astimezone(utc).isoformat(),
        (ended + timedelta(minutes=5)).astimezone(utc).isoformat(),
    )
    mat = collect(rows)
    if mat.empty:
        logger.info("共影剧情：《%s》%s 这场没有画面记录，不整理", ep.series, ep.label)
        return None
    synopsis = search_synopsis(web_search, ep)
    got = summarize(adapter, ep, mat, synopsis)
    if got is None:
        return None
    observed = {
        "series": ep.series, "season": ep.season, "episode": ep.episode, "label": ep.label,
        "title": title, "watch_id": watch_id,
        "plot": got["plot"], "moments": got["moments"], "characters": got["characters"],
        # 来源要写清楚：哪些是我们看到的，哪些是网上的
        "sources": ["画面"] + (["网上分集剧情"] if got["web_matches"] else []),
        "frames": len(mat.frames),
    }
    obs = world.observe(SOURCE, TYPE, observed, ended.astimezone(utc),
                        dedup_key=f"{TYPE}/{watch_id}")
    if obs is None:
        logger.info("共影剧情：%s 这场已经整理过了", watch_id)
        return None
    logger.info("共影剧情：记下《%s》%s（%d 段画面，%s）", ep.series, ep.label,
                len(mat.frames), "参考了网上剧情" if got["web_matches"] else "只用画面")
    if ob is not None and got["moments"]:
        from temporal import CST   # 北京时间只在 temporal 定义（边界法则 R9）
        day = ended.astimezone(CST).strftime("%Y-%m-%d")
        content = (f"{day} 和糖糖一起看《{ep.series}》{ep.label}。一起看时：" + "；".join(got["moments"]))
        try:
            r = ob.hold(content, tags=f"共影,{ep.series}", importance=5)
            if not getattr(r, "ok", True):
                logger.warning("共影剧情：OB 没存上：%s", getattr(r, "error", ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("共影剧情：OB hold 失败（World Model 那份在）：%s", exc)
    return observed


# ---------------------------------------------------------------- 前情

def previously(world: Any, ep: Episode, limit: int = 200) -> list[dict]:
    """同一部剧、在这一集之前看过的（按集数排；同一集看过几次取最新那份）。"""
    if world is None:
        return []
    try:
        items = world.query(TYPE, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("共影剧情：读前情失败：%s", exc)
        return []
    best: dict[tuple[int, int], dict] = {}
    for e in reversed(items):        # query 新的在前 → 倒过来，后写的覆盖先写的
        raw = getattr(e, "raw", None) or {}
        if raw.get("series") != ep.series:
            continue
        key = (int(raw.get("season") or 1), int(raw.get("episode") or 0))
        if key < (ep.season, ep.episode):
            best[key] = raw
    return [best[k] for k in sorted(best)]


def render_previously(ep: Episode, items: list[dict]) -> str:
    if not items:
        return ""
    lines = [f"【前情】《{ep.series}》你们已经一起看到{ep.label}之前："]
    used = len(lines[0])
    for i, raw in enumerate(reversed(items)):      # 从最近一集往前写，预算花在最近的上
        cap = PREV_LATEST if i == 0 else PREV_OLDER
        line = f"{raw.get('label') or '第%s集' % raw.get('episode')}：{_clip(raw.get('plot', ''), cap)}"
        if used + len(line) > PREV_BUDGET:
            lines.insert(1, "（更早的几集省略了）")
            break
        lines.insert(1, line)
        used += len(line)
    return "\n".join(lines)
