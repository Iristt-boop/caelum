"""TodoProvider —— 她手头有什么事。

## 唯一数据源：bridge 的本地清单（SQLite）

2026-08-18 起，待办的真源是 **bridge 自己那张 SQLite `todos` 表**
（手机端 / 桌面端「今天」页记的那些）。GitHub 上那份 `todo.md` 从那天起
**退役为只读存档**，不再有任何代码写它、也没有代码读它。

本 Provider 经 `/api/todo/list` 读那张表 —— bridge 按「今天有没有命中」
把行分到 `进行中 / 近期 / 随时` 三个桶（`hitsToday()`，三种时间模型），
这里只负责映射到 `render()` 认的三个桶。

> ⚠️ **2026-09-12 删掉了「读 GitHub todo.md」那条路。**
> 它原本是"没配 bridge 时的兜底"，但那份文件已经退役 —— 真触发时他会
> **拿一份旧清单当她的待办讲出去**，而且是静默的（不报错，只是内容过期）。
> 这正是 2026-08-05 那个「两个孤岛」bug 的形状，只是更隐蔽。
> **没有源就不该有这一栏**，而不是端上一份假的。
> 同一批删掉的还有 Core 那 3 个写 GitHub 的端点（见 `api/server.py` 里那段）。
> 现在没配 bridge 就是**不注册**这个 Provider，早报会把它列进「没注册的项」。

## ⚠️ 改数据源的时候，记得回来看渲染

`_fetch_local` 的 `due_soon` 装的是**整个「进行中」分区**（可能十几条）；
而 2026-08-18 之前走 GitHub 时它只装今明两天到期的（通常一两条）。
换源那次**只改了取数、没改渲染**，于是 todo 一个 Provider 就能吃光 800 字预算：

    Context 超出 800 字预算，这轮略过: todo      ← 48 小时喊了 61 次

也就是说**他每天有几十轮根本看不见她的待办，而且不报错**。
`render()` 里那段红字记的就是这件事。

## 已完成的不进上下文

bridge 的 `/api/todo/list` 直接 `WHERE done = 0`，已完成的根本不会来。
（GitHub 那条路上要在这里手动排除 `## 已完成` 区，那段代码随源一起删了。）
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)

#: 标题和说明之间的分隔。清单里破折号后面那段是写给糖糖看的细节
_DESC_SEP = re.compile(r"\s*(?:——|—|--|\s-\s)\s*")


class TodoProvider(BaseContextProvider):
    """bridge 本地清单里的待办。只读。"""

    name = "todo"
    section = "user"
    #: 30 分钟。她随口说一句 App 就改，但也没必要每轮都去 bridge 拉。
    #: ⚠️ 这个值同时就是「她刚记下的事，他最长能多久当作没听见」——
    #:    所以写路径**必须**登记 `context.wrote("todo")`（见 `tools/context.py`），
    #:    否则一串「他记性不好」的现象其实全来自这里。
    #:    同类：`health`（经期）6 小时、`memory` 5 分钟、`music` 3 分钟。
    #:    **体重不在此列** —— 没有任何 Provider 读饮食/体重，
    #:    所以它不是「快照过期」的问题，别照着这句话去挂 invalidate。
    ttl = timedelta(minutes=30)

    #: 「进行中」最多列几条。这段每轮都要付未命中价，不能让它无限长
    max_ongoing = 5

    #: 「今天要做的」最多列几条。**这一档比上面那档要紧**，所以给得宽一点，
    #: 但同样得有上限 —— 本地清单里它装的是整个「进行中」分区，
    #: 不限的话一个 provider 就能把 800 字预算吃光（2026-09-08 的病根）
    max_due = 6

    def __init__(self, bridge: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.bridge = bridge

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        if self.bridge is None:
            # 明确报错，不返回空清单 —— 「没有源」和「她真的没事」必须能分辨
            raise RuntimeError(
                "TodoProvider 没有 bridge：待办没有数据源"
                "（GitHub 的 todo.md 已退役，不再回退）")
        return self._fetch_local()

    def _fetch_local(self) -> dict[str, Any]:
        """读 bridge 的本地清单。

        `/api/todo/list` 回 `{ok, sections: {进行中, 近期, 随时}}`，
        分区规则在 bridge 那边（`hitsToday()`，三种时间模型）。
        「进行中」= 今天命中的那些（区名沿用旧的，因为手机端照它取值）。
        这里只负责映射到 `render()` 认的那三个桶。

        ⚠️ **`due_soon` 在这条路上的含义和走 GitHub 时不一样**：
        那边它只收今明两天到期的（通常一两条），这里它装的是**整个「进行中」分区**
        （可能十几条）。2026-08-18 换源时只改了取数、没改渲染，于是 todo 一个人
        就能吃光 800 字预算 —— `render()` 里那段红字记的就是这件事。
        以后再换源，**取数的含义变了就得回去看渲染**。
        """
        r = self.bridge.get("/api/todo/list")
        if not r.ok:
            raise RuntimeError(f"读本地清单失败: {r.error}")
        sections = ((r.data or {}).get("sections") or {})

        due_soon = [f"今天：{x}" for x in (sections.get("进行中") or [])]
        upcoming = list(sections.get("近期") or [])
        # 「随时」档没有时间，属于她一直挂着的事，归到「进行中」那一桶
        ongoing = list(sections.get("随时") or [])

        return {
            "due_soon": due_soon,
            "ongoing": ongoing,
            "upcoming": upcoming,
            "total_open": len(due_soon) + len(ongoing) + len(upcoming),
            "source": "local",
        }

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False or not state.get("total_open"):
            return ""

        stale = "（缓存）" if state.get("stale") else ""
        lines: list[str] = []

        # 今天/明天到期的最要紧，放最前。
        #
        # 🔴 **和下面的「手头在做」用同一套裁法**（2026-09-08 修）。
        # 原来这里是「每条一整行、原文照搬、不限条数」——
        # 走 GitHub 那阵子没事，因为 `due_soon` 只收今明两天到期的，通常一两条。
        # 2026-08-18 改读本地清单后，`_fetch_local` 把**整个「进行中」分区**
        # 都塞进了 due_soon，而渲染这边一个字没跟着改（那行注释还写着
        # 「渲染一个字没改」，当时是优点，现在是病根）。
        #
        # 后果实测：`Context 超出 800 字预算，这轮略过: todo` 48 小时喊了 61 次。
        # todo 在加载顺序里排第 9，前面 memory 一个就吃 330 字 —— 它基本必被丢。
        # 也就是说**他每天有几十轮根本看不见她的待办**，而且不报错。
        due = [_title(x) for x in state.get("due_soon", [])]
        if due:
            shown = due[: self.max_due]
            tail = f"，还有 {len(due) - len(shown)} 项" if len(due) > len(shown) else ""
            lines.append(f"【要做的{stale}】{'、'.join(shown)}{tail}。")

        # 「在忙什么」只要标题，破折号后面那些说明是写给糖糖看的。
        # 不裁的话这一行能到 200 多字，把 800 预算吃掉四分之一，
        # 而多出来的全是他用不上的细节。
        ongoing = [_title(x) for x in state.get("ongoing", [])]
        if ongoing:
            shown = ongoing[: self.max_ongoing]
            tail = f"，还有 {len(ongoing) - len(shown)} 项" if len(ongoing) > len(shown) else ""
            lines.append(f"【手头在做】{'、'.join(shown)}{tail}。")

        upcoming = [_title(x) for x in state.get("upcoming", [])]
        if upcoming:
            lines.append(f"【近期】{'、'.join(upcoming[:3])}。")

        return "\n".join(lines)


def _title(item: str) -> str:
    """只取破折号前的标题。

    「Nox会话管理修复 — 窗口碎片化+上下文5轮就丢」→「Nox会话管理修复」
    他需要知道的是她在忙哪几件事，不是每件的来龙去脉。
    """
    return _DESC_SEP.split(item, 1)[0].strip() or item.strip()
