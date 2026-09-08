"""TodoProvider —— 她手头有什么事。

数据源是 GitHub 上那份 `todo.md`（`Iristt-boop/Claude` 仓库）。
那是她真正在用的清单：Claude 的 routines 每天早上读它推晨报，
她随口说一句、routine 记一笔。

## 只读 main，不读分支

routines 绑在 session 上，每个 session 开一个 `claude/xxx` 分支，
干完开 PR 合进 main。所以**理论上** main 会滞后「routine 改完到她合并」那段时间。

实测（2026-08-03）她合并很勤（8-1 两次、8-2 两次），而且当时 main 和
活跃分支的 `todo.md` 是同一个 blob sha。而待办这种东西滞后几小时基本无害 ——
「买传感器」不会因为晚知道两小时就出事。

跨分支找最新版要多一次 API 调用 + 排序逻辑，还得跟着 routine 的分支命名走。
**先只读 main**，等真发现「他老是不知道我刚加的事」再上那套。

## 只读，不写

她明确定的。写入意味着两个 Claude（routines 和 Nox）同时改一个文件，
冲突了谁也说不清。记待办继续走 routines 那条路。

## 已完成的不进上下文

`## 已完成` 那一大段是给她回顾用的，不是他要惦记的。
全塞进去只会挤掉真正要紧的几条 —— 那段现在有 20 条，比未完成的还多。
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from personality.mood import now_cst
from tools.http import RestClient

logger = logging.getLogger(__name__)

#: `## 区名` 开头的区块
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
#: 未完成项。`- [ ] xxx`，中间的空格可能是全角
_UNDONE = re.compile(r"^\s*-\s*\[\s\]\s*(.+?)\s*$")
#: 条目里的「8月4日」这种日期
_MONTH_DAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
#: 条目开头的「8月4日（周一）：」前缀。已经标了「今天/明天」就别再重复一遍日期
_DATE_PREFIX = re.compile(r"^\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*(?:（[^）]*）)?\s*[:：]?\s*")
#: 标题和说明之间的分隔。清单里破折号后面那段是写给糖糖看的细节
_DESC_SEP = re.compile(r"\s*(?:——|—|--|\s-\s)\s*")

#: 这些区里的内容才是「还要做的」。「已完成」故意排除
_ACTIVE_SECTIONS = ("进行中", "近期", "定期")


class TodoProvider(BaseContextProvider):
    """GitHub 上那份待办清单。只读。"""

    name = "todo"
    section = "user"
    # 30 分钟。她随口说一句 routine 就改，但也没必要每轮都去 GitHub 拉
    ttl = timedelta(minutes=30)

    #: 「进行中」最多列几条。这段每轮都要付未命中价，不能让它无限长
    max_ongoing = 5

    #: 「今天要做的」最多列几条。**这一档比上面那档要紧**，所以给得宽一点，
    #: 但同样得有上限 —— 本地清单里它装的是整个「进行中」分区，
    #: 不限的话一个 provider 就能把 800 字预算吃光（2026-09-08 的病根）
    max_due = 6

    def __init__(self, repo: str = "", path: str = "todo.md", token: str = "",
                 bridge: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        #: 给了 bridge 就读**本地清单**（2026-08-18 起的唯一活清单）；
        #: 没给才回退去读 GitHub 的 todo.md（只读存档，留着兜底）
        self.bridge = bridge
        self.repo = repo.strip().strip("/")
        self.path = path.strip().lstrip("/")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = RestClient(
            base="https://api.github.com", headers=headers, timeout=15.0,
            auth_hint="GitHub token 无效或没有这个私有仓库的读权限",
        )

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        if self.bridge is not None:
            return self._fetch_local()
        return self._fetch_github()

    def _fetch_local(self) -> dict[str, Any]:
        """读 bridge 的本地清单。

        `/api/todo/list` 回 `{ok, sections: {进行中, 近期, 随时}}`，
        分区规则在 bridge 那边（`hitsToday()`，三种时间模型）。
        「进行中」= 今天命中的那些（区名沿用旧的，因为手机端照它取值）。
        这里只负责映射到 `render()` 认的那三个桶。

        ⚠️ **`due_soon` 在这条路上的含义变了**：走 GitHub 时它只收今明两天
        到期的（通常一两条），这里它装的是**整个「进行中」分区**（可能十几条）。
        2026-08-18 换源时只改了取数、没改渲染，于是 todo 一个人就能吃光
        800 字预算 —— `render()` 里那段红字记的就是这件事。
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

    def _fetch_github(self) -> dict[str, Any]:
        r = self.client.get(f"/repos/{self.repo}/contents/{self.path}")
        if not r.ok:
            raise RuntimeError(f"读 {self.repo}/{self.path} 失败: {r.error}")

        d = r.data or {}
        raw = d.get("content") or ""
        try:
            text = base64.b64decode(raw).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"todo.md 解码失败: {exc}") from exc

        buckets = _split_sections(text)
        today = now_cst().date()

        due_soon: list[str] = []
        ongoing: list[str] = []
        upcoming: list[str] = []
        for name in _ACTIVE_SECTIONS:
            for item in buckets.get(name, []):
                when = _match_date(item, today.year)
                if when is not None and (when - today).days in (0, 1):
                    label = "今天" if when == today else "明天"
                    # 去掉条目自带的日期前缀，不然会变成「明天：8月4日（周一）：买…」
                    due_soon.append(f"{label}：{_DATE_PREFIX.sub('', item)}")
                elif name == "进行中":
                    ongoing.append(item)
                else:
                    upcoming.append(item)

        return {
            "due_soon": due_soon,
            "ongoing": ongoing,
            "upcoming": upcoming,
            "total_open": len(due_soon) + len(ongoing) + len(upcoming),
            "sha": d.get("sha"),
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


def _split_sections(text: str) -> dict[str, list[str]]:
    """按 `## 区名` 切开，每区收集 `- [ ]` 的未完成项。"""
    out: dict[str, list[str]] = {}
    marks = list(_SECTION.finditer(text))
    for i, m in enumerate(marks):
        name = m.group(1).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        items = []
        for line in text[m.end():end].splitlines():
            hit = _UNDONE.match(line)
            if hit:
                items.append(hit.group(1).strip())
        out[name] = items
    return out


def _match_date(item: str, year: int):
    """从条目里抠出「8月4日」这种日期。抠不到返回 None。"""
    m = _MONTH_DAY.search(item)
    if not m:
        return None
    from datetime import date
    try:
        return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
