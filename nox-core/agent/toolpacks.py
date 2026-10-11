"""按需工具包（Runtime Context R2，2026-10-11）。

R0 实测（设计稿 5.3½）：工具表 ≈ 23K token，占他每轮固定开销的 85%；164 个里 97 个四周一次没调过。
所以不再每轮把 164 个全给他：

- **核心集**：不属于任何包的工具 —— 默认都在。没登记进包的工具**不会被悄悄砍掉**
- **工具包**：点单 / 打车 / 快递 / 淘宝 / 电脑 / 花园 / 共听 … 下面三种情况挂上：
    1. 她最近几句话里有这个包的触发词
    2. 他 30 分钟内**真调过**这个包里的工具（「用过就留着」：点单是多轮任务，「就第一家」「好」没有关键词）
    3. 工牌入口就是这个包（共听 → listen、共读 → reading、Stack-chan 面前 → stackchan）

历史回放（设计稿 5.3¾）：前半段定规则、后半段打分，264 轮调了工具，漏 5 轮（1.9%），省 ≈ 50–60% 工具 token。

🔴 **不靠他自己「打开工具箱」** —— 那又回到了要模型主动决定的老路（设计稿第一节）。
🔴 他会**主动**用的工具（她旅行时自己查天气 amap_、问他配置时的 caelum_map）不进包，留在核心。

包也是数据：改这张表就行，loop 一行不改。`NOX_TOOL_PACKS=off` 退回全量（每次现读）。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pack:
    name: str
    #: 工具名前缀（MCP 工具没有代码里的 ToolSpec，只能按名字认）
    prefixes: tuple[str, ...] = ()
    #: 单独点名的工具（前缀不成组的）
    names: tuple[str, ...] = ()
    triggers: str = ""
    #: 工牌入口是这些时直接挂上
    surfaces: tuple[str, ...] = ()

    def owns(self, tool: str) -> bool:
        return tool in self.names or tool.startswith(self.prefixes)


PACKS: tuple[Pack, ...] = (
    Pack("mcd", ("mcd_",), triggers=r"麦当劳|麦麦|金拜拜|汉堡|薯条|麦乐"),
    Pack("luckin", ("luckin_",), triggers=r"瑞幸|luckin|咖啡|拿铁|生椰|美式"),
    Pack("didi", ("didi_",), triggers=r"打车|滴滴|叫车|快车|专车|司机"),
    Pack("kd100", ("kd100_",), triggers=r"快递|物流|单号|包裹|到哪了|顺丰|中通|圆通|韵达"),
    Pack("taobao", ("taobao_",), triggers=r"淘宝|购物车|加购|买.{0,6}(东西|这个|那个)|下单"),
    # 糖糖 10-11：start_long_task 是电脑上才用的，跟着电脑包走
    Pack("computer", ("computer_",), names=("start_long_task", "task_status", "task_cancel"),
         triggers=r"电脑|屏幕|窗口|截图|桌面|浏览器|网页|打开.{0,8}(网站|页面)|长任务"),
    Pack("garden", ("galatea_",), triggers=r"花园|galatea|帖子"),
    Pack("listen", ("listen_",), triggers=r"共听|一起听|听.{0,6}歌|歌单|这首|播放|放.{0,4}(首|点)|网易云|喜欢列表|挑一首|听什么",
         surfaces=("listen",)),
    Pack("notion", ("notion_",), triggers=r"notion|笔记本"),
    Pack("reading", ("reading_",), triggers=r"共读|读书|看书|这本书|批注|划线", surfaces=("reading",)),
    Pack("stackchan", ("stackchan_",), triggers=r"stack|小机器人|看看我|看到我|看得到我|看得见我|摄像头|你的眼睛",
         surfaces=("stackchan",)),
    # 糖糖 10-11：send_gallery_image 几乎没用过 —— 不砍，提到相册 / 照片才挂
    Pack("gallery", names=("send_gallery_image", "favorite_image"), triggers=r"相册|照片|图库|发.{0,4}(张|个)图"),
    # Creations（10-11）：她开口要才做（v1 不自主生成），平常聊天不背这五个
    Pack("studio", names=("make_creation", "edit_creation", "send_creation", "list_creations", "delete_creation"),
         triggers=r"做个|做一个|帮我做|弄个|小游戏|小工具|小玩意|creation|studio|五子棋|转盘|那个游戏"),
)

_TRIGGERS = {p.name: re.compile(p.triggers, re.I) for p in PACKS if p.triggers}

#: 「用过就留着」的窗口
STICKY_S = 30 * 60
#: 看她最近几句话（含这一句）
RECENT_USER_MSGS = 3


def enabled() -> bool:
    return os.getenv("NOX_TOOL_PACKS", "on").strip().lower() not in ("off", "0", "false")


def pack_of(tool: str) -> str | None:
    for p in PACKS:
        if p.owns(tool):
            return p.name
    return None


class UsedPacks:
    """会话 → {包: 他最后一次真调这个包的时间}。进程内存，重启就空 —— 最坏是这一轮靠关键词重新挂上。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at: dict[str, dict[str, float]] = {}

    def note(self, session_id: str | None, tools: Iterable[str], now: float | None = None) -> None:
        if not session_id:
            return
        now = time.time() if now is None else now
        packs = {p for p in (pack_of(t.rstrip("✗")) for t in tools) if p}   # 失败的也算用过
        if not packs:
            return
        with self._lock:
            d = self._at.setdefault(session_id, {})
            for p in packs:
                d[p] = now

    def recent(self, session_id: str | None, now: float | None = None) -> set[str]:
        if not session_id:
            return set()
        now = time.time() if now is None else now
        with self._lock:
            d = self._at.get(session_id, {})
            return {p for p, t in d.items() if now - t <= STICKY_S}


def select(
    tool_names: Iterable[str],
    *,
    texts: Iterable[str],
    used: set[str] = frozenset(),
    surface: str | None = None,
) -> tuple[set[str] | None, set[str]]:
    """这一轮给他哪些工具。返回 (工具名集合, 挂上的包)；开关关着返回 (None, ∅) = 全量。

    `texts`：她这一句 + 最近几句（调用方给，这里不翻历史）。
    """
    if not enabled():
        return None, set()
    mounted = set(used)
    joined = "\n".join(t for t in texts if t)
    for p in PACKS:
        rx = _TRIGGERS.get(p.name)
        if rx is not None and rx.search(joined):
            mounted.add(p.name)
        if surface and surface in p.surfaces:
            mounted.add(p.name)
    offer = {n for n in tool_names if (pack_of(n) is None or pack_of(n) in mounted)}
    return offer, mounted
