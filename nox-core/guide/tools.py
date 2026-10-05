"""手册 + 地图的两个只读工具：`guide_read` 和 `caelum_map`。

两个都 `side_effect="none"`：只读，改不了任何东西。

登记**只走 `loop.register`** —— 那道闸（没声明 side_effect 就拒收）是整条工具闸门的
地基，绕过它直接写 `loop.tools` 等于开了个后门。重名也**不许静默覆盖**
（2026-08-05 `add_todo` 撞名，后注册的赢，没有任何报错）。

每次调用打一行 info 日志 —— 他会不会真的翻手册 / 查地图，得靠这行数出来
（他 98.7% 的轮次不翻记忆，手册完全可能是同样下场；没有数据之前不能说做成了）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from guide.loader import Topic, guide_read, load_topics
from guide.world_map import SCOPES, MapDeps, build_map
from tools import context as tool_context

logger = logging.getLogger(__name__)


GUIDE_READ_SPEC = ToolSpec(
    side_effect="none",
    name="guide_read",
    description=(
        "翻 Caelum 使用手册里的一篇。她问「这个怎么用」「在哪设置」「某个机制怎么运作」、"
        "或你自己拿不准 Caelum 某块是怎么回事的时候，**先翻，不要凭印象答**。"
        "篇目见系统提示里的目录；topic 填目录里的 id。"
        "手册只讲做法和原理，**不讲现状**；现在开没开、配没配、有多少，用 caelum_map 或对应工具查，"
        "操作做成没做成以工具返回为准。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "目录里的篇目 id，例如 models-and-routes"},
        },
        "required": ["topic"],
    },
)

CAELUM_MAP_SPEC = ToolSpec(
    side_effect="none",
    name="caelum_map",
    description=(
        "查 Caelum 现在有什么：工具有哪些分组、哪些上下文来源在场、各条模型线路是什么、"
        "有几件事在等她点头、后台健康怎样、桌面端有哪些页面。"
        "**要知道「现在有什么 / 开着什么 / 配没配」时查这个，不要凭记忆答。**"
        "只给名称、数量、状态，不含任何密钥，也不含记忆和对话正文。"
        "scope 不填给总览；填 tools / context / models / pending / health / os 给那一段的详情。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": list(SCOPES), "description": "可选；不填给总览"},
        },
    },
)


def register_all(
    loop,
    *,
    topics: list[Topic] | None = None,
    deps: MapDeps | None = None,
) -> None:
    """登记两个工具。

    `topics=None` 时现场读手册文件；`deps=None` 时只有工具段有数据，其余显示「（未接入）」。
    手册文件不合格会在这里抛 `GuideError` —— 炸在启动，好过某天他翻出半截的。
    """
    topics = load_topics() if topics is None else topics
    deps = MapDeps(loop=loop) if deps is None else deps

    for spec in (GUIDE_READ_SPEC, CAELUM_MAP_SPEC):
        if spec.name in loop.tools:
            raise ValueError(f"工具 {spec.name!r} 已经登记过了，不许静默覆盖")

    known = {t.id for t in topics}

    def read_handler(args: dict) -> str:
        key = args.get("topic")
        text = guide_read(topics, key)
        logger.info(
            "guide_read topic=%s hit=%s session=%s",
            key, isinstance(key, str) and key.strip() in known, tool_context.session_id(),
        )
        return text

    def map_handler(args: dict) -> str:
        scope = args.get("scope")
        logger.info("caelum_map scope=%s session=%s", scope, tool_context.session_id())
        return build_map(deps, scope)

    loop.register(GUIDE_READ_SPEC, read_handler)
    loop.register(CAELUM_MAP_SPEC, map_handler)
