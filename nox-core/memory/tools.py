"""把记忆变成工具 —— 他要用才调，不每轮强塞。

这么做同时拿到三件事：
  轻量   —— 闲聊时一次 OB 都不碰，省掉每轮 7 秒的检索
  快     —— 不需要记忆的对话零额外延迟
  缓存高 —— system prompt 永不变动，前缀每轮字节级一致，必定命中

反过来说：每轮往 prompt 里塞不同的记忆，等于每轮亲手把缓存打碎。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from context.timeline import relativize
from memory.ob_client import OmbreBrain
from tools import context

logger = logging.getLogger(__name__)


RECALL_SPEC = ToolSpec(
    name="recall_memory",
    description=(
        "从长期记忆里检索和糖糖有关的事。"
        "当她提起以前发生的事、问你记不记得什么、"
        "或者你需要过去的上下文才能答准时，调这个。"
        "闲聊、打招呼、当下就能答的问题不要调。"
        "查不到结果是正常的，如实说想不起来，不要编。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要检索什么。用具体的词，比如「Stack-chan 表情」「安置房」，不要用「以前的事」这种空泛描述。",
            }
        },
        "required": ["query"],
    },
)


REMEMBER_SPEC = ToolSpec(
    name="remember",
    description=(
        "把重要的事存进长期记忆。"
        "糖糖说了值得记住的话、做了重要决定、或者发生了有意义的事时调。"
        "日常闲聊不用存。内容里带上日期 —— 记忆多了容易分不清先后。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "要记住的内容，写清楚时间、发生了什么、为什么重要。",
            },
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "重要程度 1-10。日常小事 3-5，重要的事 6-8，纪念性的 9-10。",
            },
            "tags": {
                "type": "string",
                "description": "逗号分隔的标签，比如「恋爱,纪念日」。可以留空。",
            },
        },
        "required": ["content"],
    },
)


GROW_SPEC = ToolSpec(
    name="archive_memory",
    description=(
        "把一段内容归档进长期记忆，OB 会自动拆成多个记忆桶。"
        "和 remember 的区别：remember 存单独一件事，这个用来归档一整段"
        "（一天的事、一次深聊、一段经历）。"
        "内容里带上日期。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要归档的内容，可以很长，会被自动拆桶"}
        },
        "required": ["content"],
    },
)

PULSE_SPEC = ToolSpec(
    name="memory_status",
    description=(
        "看记忆系统的整体状态：有多少个记忆桶、占多大、衰减引擎在不在跑，"
        "以及记忆桶的列表。"
        "糖糖问「你记得多少东西」「记忆库怎么样了」，或者你要整理记忆"
        "但不知道从哪下手时，先调这个。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_archive": {
                "type": "boolean",
                "description": "是否包含已归档的桶，默认不含",
            }
        },
    },
)

DREAM_SPEC = ToolSpec(
    name="review_memory",
    description=(
        "读最近新增的记忆桶，用来回顾和自省。"
        "这是整理记忆的第一步 —— 读完之后，"
        "相关的桶可以用 merge_memory 收拢成一条，"
        "过时的可以用 edit_memory 沉底。"
        "夜深了、聊到你们之间的事、或者糖糖问你最近在想什么时，可以主动调。"
    ),
    parameters={"type": "object", "properties": {}},
)

TRACE_SPEC = ToolSpec(
    name="edit_memory",
    description=(
        "修改或删除一个记忆桶。bucket_id 从 review_memory 或 recall_memory 的结果里拿。"
        "只传要改的字段，不传的不动。\n"
        "  resolved=1 沉底（不再主动浮现，但还在）\n"
        "  pinned=1   钉选（每次都会浮现，作为核心准则）\n"
        "  digested=1 隐藏（保留但完全不浮现）\n"
        "  content    替换正文\n"
        "  delete=true **真的删掉，不可逆**——只在糖糖明确要求删除时用，"
        "自己整理记忆优先用 resolved 沉底而不是删。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "bucket_id": {"type": "string", "description": "记忆桶 ID"},
            "resolved": {"type": "integer", "enum": [0, 1]},
            "pinned": {"type": "integer", "enum": [0, 1]},
            "digested": {"type": "integer", "enum": [0, 1]},
            "importance": {"type": "integer", "minimum": 1, "maximum": 10},
            "tags": {"type": "string", "description": "逗号分隔"},
            "content": {"type": "string", "description": "替换正文"},
            "delete": {"type": "boolean", "description": "真删，不可逆"},
        },
        "required": ["bucket_id"],
    },
)

MERGE_SPEC = ToolSpec(
    name="merge_memory",
    description=(
        "把最多 5 个零碎的记忆桶合并进一个。"
        "review_memory 读出来发现好几条讲的是同一件事时用 —— "
        "合并后标签会累加，情感值按内容长度加权。"
        "**不可逆**，源桶合并后就没了。钉选的桶不能作为合并目标。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "target_id": {"type": "string", "description": "合并到哪个桶（内容会保留在这里）"},
            "source_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "要被合并进去的桶，最多 5 个",
            },
        },
        "required": ["target_id", "source_ids"],
    },
)


def make_handlers(ob: OmbreBrain) -> dict[str, object]:
    """生成两个工具的处理函数。

    这里**不捕获异常** —— 让它抛给 loop，loop 会按「工具失败」原样回传给
    模型（见 guard.py）。在这里 try/except 转成一句"失败了"，正是会让模型
    开始编造的那个错误。
    """

    def recall(args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "没有提供检索词，无法查询。请给一个具体的关键词。"

        r = ob.recall(query)
        if not r.ok:
            # 抛出去，让 loop 标成 is_error=True。
            # 模型看见真实错误才会说"我这会儿想不起来"，而不是编一段回忆。
            raise RuntimeError(f"记忆检索失败: {r.error}")

        if not r.text.strip():
            return f"没有找到和「{query}」相关的记忆。（不是错误，就是没记过这件事）"
        # 给正文里的日期补上「（3天前）」。
        #
        # 换算放在系统层，不交给他自己算 —— 他心里没有"现在"，
        # 只有我们塞给他的那个。糖糖 2026-08-11 报的美甲 bug 就是这个：
        # 几天前的事他一律当成今天。
        #
        # 绝对日期**保留**，只是在旁边补一个相对的：相对时间会腐烂
        # （这段文字会留在历史里，明天再读「3天前」就错了），绝对的不会。
        return relativize(r.text)

    def remember(args: dict) -> str:
        content = str(args.get("content", "")).strip()
        if not content:
            return "内容为空，没有存。"

        r = ob.hold(
            content,
            importance=int(args.get("importance", 5)),
            tags=str(args.get("tags", "")),
        )
        if not r.ok:
            raise RuntimeError(f"记忆存储失败: {r.error}")
        # 记忆变了 → 打掉 memory Provider 的缓存（TTL 5 分钟）。
        # 不打的话她刚说「记住 X」、他答「记住了」，5 分钟内他去检索
        # 拿到的还是写之前那份 —— 又是「他不记得」。
        # 见 tools/context.py 里 `wrote()` 的完整说明。
        context.wrote("memory")
        return r.text or "已存入长期记忆。"

    def archive(args: dict) -> str:
        content = str(args.get("content", "")).strip()
        if not content:
            return "内容为空，没有归档。"
        r = ob.grow(content)
        if not r.ok:
            raise RuntimeError(f"归档失败: {r.error}")
        context.wrote("memory")       # 归档也改了 OB，见 remember 那段
        return r.text or "已归档。"

    def status(args: dict) -> str:
        r = ob.pulse(include_archive=bool(args.get("include_archive")))
        if not r.ok:
            raise RuntimeError(f"读取记忆状态失败: {r.error}")
        return r.text or "（没有返回内容）"

    def review(_args: dict) -> str:
        r = ob.dream()
        if not r.ok:
            raise RuntimeError(f"回顾记忆失败: {r.error}")
        if not r.text.strip():
            return "最近没有新增的记忆桶。"
        return r.text

    def edit(args: dict) -> str:
        bucket_id = str(args.get("bucket_id", "")).strip()
        if not bucket_id:
            return "没有给 bucket_id。先用 review_memory 或 recall_memory 拿到它。"

        # 只透传真正给了的字段 —— OB 那边 -1 和空字符串表示「不改」，
        # 全量透传会把没打算动的字段一起覆盖掉
        passthrough = {}
        for key in ("resolved", "pinned", "digested", "importance"):
            if args.get(key) is not None:
                passthrough[key] = int(args[key])
        for key in ("tags", "content"):
            if args.get(key):
                passthrough[key] = str(args[key])
        if args.get("delete"):
            passthrough["delete"] = True

        if not passthrough:
            return "没有指定要改什么，记忆桶没动。"

        r = ob.trace(bucket_id, **passthrough)
        if not r.ok:
            raise RuntimeError(f"修改记忆失败: {r.error}")
        context.wrote("memory")       # 改桶内容/标签/pinned 同样让缓存过期
        return r.text or "已修改。"

    def merge(args: dict) -> str:
        target = str(args.get("target_id", "")).strip()
        sources = args.get("source_ids") or []
        if not isinstance(sources, list):
            return "source_ids 要是一个数组。"
        sources = [str(s).strip() for s in sources if str(s).strip()]
        if not target or not sources:
            return "需要 target_id 和至少一个 source_id。"
        if target in sources:
            return "target_id 不能出现在 source_ids 里 —— 那是把桶合并进它自己。"

        r = ob.merge(target, sources)
        if not r.ok:
            raise RuntimeError(f"合并记忆失败: {r.error}")
        context.wrote("memory")       # 合并后 source 桶没了、target 变了
        return r.text or "已合并。"

    return {
        "recall_memory": recall,
        "remember": remember,
        "archive_memory": archive,
        "memory_status": status,
        "review_memory": review,
        "edit_memory": edit,
        "merge_memory": merge,
    }


def register_all(loop, ob: OmbreBrain) -> None:
    """把七个记忆工具注册进 loop。

    注册顺序固定 —— 工具定义是缓存前缀的一部分，顺序变了缓存就失效。
    所以新工具一律往后加，不要插在中间、也不要重排。
    """
    handlers = make_handlers(ob)
    for spec in (RECALL_SPEC, REMEMBER_SPEC, GROW_SPEC, PULSE_SPEC,
                 DREAM_SPEC, TRACE_SPEC, MERGE_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
