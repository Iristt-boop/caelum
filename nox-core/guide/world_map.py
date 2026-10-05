"""环境地图：Caelum「现在有什么」，每次现取，只给地图和摘要。

和手册（`guide/loader.py`）是一对：手册讲怎么运作、从不写现状；地图讲现状、
**数据全部来自运行期**（工具表、Context 注册表、配置、各仓库），不手写 ——
手写的地图就是又一份会过期的 PROJECT.md。

## 只给名称、数量、状态

🔴 **每一段都是白名单字段**，不许把依赖返回的整个 dict / 对象拼进输出：
  · 模型线路只说「key 已配置 / 未配置」，key 本身一个字节都不给；
  · 不含记忆正文、对话内容、任何 key/token/secret/password 字段的值；
  · 依赖回调里夹带的多余字段（哪怕叫 note）一律丢掉。
这条由 `tests/test_guide_map.py` 守着（塞已知标记串进去，扫输出）。

## 依赖缺失 / 出错不拖垮别的段

某个依赖是 None → 该段「（未接入）」；调用抛异常 → 「（取不到：异常类名）」，
**并且 warning 留痕**（`docs/LOGGING.md`：每个 catch 必须留痕）。其它段照常。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from agent.llm import GATED_EFFECTS

logger = logging.getLogger(__name__)

SCOPES = ("tools", "context", "models", "pending", "health", "os")

_OVERVIEW_LIMIT = 1500
_DETAIL_LIMIT = 4000
_MAX_GROUP_LINES = 25
_MAX_ITEMS = 40

#: 工具的归属 = `handler.__module__` → 给她和他看的短中文名。
#: 查不到的模块直接显示模块名 —— 别为了好看编一个名字。
#: 新加工具模块不登记这里也不会坏，只是显示成模块名。
MODULE_LABELS: dict[str, str] = {
    "memory.tools": "记忆",
    "topic_pool.tool": "话题池",
    "tools.daily": "日常（清单、日记、相册）",
    "tools.planner": "日常（清单、日记、相册）",
    "tools.diet": "饮食",
    "tools.record": "健康记录",
    "tools.remind": "给自己留纸条",
    "tools.misc": "时间",
    "tools.search": "联网搜索",
    "tools.notion": "Notion",
    "tools.eryu": "共听",
    "tools.netease": "共听",
    "tools.reading": "共读",
    "tools.watching": "共影",
    "tools.ha": "家居",
    "tools.room": "像素房间",
    "tools.stackchan": "Stack-chan 身体",
    "tools.intimate": "表达（表情包、语音、笔记）",
    "tools.computer": "她的电脑",
    "tools.tracker": "应用使用记录",
    "tools.tasks": "长任务",
    "agent.tasks": "长任务",
    "tools.amap": "出行",
    "tools.didi": "出行",
    "tools.train": "出行",
    "tools.kd100": "快递",
    "tools.mcd": "点单",
    "tools.luckin": "点单",
    "tools.taobao": "购物",
    "guide.tools": "手册与地图",
}


@dataclass
class MapDeps:
    """地图的数据来源。全部是只读取值，由 `nox.py` 装配（见 `guide/wiring.py`）。"""

    #: AgentLoop —— 只读 `.tools`（name -> Tool，Tool.spec / Tool.handler）
    loop: Any = None
    #: ContextProviderRegistry —— 只读 `.names()` / `.get(name)`
    context_registry: Any = None
    #: 每项 {"role", "backend", "model", "key_configured": bool}
    models: Callable[[], list[dict]] | None = None
    #: {"orders": n, "tasks": n}
    pending: Callable[[], dict[str, int]] | None = None
    #: 形如 `/health` 的返回：只取 ok / model / background_stale
    health: Callable[[], dict] | None = None
    #: 每项 {"id", "title"}
    os_pages: Callable[[], list[dict]] | None = None


# --------------------------------------------------------------- 小工具


def _s(value: Any, limit: int = 60) -> str:
    return str(value).replace("\n", " ").strip()[:limit]


_CLIP_MARK = "\n…（已截断）"

#: 总览里每段的字符预算。不给每段单独的预算，工具一多，**排在后面的段（待确认、健康）
#: 会被整体截断吞掉** —— 而那几段恰恰是最短、最该看见的。
_OVERVIEW_BUDGET = {
    "tools": 600, "context": 250, "models": 250, "pending": 80, "health": 130, "os": 130,
}  # 合计 1440 + 段间空行 10 < _OVERVIEW_LIMIT，所以总兜底不会再吞掉哪一段


def _clip(text: str, limit: int) -> str:
    """截到不超过 `limit`（标记也算在内），在行边界上切。"""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - len(_CLIP_MARK), 0)].rsplit("\n", 1)[0]
    return cut + _CLIP_MARK


def _module_of(handler: Any) -> str:
    """handler 可能是函数、functools.partial 或带 `.func` 的包装。"""
    for _ in range(4):
        mod = getattr(handler, "__module__", None)
        if mod and mod not in ("functools", "builtins"):
            return mod
        inner = getattr(handler, "func", None)
        if inner is None:
            break
        handler = inner
    return "其他"


def _label_of(tool: Any) -> str:
    mod = _module_of(getattr(tool, "handler", None))
    return MODULE_LABELS.get(mod, mod)


# --------------------------------------------------------------- 各段


def _tools(deps: MapDeps, detail: bool) -> str:
    if deps.loop is None:
        return "工具：（未接入）"
    groups: dict[str, list[tuple[str, bool]]] = {}
    for name, tool in dict(deps.loop.tools).items():
        gated = getattr(getattr(tool, "spec", None), "side_effect", None) in GATED_EFFECTS
        groups.setdefault(_label_of(tool), []).append((name, gated))
    total = sum(len(v) for v in groups.values())
    gated_total = sum(1 for v in groups.values() for _, g in v if g)
    lines = [f"工具（{total} 个，分 {len(groups)} 组；{gated_total} 个需要她点头）："]
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = ordered if detail else ordered[:_MAX_GROUP_LINES]
    for label, items in shown:
        g = sum(1 for _, flag in items if flag)
        line = f"- {label}：{len(items)} 个"
        if g:
            line += f"（其中 {g} 个需要她点头）"
        if detail:
            line += "：" + "、".join(n for n, _ in sorted(items))
        lines.append(line)
    if len(shown) < len(ordered):
        lines.append(f"- …另有 {len(ordered) - len(shown)} 组（scope=tools 看全部）")
    return "\n".join(lines)


def _context(deps: MapDeps, detail: bool) -> str:
    reg = deps.context_registry
    if reg is None:
        return "上下文来源：（未接入）"
    names = list(reg.names())
    if not detail:
        return f"上下文来源（在场 {len(names)} 个）：" + "、".join(_s(n, 30) for n in names[:_MAX_ITEMS])
    lines = [f"上下文来源（在场 {len(names)} 个；注册表没有启用/关闭开关，在场即会被 Router 按需取）："]
    for n in names[:_MAX_ITEMS]:
        p = reg.get(n)
        extra = []
        for attr in ("section", "ttl"):
            v = getattr(p, attr, None)
            if v is not None:
                extra.append(f"{attr}={_s(v, 30)}")
        lines.append(f"- {_s(n, 30)}" + (f"（{', '.join(extra)}）" if extra else ""))
    return "\n".join(lines)


def _models(deps: MapDeps, detail: bool) -> str:
    if deps.models is None:
        return "模型线路：（未接入）"
    rows = deps.models()
    lines = ["模型线路："]
    for row in rows[:_MAX_ITEMS]:
        configured = "已配置" if row.get("key_configured") else "未配置"
        lines.append(
            f"- {_s(row.get('role'), 20)}：{_s(row.get('backend'), 30)} / "
            f"{_s(row.get('model'), 60)}，key {configured}"
        )
    return "\n".join(lines)


def _pending(deps: MapDeps, detail: bool) -> str:
    if deps.pending is None:
        return "待她确认：（未接入）"
    d = deps.pending()
    return f"待她确认：点单 {int(d.get('orders', 0))}、任务 {int(d.get('tasks', 0))}"


def _health(deps: MapDeps, detail: bool) -> str:
    if deps.health is None:
        return "健康：（未接入）"
    d = deps.health()
    stale = [_s(x, 40) for x in list(d.get("background_stale") or [])[:5]]
    parts = ["正常" if d.get("ok") else "不正常"]
    if d.get("model"):
        parts.append(f"主模型 {_s(d['model'], 60)}")
    parts.append("停摆的后台任务：" + ("、".join(stale) if stale else "无"))
    return "健康：" + "；".join(parts)


def _os(deps: MapDeps, detail: bool) -> str:
    if deps.os_pages is None:
        return "桌面端页面：（未接入，页面名不要凭记忆答）"
    pages = deps.os_pages()
    items = [f"{_s(p.get('id'), 30)}：{_s(p.get('title'), 30)}" for p in pages[:_MAX_ITEMS]]
    if not detail:
        return f"桌面端页面（{len(pages)} 个）：" + "、".join(i.split("：")[0] for i in items)
    return f"桌面端页面（{len(pages)} 个）：\n" + "\n".join(f"- {i}" for i in items)


_SECTIONS: dict[str, Callable[[MapDeps, bool], str]] = {
    "tools": _tools,
    "context": _context,
    "models": _models,
    "pending": _pending,
    "health": _health,
    "os": _os,
}


def build_map(deps: MapDeps, scope: str | None = None) -> str:
    """`scope=None` → 总览；在 `SCOPES` 里 → 只给那一段（更详细）；不认识 → 提示，不抛。"""
    if scope in (None, ""):
        wanted, detail = list(SCOPES), False
    elif isinstance(scope, str) and scope in SCOPES:
        wanted, detail = [scope], True
    else:
        return f"不认识的范围「{_s(scope, 30)}」。可选：{' / '.join(SCOPES)}"

    parts: list[str] = []
    for name in wanted:
        try:
            part = _SECTIONS[name](deps, detail)
        except Exception as exc:  # noqa: BLE001 —— 一段取不到不拖垮别的段，但要留痕
            logger.warning("caelum_map 的「%s」段取不到: %s", name, exc, exc_info=True)
            part = f"{name}：（取不到：{type(exc).__name__}）"
        parts.append(part if detail else _clip(part, _OVERVIEW_BUDGET[name]))
    return _clip("\n\n".join(parts), _DETAIL_LIMIT if detail else _OVERVIEW_LIMIT)
