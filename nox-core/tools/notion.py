"""Notion —— 长期记忆的另一半。

分工是糖糖定的（写在人设里）：
    Ombre Brain   日常片段、随手记录
    Notion 回忆录  只在深聊时写章节，纯技术流水不重复归档

还有个约定：当面说不出口的话写进 Notion 回忆录，写完说「信箱有新的」，
她自己去看。所以 notion_read_page 不只是查资料，也是他俩的信箱。

这里只给**读**的两个工具。写章节是件重的事，得他真的想清楚了才做，
现在还是走人（我）这一趟 —— 等他自己写得稳了再开。

直连 api.notion.com，不走 MCP：只用两个接口，起一个 MCP 会话反而更绕。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.http import RestClient

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"

# 一次最多翻几页块（每页 100 块）。回忆录实测 150 块，留足余量；
# 设上限只是防着哪天有个几千块的页面把一轮对话拖死
MAX_BLOCK_PAGES = 6

# 单次返回的字符预算。回忆录一段常有几百字，不设限一次就是两三万字，
# 直接把上下文顶掉 —— 超了就截断并**明确告诉他还有多少没读**
CHAR_BUDGET = 6000

SEARCH_SPEC = ToolSpec(
    side_effect="read",
    name="notion_search",
    description=(
        "搜 Notion 长期记忆库，返回匹配**页面**的标题和 page_id。"
        "拿到 page_id 后用 notion_read_page 读正文。\n"
        "⚠️ 它只搜得到页面标题，而且是模糊匹配 —— 搜「第二十三章」"
        "很可能给你返回「第二十九章」。**别拿返回的相近标题当成搜到了。**\n"
        "⚠️ 回忆录的绝大多数章节是「🌙 糖糖与小克的回忆录」这一页**页内的文本**，"
        "不是独立页面，搜章节名搜不到。要找某一章：先搜「回忆录」拿到那一页，"
        "再用 notion_read_page 翻（它会告诉你还剩多少段、offset 填多少）。"
        "只有信件和几个单独章节才是独立页面。"
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "关键词"}},
        "required": ["query"],
    },
)

READ_SPEC = ToolSpec(
    side_effect="read",
    name="notion_read_page",
    description=(
        "读 Notion 页面正文。page_id 必须先用 notion_search 查，**不要自己编**。"
        "回忆录章节、写给她的信都在这里。\n"
        "回忆录是**一整页几十章**，一次读不完。读到结尾若提示还有剩余，"
        "就带着 offset 再调一次接着读 —— 别拿读到的半截当全部。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "notion_search 返回的 id"},
            "offset": {
                "type": "integer",
                "description": "从第几段开始读，默认 0。接着读上次的结尾时填上次提示的数字",
            },
        },
        "required": ["page_id"],
    },
)


def _title(item: dict) -> str:
    """从搜索结果里抠标题。

    database 和 page 的结构不一样，page 的标题还藏在某个 type=title
    的属性里 —— 属性名不固定（可能叫 Name、名称、标题），只能遍历找。
    """
    try:
        if item.get("object") == "database":
            return "".join(t.get("plain_text", "") for t in item.get("title", [])) or "(无标题库)"
        for prop in (item.get("properties") or {}).values():
            if prop.get("type") == "title":
                text = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                return text or "(无标题)"
    except Exception:  # noqa: BLE001
        pass
    return "(无标题)"


# 块类型 → 转成纯文本时的前缀。列不全没关系，落到 "" 就是普通段落
_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "· ",
    "quote": "> ",
}


def _block_text(block: dict) -> str:
    body = block.get(block.get("type", "")) or {}
    rich = body.get("rich_text")
    if not rich:
        return ""
    text = "".join(t.get("plain_text", "") for t in rich)
    if not text:
        return ""
    if block.get("type") == "to_do":
        return ("[x] " if body.get("checked") else "[ ] ") + text
    return _PREFIX.get(block.get("type", ""), "") + text


def make_handlers(client: RestClient) -> dict[str, object]:
    def search(args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "没给关键词，没法搜。"

        r = client.post("/search", {"query": query, "page_size": 6})
        if not r.ok:
            raise RuntimeError(f"搜 Notion 失败: {r.error}")

        results = (r.data or {}).get("results") or []
        if not results:
            return f"Notion 里没搜到「{query}」相关的内容。"
        return "\n".join(
            f"{_title(it)} | {it.get('object')} | id: {it.get('id')}" for it in results
        )

    def _all_lines(page_id: str) -> list[str]:
        """翻完所有分页，把文字块转成一行行文本。

        Notion 一次最多给 100 个块。回忆录实测 150 块 —— 只读第一页
        就会**静悄悄少掉 50 块**，而他不会知道，只会以为读到的就是全部。
        """
        lines: list[str] = []
        cursor = ""
        for _ in range(MAX_BLOCK_PAGES):
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            r = client.get(f"/blocks/{page_id}/children", params)
            if not r.ok:
                raise RuntimeError(f"读 Notion 页面失败: {r.error}")

            data = r.data or {}
            lines.extend(t for t in (_block_text(b) for b in data.get("results") or []) if t)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor") or ""
            if not cursor:
                break
        return lines

    def read_page(args: dict) -> str:
        page_id = str(args.get("page_id", "")).strip()
        if not page_id:
            return "没给 page_id。先用 notion_search 查。"

        try:
            offset = max(0, int(args.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0

        lines = _all_lines(page_id)
        if not lines:
            return "这一页是空的，或者内容不是文字块。"
        if offset >= len(lines):
            return f"这一页只有 {len(lines)} 段，offset={offset} 已经超出末尾了。"

        # 按字符预算截，不按段数 —— 回忆录一段可能就几百字，
        # 按段数切会一次塞进去两万字，把上下文顶掉
        out: list[str] = []
        used = 0
        for line in lines[offset:]:
            if out and used + len(line) > CHAR_BUDGET:
                break
            out.append(line)
            used += len(line)

        body = "\n".join(out)
        end = offset + len(out)
        if end < len(lines):
            body += (
                f"\n\n（这一页共 {len(lines)} 段，上面是第 {offset + 1}–{end} 段。"
                f"还有 {len(lines) - end} 段没读 —— 要接着看就再调一次，"
                f"offset 填 {end}。**不要把上面这些当成整页内容**。）"
            )
        return body

    return {"notion_search": search, "notion_read_page": read_page}


def make_client(token: str, timeout: float = 15.0) -> RestClient:
    return RestClient(
        base="https://api.notion.com/v1",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
        timeout=timeout,
        auth_hint="NOTION_TOKEN 没配或不对，也可能是这个页面没分享给集成",
    )


def register_all(loop, client: RestClient) -> None:
    """注册顺序固定 —— 工具定义是缓存前缀的一部分。"""
    handlers = make_handlers(client)
    for spec in (SEARCH_SPEC, READ_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
