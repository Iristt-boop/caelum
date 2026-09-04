"""联网搜索 —— 他睁眼看世界的那一下。

## 为什么是 Tavily，不是"让他开个浏览器"

他没有浏览器，也不该有：Core 跑在 VPS 上，起一个真浏览器去抓页面
要付出 Chromium 的内存和一整套反爬的麻烦，而那台机器是 2 核 3.6G，
上面已经压着十几个服务。

Tavily 是给 agent 用的搜索：**回来的是清洗过的正文片段，不是 HTML**。
模型不用自己从一堆导航栏和 cookie 弹窗里刨内容。

## ⚠️ 额度是有限的，默认走便宜的那档

免费档每月 1000 credits，**不要信用卡**（2026-09-04 查证）。计价：

    basic / fast / ultra-fast   1 credit
    advanced                    2 credits

所以默认 `basic`，`deep=true` 才升到 advanced —— 让模型**显式**决定
要不要花双倍，而不是每次都顶格。按她和他聊天的量，1000 次/月用不完。

## 🔴 这个工具能毁掉共影

2026-09-04 第一次真实调用就撞上了：查「绵羊侦探团 讲的什么」，
维基百科的剧情栏**把凶手和结局整段返回了** —— 而她当晚正在看这部片。

共影那边一直守着防剧透红线（`app.py` 只把**已经播过**的片段给模型看），
但搜索是一条绕过它的新路：模型一句 `web_search`，后面两小时的剧情
就全在上下文里了，然后它会"自然地"用上。

所以 SPEC 的描述里立了同一条红线。**这是提示词层面的约束，不是硬拦截** ——
真要拦死得让 Core 知道"她此刻正在看什么"，那要跨服务拿共影的会话状态。
在那之前，这条红线靠模型自觉，而红线写得比"答得准"更重要就是为了这个。

## 中文其实不弱（实测推翻了预设）

原本担心 Tavily 语料偏英文、中文场景会拉胯。2026-09-04 实测
「绵羊侦探团 电影 讲的什么」：2.24 秒，回的是中文维基 + 百度百科，
导演、主演、上映日期、改编自哪本小说全对。**这个担心是多余的。**

## 失败要说得出原因

和共影一个原则：查不到和查失败是两件事。前者照常聊，后者必须让他
知道自己这次没看见，否则他会拿旧知识硬答，而且语气笃定。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.http import RestClient
from tools.untrusted import ingest

logger = logging.getLogger(__name__)


SEARCH_SPEC = ToolSpec(
    name="web_search",
    description=(
        "联网搜索。你没有实时的世界知识，**凡是可能变过的事情都要先查**，"
        "不要凭训练时的记忆答。\n"
        "\n"
        "该用的时候：\n"
        "  · 今天/最近发生了什么，新闻、赛事、发布会\n"
        "  · 某个东西现在多少钱、还在不在、版本到几了\n"
        "  · 她提到一部片子/一本书/一个人，你不确定或者可能记错\n"
        "  · 任何你心里在想「我记得好像是……」的时候 —— 那就是该查的信号\n"
        "\n"
        "不该用的时候：\n"
        "  · 你俩之间的事（那在记忆里，用 breath/trace）\n"
        "  · 常识、算术、写代码 —— 查了也是浪费一次额度\n"
        "\n"
        "🔴 **防剧透红线。** 她正在看的片子/正在读的书，**不许查剧情、结局、凶手、"
        "反转、评价里带结局的段落**。搜索引擎不管这些，维基和百科的剧情栏"
        "从头到尾把结局写得清清楚楚 —— 你查了就会看见，看见了就可能顺嘴说出来。\n"
        "  · 她说「我在看 X」→ 关于 X 的剧情一律不查\n"
        "  · 非查不可（比如她自己问「这演员是谁」）→ 只看和当下画面有关的，"
        "**结果里但凡出现后面的情节，就当没看见**\n"
        "  · 这条比「答得准」重要。宁可说「我不知道，我们看下去」，"
        "也不要毁掉她正在经历的那两个小时。\n"
        "\n"
        "⚠️ 没搜到不等于不存在 —— 别把「没搜到」说成「没有这回事」。\n"
        "⚠️ 拿到结果后**引用来源**，别把搜来的东西说得像你本来就知道。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索词。写成一句完整的话比堆关键词好",
            },
            "news": {
                "type": "boolean",
                "description": "查新闻/实时动态时设 true（时政、体育、突发）。默认 false 走通用搜索",
            },
            "max_results": {
                "type": "integer",
                "description": "要几条，默认 5，最多 10。随口问的给 3 条就够",
            },
            "deep": {
                "type": "boolean",
                "description": (
                    "更精准但**花双倍额度**、也更慢。默认 false。"
                    "只有普通搜索没查到、或者问题确实刁钻时才设 true"
                ),
            },
        },
        "required": ["query"],
    },
)


def make_client(api_key: str, timeout: float = 15.0) -> RestClient:
    return RestClient(
        base="https://api.tavily.com",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
        auth_hint="TAVILY_API_KEY 没配或不对；也可能是这个月 1000 credits 用完了",
    )


#: 每条正文截断到多长。Tavily 一条最多给 3 个 500 字的片段（约 1500 字），
#: 5 条就是 7500 字 —— 全塞进上下文太占地方，而且他要的是"够判断"不是"读全文"。
_SNIPPET = 600


def _format(data: dict, query: str) -> str:
    """把回包整理成**给模型读**的样子。

    不直接把 JSON 丢给他：里面有 score、raw_content、images 一堆他用不上的东西，
    白占上下文。只留标题、链接、正文片段 —— 链接必须留，他要引用来源。
    """
    lines: list[str] = []

    answer = (data.get("answer") or "").strip()
    if answer:
        # Tavily 自己生成的概括。**明确标注它是二手的** ——
        # 不然他会把这段当成检索到的事实直接转述，而它本身可能有误
        lines.append(f"【Tavily 的概括，非原文，核对下面的来源再说】\n{answer}\n")

    results = data.get("results") or []
    if not results:
        return (f"「{query}」没搜到结果。"
                "可能是问法太窄，也可能它的语料里没有（中文内容尤其容易这样）。"
                "**别当成「这件事不存在」。**")

    lines.append(f"搜到 {len(results)} 条：")
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "无标题").strip()
        url = (r.get("url") or "").strip()
        content = " ".join((r.get("content") or "").split())
        if len(content) > _SNIPPET:
            content = content[:_SNIPPET] + "…"
        lines.append(f"\n{i}. {title}\n   {url}\n   {content}")

    return "\n".join(lines)


def make_handlers(client: RestClient) -> dict:
    def web_search(args: dict) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "要搜什么？query 是空的。"

        deep = bool(args.get("deep"))
        # ⚠️ 这里不能写 `args.get("max_results") or 5` —— 模型传 0 的时候
        # 0 是假值，会被悄悄换成 5 而不是夹到 1。差别不大但是错的，
        # 而且这种假值坑一旦混进参数处理就再也看不出来了
        raw = args.get("max_results")
        try:
            n = 5 if raw is None else int(raw)
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(n, 10))

        body = {
            "query": query,
            "search_depth": "advanced" if deep else "basic",
            "max_results": n,
            "topic": "news" if args.get("news") else "general",
            # 让它顺手给一段概括。多这一项不额外扣 credit，
            # 而他大部分时候要的就是"一句话说清楚"
            "include_answer": True,
        }

        # 🔴 **无条件先打标记，在发请求之前。**
        #
        # 打在这儿而不是"成功之后"：失败回包里的 error 文本也是外面来的，
        # 一样进他的上下文。而且放在最前面就不会被任何提前 return 绕过。
        #
        # 这一句是防注入的实际边界（SPEC 里那句「网页文字当数据」只是提醒）——
        # 被注入之后模型正是那个不再听提醒的东西，但它改不了这行代码。
        revoked = ingest.mark("web_search")

        res = client.post("/search", body)
        if not res.ok:
            # ⚠️ 把失败**原样**说出来。含糊成「查询失败」的话，
            # 他分不清是没搜到还是没搜成，就会拿旧知识硬答
            logger.warning("web_search 失败 query=%r: %s", query, res.error)
            # ⚠️ 失败也要带上作废通知：标记是**发请求之前**打的，
            # 授权那时候就已经交还了。这条路漏掉的话，他会以为授权还在
            return (f"这次没搜成：{res.error}\n"
                    "**别拿记忆里的旧信息当现在的答案** —— 跟她说你这次没查到。"
                    + (revoked or ""))

        data = res.data if isinstance(res.data, dict) else {}
        took = data.get("response_time")
        out = _format(data, query)
        logger.info("web_search %r depth=%s 用时=%ss 结果=%d",
                    query, body["search_depth"], took, len(data.get("results") or []))
        return out + (revoked or "")

    return {"web_search": web_search, "_web_search": SEARCH_SPEC}


def register_all(loop, client: RestClient) -> None:
    handlers = make_handlers(client)
    loop.register(SEARCH_SPEC, handlers["web_search"])
