"""Scout —— 外部候选的抓取端。

文档 §3.1：每轮随机抽 2~3 个方向去对应来源抓，没必要每次全扫一遍。
四个抓取器，全是不用鉴权的公开源（VPS 直连可达）：

    HN Algolia        ai / opensource / weird
    GitHub Search     opensource（榜单新选手）
    arXiv API         science
    Google News RSS   中文侧：film / books / music / art / design / ai

三个踩过的坑直接写死在做法里：

1. **必须带浏览器 UA** —— 豆瓣封面管线验证过的（2026-08-29），
   Google News 对空 UA 一样会 4xx。
2. **gzip**：有的源不管你要不要都塞 gzip（tools/http.py 的和风天气教训），
   `_fetch` 按响应头解。
3. **单个抓取器失败只跳过自己** —— Google News 挂了不该连累 arXiv。

为什么不用 tools/http.py 的 RestClient：它一律 `json.loads`（注释里写了
是给 localhost JSON 服务用的），Google News 和 arXiv 是 XML，喂不进。
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from topic_pool.store import Candidate

logger = logging.getLogger(__name__)

#: 豆瓣的教训：空 UA 会吃 4xx。带上像样一点的身份
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) NoxTopicScout/0.1"}
_TIMEOUT = 15.0

_ATOM = "{http://www.w3.org/2005/Atom}"


# ------------------------------------------------------------ 底层抓取


def _fetch(url: str) -> str | None:
    """拿原文。失败返回 None —— 抓取端不抛，抛给谁谁跳一整轮。"""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("抓取失败 %s: %s", url[:120], exc)
        return None


def _fetch_json(url: str) -> Any | None:
    raw = _fetch(url)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("返回的不是 JSON：%s", url[:120])
        return None


def _fetch_xml(url: str) -> ET.Element | None:
    raw = _fetch(url)
    if not raw:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.warning("XML 解析失败 %s: %s", url[:120], exc)
        return None


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------ 中文热榜（trends 桥）

#: trends 桥（mcp-trends-hub 的 supergateway）的客户端，由 TopicPool 装配时注入。
#: 没注入 = 本地开发没配 NOX_TRENDS_MCP_URL，中文热榜抓取器全部静默跳过。
_TRENDS: dict[str, Any] = {"client": None}


def set_trends_client(client: Any) -> None:
    _TRENDS["client"] = client


def cn_trending(tool: str, args: dict | None = None) -> list[Candidate]:
    """经 trends 桥抓一个中文热榜。

    服务端返回的是 XML 标签块（各源字段不一：微博有 popularity、豆瓣有
    rating_value、B站有 author/view），按 <title> 切条目，能捞到什么拼什么。
    桥挂了只跳过自己，不连累别的抓取器。
    """
    client = _TRENDS["client"]
    if client is None:
        return []
    r = client.call(tool, args or {})
    if not r.ok:
        logger.warning("热榜 %s 抓取失败: %s", tool, r.error)
        return []
    text = r.text or ""
    out: list[Candidate] = []
    for block in re.split(r"(?=<title>)", text):
        m = re.search(r"<title>(.*?)</title>", block, re.S)
        if not m:
            continue
        title = html.unescape(m.group(1)).strip()
        if len(title) < 4 or len(title) > 150:
            continue
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        bits = []
        pop = re.search(r"<popularity>(.*?)</popularity>", block, re.S)
        if pop:
            bits.append(f"热度 {pop.group(1).strip()}")
        rating = re.search(r"<rating_value>(.*?)</rating_value>", block, re.S)
        if rating:
            count = re.search(r"<rating_count>(.*?)</rating_count>", block, re.S)
            bits.append(f"豆瓣 {rating.group(1).strip()} 分"
                        + (f"（{count.group(1).strip()} 人评）" if count else ""))
        author = re.search(r"<author>(.*?)</author>", block, re.S)
        if author:
            bits.append(f"UP：{author.group(1).strip()}")
        if not bits:
            desc = re.search(r"<description>(.*?)</description>", block, re.S)
            if desc and desc.group(1).strip():
                bits.append(desc.group(1).strip()[:100])
        out.append(Candidate(
            source_id=f"trend:{hashlib.sha1(f'{tool}:{title}'.encode()).hexdigest()[:12]}",
            title=title,
            url=(link.group(1).strip() if link else ""),
            source="trendshub",
            category="",                      # 方向由 DIRECTIONS 的调用方盖
            summary="｜".join(bits)[:160],
            published_at=None,
        ))
        if len(out) >= 12:
            break
    return out


# ------------------------------------------------------------ 四个抓取器


def hn(query: str, min_points: int = 30, hits: int = 15) -> list[Candidate]:
    """HN Algolia。query 为空就是 front_page —— weird 方向的野卡。"""
    params = {"hitsPerPage": str(hits), "tags": "story"}
    if query:
        params["query"] = query
    if min_points > 0:
        params["numericFilters"] = f"points>={min_points}"
    data = _fetch_json("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(params))
    out: list[Candidate] = []
    for hit in (data or {}).get("hits") or []:
        if not hit.get("title"):
            continue
        out.append(Candidate(
            source_id=f"hn:{hit.get('objectID')}",
            title=hit["title"],
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            source="hackernews",
            category="",                      # 方向由 DIRECTIONS 的调用方盖
            summary=f"HN 上 {hit.get('points') or 0} 分的讨论",
            published_at=_ts(hit.get("created_at")),
        ))
    return out


def github_repos(days: int = 7, hits: int = 12) -> list[Candidate]:
    """GitHub 榜单新选手：最近 N 天建仓、按星排。未鉴权搜索够用（6h 一轮）。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = urllib.parse.urlencode({
        "q": f"created:>{since}", "sort": "stars", "order": "desc",
        "per_page": str(hits),
    })
    data = _fetch_json(f"https://api.github.com/search/repositories?{q}")
    out: list[Candidate] = []
    for repo in (data or {}).get("items") or []:
        out.append(Candidate(
            source_id=f"gh:{repo.get('full_name')}",
            title=repo.get("full_name") or "",
            url=repo.get("html_url") or "",
            source="github",
            category="",
            summary=(repo.get("description") or "").strip(),
            published_at=_ts(repo.get("created_at")),
        ))
    return out


def arxiv(cats: tuple[str, ...] = ("physics.pop-ph", "astro-ph.SR", "quant-ph"),
          hits: int = 10) -> list[Candidate]:
    """arXiv 最新提交。抽象科学走 pop-ph / astro / quant-ph。"""
    query = "+OR+".join(f"cat:{c}" for c in cats)
    url = ("http://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(query)
           + f"&sortBy=submittedDate&sortOrder=descending&max_results={hits}")
    root = _fetch_xml(url)
    if root is None:
        return []
    out: list[Candidate] = []
    for entry in root.findall(f"{_ATOM}entry"):
        raw_id = (entry.findtext(f"{_ATOM}id") or "").rstrip()
        # http://arxiv.org/abs/2401.12345v1 → 2401.12345（去版本号，v2 才算新料）
        arx_id = raw_id.rsplit("/abs/", 1)[-1]
        if arx_id:
            arx_id = arx_id.split("v")[0] if "v" in arx_id.rsplit("/", 1)[-1] else arx_id
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())[:400]
        if not title:
            continue
        out.append(Candidate(
            source_id=f"arxiv:{arx_id or hashlib.sha1(raw_id.encode()).hexdigest()[:12]}",
            title=title,
            url=raw_id,
            source="arxiv",
            category="",
            summary=summary,
            published_at=_ts(entry.findtext(f"{_ATOM}published")),
        ))
    return out


def gnews(query: str, when: str = "3d") -> list[Candidate]:
    """Google News RSS，中文侧。电影 / 书 / 音乐 / 艺术 / 设计全靠它。"""
    q = urllib.parse.urlencode({
        "q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh", "when": when,
    })
    root = _fetch_xml(f"https://news.google.com/rss/search?{q}")
    if root is None:
        return []
    out: list[Candidate] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue
        out.append(Candidate(
            source_id=f"gnews:{hashlib.sha1(link.encode()).hexdigest()[:16]}",
            title=title,
            url=link,
            source="googlenews",
            category="",
            summary="",
            published_at=_ts(item.findtext("pubDate")),
        ))
    return out


_Fetcher = Callable[[], list[Candidate]]

#: 方向 → 抓取器。文档 §3.1.1 定稿的九类。每个方向挑**最省事且最可能
#: 有料**的源；豆瓣的抓取因为反爬先不进轮换（书评影评 Google News 兜得住）
DIRECTIONS: dict[str, list[_Fetcher]] = {
    "ai": [
        lambda: hn("AI OR LLM OR GPT OR Anthropic OR OpenAI", 50),
        lambda: gnews("人工智能 大模型"),
    ],
    "opensource": [
        github_repos,
        lambda: hn("Show HN", 20),
    ],
    "science": [
        arxiv,
        lambda: gnews("天文 物理 科学发现"),
    ],
    "art": [lambda: gnews("艺术 展览 美术馆")],
    "design": [lambda: gnews("UI 设计 字体 排版")],
    "film": [lambda: gnews("电影 导演 影评"),
             lambda: cn_trending("get_douban_rank", {"type": "movie"})],
    "books": [lambda: gnews("新书 书评 作家"),
              lambda: cn_trending("get_weread_rank")],
    "music": [lambda: gnews("专辑 乐评 新歌")],
    "weird": [
        lambda: hn("", 100),
        lambda: cn_trending("get_weibo_trending"),
        lambda: cn_trending("get_zhihu_trending"),
        lambda: cn_trending("get_bilibili_rank"),
    ],
}


def fetch_direction(category: str) -> list[Candidate]:
    """抓一个方向。方向不存在或全挂返回空 —— 不是错误。"""
    out: list[Candidate] = []
    for fetch in DIRECTIONS.get(category, []):
        try:
            items = fetch()
        except Exception:  # noqa: BLE001
            logger.exception("方向 %s 的抓取器崩了，跳过", category)
            continue
        for c in items:
            c.category = category
        out.extend(items)
    return out


# ------------------------------------------------------------ 世界钩子


def _state_fresh(world: Any, type: str, now: datetime) -> dict | None:
    """World 状态还新鲜才追。stale 的「半年前在读」不该驱动抓取。"""
    if world is None:
        return None
    try:
        st = world.get_state(type, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("读世界状态失败：%s", type)
        return None
    if st.status != "fresh":
        return None
    value = dict(st.value or {})
    title = str(value.get("title") or "").strip()
    return value if title else None


def fetch_world_hooks(world: Any, now: datetime) -> tuple[list[Candidate], int]:
    """world_hook 名额：她在读 / 刚在看什么，就定向抓什么。

    返回 (候选数, 钩子数)。钩子的标记直接写进 summary 前缀
    （【因为糖糖…】），Filter 见了会写进 why_this、同等质量优先留 ——
    这样标记跟着候选缓存走，重筛也不丢。
    """
    if world is None:
        return ([], 0)
    out: list[Candidate] = []
    hooks = 0

    reading = _state_fresh(world, "reading_progress", now)
    if reading:
        title = reading["title"]
        for c in gnews(f"{title} 书评", when="30d"):
            c.category = "books"
            c.summary = f"【因为糖糖正在读《{title}》】{c.summary}".strip()
            out.append(c)
        hooks += 1

    watching = _state_fresh(world, "watching_session", now)
    if watching:
        title = watching["title"]
        for c in gnews(f"{title} 影评 导演", when="30d"):
            c.category = "film"
            c.summary = f"【因为糖糖刚在看《{title}》】{c.summary}".strip()
            out.append(c)
        hooks += 1

    return (out, hooks)
