"""Nox Core 组装入口。

把各层拼起来，并守住那条最重要的规矩：
**静态前缀生成一次就冻住，绝不在对话中途重建。**
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from agent import vision
from agent.adapters import make_adapter, supports_vision
from agent.llm import LLMAdapter, Message
from agent.loop import AgentLoop
from config import Config, _build_llm, config as default_config
from context import ContextProviderRegistry
from context.base import Turn
from context.timeline import humanize
from context.providers.understanding import has_live_anchor
from context.providers import (
    HealthProvider, HomeProvider, LocationProvider, MemoryProvider,
    MoodProvider, MusicProvider, ResonanceProvider, TimeProvider, TodoProvider,
    UnderstandingProvider, WeatherProvider,
)
from memory import tools as memory_tools
from memory.archiver import ArchiveResult, Archiver
from memory.ob_client import OmbreBrain
from personality import mood
from personality.mood import CST, now_cst
from personality import prompt as personality
from personality import scenes
from router.intent import _NEED_MUSIC, classify, classify_context
from router.router import RouteResult, Router
from tools import amap as amap_tools
from tools import context
from tools import daily as daily_tools
from tools import didi as didi_tools
from tools import diet as diet_tools
from tools import eryu as eryu_tools
from tools import galatea as galatea_tools
from tools import ha as ha_tools
from tools import intimate as intimate_tools
from tools import kd100 as kd100_tools
from tools import mcd as mcd_tools
from tools import misc as misc_tools
from tools import netease as netease_tools
from tools import notion as notion_tools
from tools import planner as planner_tools
from tools import luckin as luckin_tools
from tools import reading as reading_tools
from tools import room as room_tools
from tools import search as search_tools
from tools import tracker as tracker_tools
from tools import train as train_tools
from tools import watching as watching_tools
from tools.bridge_client import BridgeClient
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

# 路径里像凭据的长串十六进制。ha-mcp 的访问 token 就藏在 URL 路径里，
# 直接打日志会把它写进 journal、日志文件和崩溃报告。
_SECRET_IN_PATH = re.compile(r"/[0-9a-f]{16,}(?=/|$)")


def _mask_url(url: str) -> str:
    return _SECRET_IN_PATH.sub("/<token>", url)


def _strip_mood_tag(result: RouteResult, cleaned: str) -> None:
    """把 [mood:xxx] 从对外文本和历史里都抹掉。

    历史也要改：那条 assistant 消息会被存进库、下次作为上下文回放，
    留着标记会教模型把它当成正常输出的一部分，越滚越乱。
    """
    result.result.text = cleaned
    for m in reversed(result.result.messages):
        if m.role == "assistant" and m.text:
            m.text = cleaned
            break


#: 欠卡账的落盘键。和 `attention/` 那几个键同一张 `source_state` 表 ——
#: 情绪和这条账一起存，因为它们同生同灭：都是"这一轮之前发生了什么"，
#: 都按同一个半衰期过期。**别再开一张表**（CAELUM-MAP 那条「平行实现」）
STATE_KEY = "personality.state"


class _CardDebt(set):
    """欠卡账，对外就是个 `set[str]`，但每次变动会顺手落盘。

    为什么做成 set 的子类而不是换个数据结构：`api/server.py` 里那段
    `_warn_if_claimed_without_doing` 直接 `.add()` / `.discard()`，
    `nox.py:_dynamic` 直接 `in`，测试里还有 `== set()` —— 子类把这些
    语义原样留着，调用方一行都不用改。

    额外记 `_at`（记账时刻），恢复时用它判断这笔账过没过期。
    """

    def __init__(self, on_change=None) -> None:
        super().__init__()
        #: sid → 记账时刻 ISO。只在 add 时写，discard 时删
        self._at: dict[str, str] = {}
        self._on_change = on_change

    def add(self, sid: str) -> None:
        if sid not in self:
            self._at[sid] = mood.now_cst().isoformat()
        super().add(sid)
        self._changed()

    def discard(self, sid: str) -> None:
        if sid not in self:
            return  # 没这笔账就别为它写一次盘
        super().discard(sid)
        self._at.pop(sid, None)
        self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def to_dict(self) -> dict[str, Any]:
        # 只导出还在集合里的，`_at` 里的孤儿（理论上没有）顺手丢掉
        return {sid: at for sid, at in self._at.items() if sid in self}

    def restore(self, d: dict[str, Any] | None, now: datetime | None = None) -> int:
        """读回没过期的欠账，返回捡回来几笔。**不抛异常。**

        为什么这条账也要跨重启（糖糖 2026-09-14 定）：
        原注释说「重启就忘那正好，重启后他未必再犯」——但**会话历史还留在库里**，
        而这条账治的恰恰是"他照抄自己的历史"。历史没变，他照抄的前提就没变，
        账却因为发了个版本清掉了 —— 这条防线在部署之后是**静默失效**的。

        过期时间挂 `mood.HALF_LIFE`（同一个旋钮）：那之后这段历史多半
        已经滚出上下文窗口，再当面点破他就是背一个过时的指控了。
        """
        if not d:
            return 0
        now = now or mood.now_cst()
        kept = 0
        for sid, at in dict(d).items():
            try:
                when = datetime.fromisoformat(str(at))
            except (TypeError, ValueError):
                logger.warning("欠卡账的时间戳读不出来，丢掉这笔：%s=%r", sid, at)
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=mood.CST)
            if (now - when) > mood.HALF_LIFE:
                continue
            # 走 super()：这是"读回来"不是"新记一笔"，
            # 既不该刷新记账时刻，也不该为此再写一次盘
            super().add(sid)
            self._at[sid] = str(at)
            kept += 1
        if kept:
            logger.info("欠卡账接回来 %d 笔（还没过期）", kept)
        return kept


class Nox:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or default_config

        problems = self.cfg.check()
        if problems:
            raise RuntimeError("配置有问题:\n  " + "\n  ".join(problems))

        # 按需建的备用模型 adapter，建一次留着（见 adapter_for）
        self._adapters: dict[str, LLMAdapter] = {}

        # ⚠️ **2026-09-12：「这轮是哪个会话 / 什么时候开始的」不再放在实例上。**
        #
        # 原来这里是 `self.current_session_id` + `self.current_session_started`
        # 两个进程级属性：`api/server.py` 的 `_turn_starts()` 在每轮开跑前写它们，
        # 而工具在**别的时间点**读 —— 两个并发请求（她的实时聊天 + 关注链/唤醒链
        # 自己开口）就会互串：A 轮跑到一半被写成 B，于是 A 轮里 `remind_myself`
        # 留的纸条、`luckin_order` 建的订单都挂到了 **B 会话**上，**不报错**。
        #
        # 现在走两条明确的路，都不经过实例属性：
        #   · 工具的：`ToolContext.session_id`（按轮隔离，见 tools/context.py）
        #   · 提示里的：`chat()` / `chat_stream()` 的入参，一路传到 `_dynamic()`
        # 所以这类隐患从"靠没人并发"变成"结构上不可能"。

        self.ob = OmbreBrain(self.cfg.ob_url, timeout=self.cfg.ob_timeout)
        self.loop = AgentLoop(
            adapter=make_adapter(self.cfg.primary),
            max_iterations=self.cfg.max_iterations,
            # 墙钟预算。max_iterations 数轮数，它数时间 —— 缺了它，
            # 一个"每轮都慢但不报错"的上游可以把一轮聊天拖成几十分钟
            deadline_s=self.cfg.chat_deadline_s,
        )
        memory_tools.register_all(self.loop, self.ob)
        misc_tools.register_all(self.loop)

        # 家居工具。没配 NOX_HA_URL 就跳过 —— 他控不了家电，但照样能聊天。
        ha_client: McpClient | None = None
        if self.cfg.ha_url:
            ha_client = McpClient(self.cfg.ha_url, name="ha", timeout=self.cfg.ha_timeout)
            ha_tools.register_all(self.loop, ha_client)
            # 打脱敏后的地址 —— ha-mcp 的访问凭据在 URL 路径里，
            # 原样打日志会把它写进 journal 和日志文件
            logger.info("家居工具已注册（%s）", _mask_url(self.cfg.ha_url))
        else:
            logger.info("未配置 NOX_HA_URL，跳过家居工具")

        # 我们家那间像素房间。没配 NOX_ROOM_URL 就跳过 ——
        # 他进不去房间，但照样能聊天（同家居那条的处理）。
        if self.cfg.room_url:
            room_tools.register_all(
                self.loop, McpClient(self.cfg.room_url, name="room", timeout=15.0)
            )
            logger.info("房间工具已注册（%s）", _mask_url(self.cfg.room_url))
        else:
            logger.info("未配置 NOX_ROOM_URL，跳过房间工具")

        # 相册 / 待办 / 日记。数据在 bridge 的 SQLite 里，走它的 REST 接口。
        # 存成属性是给 Daily Planner 的推送用的 —— 推送通道（订阅表 + VAPID
        # 私钥）只在 bridge 那一处，Core 借它的 /api/push/send 出去。
        self.bridge: BridgeClient | None = None
        if self.cfg.bridge_url:
            bridge = BridgeClient(
                self.cfg.bridge_url, self.cfg.bridge_token, self.cfg.bridge_timeout
            )
            self.bridge = bridge
            daily_tools.register_all(self.loop, bridge)
            # 语音条 / 设备 / Obsidian 也都从 bridge 走。
            # Obsidian 优先走 GitHub（配了 GITHUB_OBSIDIAN_REPO 时）——
            # 这样不依赖电脑 Agent 在跑，手机也能写。没配才退回 bridge。
            from tools.github_obsidian import GithubObsidian
            gh_obsidian = (GithubObsidian(self.cfg.github_token, self.cfg.github_obsidian_repo)
                           if self.cfg.github_obsidian_repo else None)
            if gh_obsidian:
                logger.info("Obsidian 走 GitHub 仓库 %s（不依赖电脑）",
                            self.cfg.github_obsidian_repo)
            intimate_tools.register_all(self.loop, bridge, gh_obsidian)
            # 一起看过什么片子（共影 P1，2026-08-22）。
            # 在这之前共影只活在前端，他在别的对话里提不起「昨天那部」
            watching_tools.register_all(self.loop, bridge)
            # 启动时探一下，别等糖糖让他写日记才发现 token 不对
            probe = bridge.ping()
            logger.info(
                "日常工具已注册（%s）%s",
                self.cfg.bridge_url,
                "" if probe.ok else f" ⚠ 自检失败: {probe.error}",
            )
        else:
            logger.info("未配置 NOX_BRIDGE_URL，跳过相册/待办/日记工具")

        # Notion。长期记忆的另一半，也是他俩的信箱
        if self.cfg.notion_token:
            notion_tools.register_all(
                self.loop, notion_tools.make_client(self.cfg.notion_token, self.cfg.notion_timeout)
            )
            logger.info("Notion 工具已注册")
        else:
            logger.info("未配置 NOTION_TOKEN，跳过 Notion 工具")

        # 联网搜索。他没有实时的世界知识，这是唯一的补法。
        # 没 key 就不注册 —— 给模型一个每次都报错的工具比没有更糟，
        # 它会一遍遍地试，然后每次都要跟她解释一次为什么没查成
        if self.cfg.tavily_key:
            search_tools.register_all(
                self.loop,
                search_tools.make_client(self.cfg.tavily_key, self.cfg.tavily_timeout),
            )
            logger.info("联网搜索已注册（Tavily）")
        else:
            logger.info("未配置 TAVILY_API_KEY，跳过联网搜索")

        # 共读的页边笔记
        if self.cfg.reading_url:
            reading_tools.register_all(
                self.loop,
                reading_tools.make_client(
                    self.cfg.reading_url, self.cfg.reading_token, self.cfg.reading_timeout
                ),
            )
            logger.info("共读工具已注册（%s）", self.cfg.reading_url)
        else:
            logger.info("未配置 NOX_READING_URL，跳过共读工具")

        # 待办写入。
        #
        # ⚠️ **2026-08-18：GitHub todo.md 退役**（Todo-Daily-Planner-设计.md）。
        # 前端 todo 是唯一活清单，写入走 `tools/daily.py` 的 add_todo →
        # bridge `/api/today`（那条路本来就在，而且带时间模型）。
        #
        # 这里原来注册的 `tools/todo.py` 是往 GitHub 写的那一套 ——
        # 再留着的话，她说「记一下」他会记进一份**没人看的死档案**，
        # 而且 App 里查不到。所以有 bridge 就不注册它。
        # 记待办和划待办都在 `tools/daily.py`（add_todo / complete_todo），
        # 走 bridge 的本地表，上面 diet 那批一起注册，这里不用再来一次。
        logger.info("待办读写走 bridge 本地清单（GitHub todo.md 已退役为只读存档）")

        # 饮食记录。bridge 是必经之路 —— 种子数据落 bridge 的 SQLite，
        # Core 只是把工具调用转成 REST 请求。不依赖外部服务，永远可注册。
        diet_handlers = diet_tools.make_handlers(bridge)
        diet_tools.register_all(self.loop, diet_handlers)
        # 只统计以 _ 开头的 ToolSpec 键（如 _search_food、_add_food）
        diet_n = sum(1 for k in diet_handlers if k.startswith("_"))
        logger.info("饮食工具已注册（%d 个）", diet_n)

        # App 使用记录
        if self.cfg.tracker_url:
            tracker_tools.register_all(
                self.loop,
                McpClient(self.cfg.tracker_url, name="tracker", timeout=self.cfg.tracker_timeout),
            )
            logger.info("App 记录工具已注册（%s）", _mask_url(self.cfg.tracker_url))
        else:
            logger.info("未配置 NOX_TRACKER_URL，跳过 App 记录工具")

        # 共听系统 —— 播放层（eryu）
        if self.cfg.eryu_url:
            eryu_tools.register_all(
                self.loop,
                eryu_tools.make_client(
                    self.cfg.eryu_url, self.cfg.eryu_token, self.cfg.eryu_timeout
                ),
            )
            logger.info("eryu 音乐工具已注册（%s）", self.cfg.eryu_url)
        else:
            logger.info("未配置 NOX_ERYU_URL，跳过 eryu 音乐工具")

        # 共听系统 —— 账号层（netease-music-mcp）
        if self.cfg.netease_url:
            netease_client = McpClient(
                self.cfg.netease_url, name="netease", timeout=self.cfg.netease_timeout
            )
            netease_tools.register_all(self.loop, netease_client)
            logger.info("netease 账号工具已注册（%s）", self.cfg.netease_url)
        else:
            logger.info("未配置 NOX_NETEASE_URL，跳过 netease 账号工具")

        # ---- 国内 MCP（2026-09-05）：高德/滴滴/快递100/12306 ----
        # 纪律：新块只能往后加（netease 之后、_build_prefix 之前），
        # 工具注册顺序是缓存前缀的一部分
        if self.cfg.amap_url:
            amap_tools.register_all(
                self.loop,
                McpClient(self.cfg.amap_url, name="amap", timeout=self.cfg.amap_timeout),
            )
            logger.info("amap 地点工具已注册（%s）", self.cfg.amap_url)
        else:
            logger.info("未配置 NOX_AMAP_MCP_URL，跳过 amap 地点工具")

        # 滴滴：只接预估/链接/查单。他不下单（tools/didi.py 头部红线）
        if self.cfg.didi_url:
            didi_tools.register_all(
                self.loop,
                McpClient(self.cfg.didi_url, name="didi", timeout=self.cfg.didi_timeout),
            )
            logger.info("didi 打车工具已注册（%s）", self.cfg.didi_url)
        else:
            logger.info("未配置 NOX_DIDI_MCP_URL，跳过 didi 打车工具")

        if self.cfg.kd100_url:
            kd100_tools.register_all(
                self.loop,
                McpClient(self.cfg.kd100_url, name="kd100", timeout=self.cfg.kd100_timeout),
            )
            logger.info("kd100 快递工具已注册（%s）", self.cfg.kd100_url)
        else:
            logger.info("未配置 NOX_KD100_MCP_URL，跳过 kd100 快递工具")

        if self.cfg.train_url:
            train_tools.register_all(
                self.loop,
                McpClient(self.cfg.train_url, name="train", timeout=self.cfg.train_timeout),
            )
            logger.info("train 火车票工具已注册（%s）", self.cfg.train_url)
        else:
            logger.info("未配置 NOX_TRAIN_MCP_URL，跳过 train 火车票工具")

        # Galatea 花园：他的第一个社交世界（B 档全量参与，公开动作见 tools/galatea.py）
        if self.cfg.galatea_url and self.cfg.galatea_token:
            galatea_tools.register_all(
                self.loop,
                McpClient(self.cfg.galatea_url, name="galatea", timeout=self.cfg.galatea_timeout,
                          headers={"Authorization": f"Bearer {self.cfg.galatea_token}"}),
            )
            logger.info("galatea 花园工具已注册")
        else:
            logger.info("未配置 NOX_GALATEA_MCP_URL/NOX_GALATEA_TOKEN，跳过 galatea 花园工具")

        # 麦当劳：只读九件（门店/菜单/价格/券/订单/活动）—— 下单类不接
        if self.cfg.mcd_url and self.cfg.mcd_token:
            mcd_tools.register_all(
                self.loop,
                McpClient(self.cfg.mcd_url, name="mcd", timeout=self.cfg.mcd_timeout,
                          headers={"Authorization": f"Bearer {self.cfg.mcd_token}"}),
            )
            logger.info("mcd 麦当劳工具已注册")
        else:
            logger.info("未配置 NOX_MCD_MCP_URL/NOX_MCD_TOKEN，跳过 mcd 麦当劳工具")

        # 瑞幸。**`luckin_order` 现在不下单**，只出一张待确认卡（2026-09-06）——
        # 真正的 createOrder 在 /api/nox/orders/{id}/confirm 后面，模型够不着。
        # 设计见 `Caelum-点单确认卡-设计.md`。
        if self.cfg.luckin_url and self.cfg.luckin_token:
            self.luckin_client = McpClient(
                self.cfg.luckin_url, name="luckin", timeout=self.cfg.luckin_timeout,
                headers={"Authorization": f"Bearer {self.cfg.luckin_token}"})
            luckin_tools.register_all(
                self.loop, self.luckin_client,
                # 传取值函数：orders store 在 `api/server.py` 才造出来，
                # 那时候这里早注册完了（同 ResonanceProvider 的 attention_ref）
                store_ref=lambda: getattr(self, "orders", None),
                # 取值函数，不是值：它每次调用去 ToolContext 里拿**当下这一轮**的
                # 会话 id。别写回 `lambda: self.current_session_id` —— 那个取的是
                # 「最近一次开跑的那轮」，并发下会把订单挂到别人的会话上，
                # 而 App 那边按会话找单会找不到（见 __init__ 里那段）。
                session_id_ref=context.session_id,
            )
            logger.info("luckin 瑞幸工具已注册（下单走确认卡）")
        else:
            self.luckin_client = None
            logger.info("未配置 NOX_LUCKIN_MCP_URL/NOX_LUCKIN_TOKEN，跳过 luckin 瑞幸工具")

        # 启动时取一次核心准则，之后**永不重取**。
        # 不做定时刷新：糖糖明确说了不需要，需要新记忆时他会自己调
        # recall_memory。定时刷新会让缓存前缀变动，得不偿失。
        self._prefix = self._build_prefix()
        self._system = self._prefix.render()

        # 轻量路径用便宜模型。配不出来就退回主模型 —— 省的大头是那 12K
        # 前缀和工具定义，不是模型单价，所以退回也依然划算。
        light = None
        if self.cfg.utility.usable:
            try:
                light = make_adapter(self.cfg.utility)
            except Exception as exc:  # noqa: BLE001
                logger.warning("杂活模型不可用，轻量路径改用主模型: %s", exc)

        self.router = Router(self.loop, self._system, light_adapter=light)

        # 累积情绪。带惯性 —— 连续几轮甜蜜会慢慢热起来，
        # 不会因为一句平淡的话瞬间冷掉。
        #
        # 2026-09-14 起**跨重启**：落 `source_state`，恢复时按离开的时长朝默认
        # 衰减（半衰期 2 小时，见 `personality/mood.py:HALF_LIFE`）。
        #
        # 原来这里写的是"重启会重置回默认，这是有意的：隔了一天再开口，本来也
        # 该是平常状态"。那句话的理由没错，错在它**默认了「重启 ≈ 隔了很久」**：
        # 2026-09-13 一天重启 8 次，全是发版本，间隔几十秒 —— 她一句话还没说完，
        # 攒了五六轮的温度就没了。衰减同时满足两头：几十秒几乎原样，隔天照样平常。
        self.mood = mood.Mood()

        #: 🔴 **欠她一张卡的会话**（2026-09-06）。
        #:
        #: 病史：他说「卡发你了」但根本没调 `luckin_order`，连着两轮。
        #: 那几轮进了会话历史之后，他开始**照抄自己** —— 同一个会话里
        #: 再问还是只说不做。换个新会话立刻正常，可 Caelum App 是
        #: **单一窗口、没有新建会话**（`Chat.jsx`），她换不了。
        #:
        #: 所以只能在同一条历史里把它掰回来：检测到「说了没做」就记一笔，
        #: 下一轮在他的动态块里当面点破，直到真的发出卡才清掉。
        #:
        #: 2026-09-14 起**跟着情绪一起落盘**，同一个半衰期过期。
        #: 原来写的是「重启就忘那正好，他未必再犯」——但那条理由是反的：
        #: **会话历史还留在库里**，而这条账治的就是"他照抄自己的历史"。
        #: 历史没变，照抄的前提就没变，账却因为发了个版本清掉了 ——
        #: 这条防线在部署之后是**静默失效**的。见 `_CardDebt.restore`。
        self.card_debt = _CardDebt(on_change=self._save_state)

        #: 状态只在第一次用到时读回来一次（见 `_restore_state_once`）。
        #: 不能在这里读：存档在 attention 的库里，而 attention 要等
        #: `api/server.py:_build_attention` 才造出来，那会儿这里早跑完了
        self._state_restored = False

        # Context Provider 注册表。第一个（也是眼下唯一一个）实例是 MoodProvider ——
        # 把本来就在跑的三层情绪收编进框架，而不是另起一套「每轮注入」的机制
        # （架构文档 0.3）。后续 time / memory / home 往这里加。
        #
        # ⚠️ 渲染结果只走 dynamic_system，见 _dynamic()。
        self.context = ContextProviderRegistry()
        self.context.register(MoodProvider(self.mood))
        self.context.register(TimeProvider())
        # 他自己的感觉。**在这个 Provider 之前，Drive 从来没进过他的上下文** ——
        # 算出来只进糖糖的面板，他本人感觉不到（2026-09-04 发现）。
        #
        # 传取值函数：attention 在 `api/server.py` 的 `_build_attention`
        # 里才造出来，那时候这里早注册完了（同上面 HealthProvider 的 world_ref）
        self.context.register(ResonanceProvider(
            attention_ref=lambda: getattr(self, "attention", None)))
        # 他理解着她的哪几件事（2026-09-05）。和上面那个是同一个教训的第二次：
        # 理解层把「她说不想干了 = 觉得投入没意义」算出来写进 Registry，
        # 不给他读就等于没算（CAELUM-MAP 三问之二：谁消费它）。
        #
        # 理解层没开时它渲染成空串，一个字都不占 —— 所以可以直接进每轮名单
        self.context.register(UnderstandingProvider(
            attention_ref=lambda: getattr(self, "attention", None)))
        # 注册但**不进每轮名单**（见 _dynamic）。它一次检索约 7 秒，
        # 而且每轮塞不同记忆会让 dynamic_system 每轮都变，
        # 缓存命中率从 98.9% 掉到 62.5%、每轮成本 ×12。
        # 日常对话继续走 recall_memory 工具，模型自己判断要不要回忆。
        # 留在这里是给 Daily Planner 那种一天一两次的场景用的。
        self.context.register(MemoryProvider(self.ob))
        # 同样注册但不进每轮名单：架构文档第八节本来就只在家居类请求里加载 home。
        # 复用上面那个 ha_client —— 绝不新建第二套 HA 客户端，
        # 设备清单已经在三处漂移过一次了（第二十节）。
        if ha_client is not None:
            self.context.register(HomeProvider(ha_client))
        # 健康数据一天才更新一次，同样不进每轮名单，留给 Daily Planner。
        # 没配 NOX_HEALTH_URL 就跳过 —— 他只是不知道她睡得怎么样，别的照常
        if self.cfg.health_url:
            # ⚠️ 经期从 **World Model** 读，不再读 health-mcp 的
            # `get_menstrual_cycle`（2026-08-19）。那个工具读的是
            # `health.db` 的 menstrual 表 —— 快捷指令写坏过、已停更，
            # 而写入侧当天已经切到 World Model。读写不切在一起的话，
            # 她在 App 里记的东西他在对话里永远看不见。
            #
            # 传的是**取值函数**不是值：World Model 在 `_build_attention`
            # 里才造出来，那时候 Provider 早注册完了
            self.context.register(HealthProvider(
                McpClient(self.cfg.health_url, name="health",
                          timeout=self.cfg.health_timeout),
                world_ref=lambda: getattr(self, "world", None),
            ))
        else:
            logger.info("未配置 NOX_HEALTH_URL，跳过 HealthProvider")
        # 天气。复用 xiaozhi-server 那份和风 key，同样不进每轮名单
        if self.cfg.qweather_host and self.cfg.qweather_key:
            self.context.register(WeatherProvider(
                host=self.cfg.qweather_host, key=self.cfg.qweather_key,
                location=self.cfg.weather_location))
        else:
            logger.info("未配置 QWEATHER_HOST/KEY，跳过 WeatherProvider")
        # 音乐。她此刻在听什么 —— 走 eryu 的 /music/recent（前端每次
        # 开始播放都会上报）。ttl 只有 3 分钟：这个状态是会变的，
        # 缓存久了会出现「他说你在听 A，其实早换成 B 了」，那比不知道更糟
        if self.cfg.eryu_url:
            self.context.register(MusicProvider(
                eryu_tools.make_client(self.cfg.eryu_url, self.cfg.eryu_token)))
        else:
            logger.info("未配置 NOX_ERYU_URL，跳过 MusicProvider")
        # 待办。**2026-08-18 起只有一个源：bridge 的本地清单**（前端 todo 是
        # 唯一活清单，GitHub 那份 `todo.md` 退役为只读存档）。
        #
        # ⚠️ 2026-09-12 删掉了「没配 bridge 就回退读 GitHub」那条分支。理由：
        # 那条兜底读的是一份**已退役的存档**，真触发时他会拿一份旧清单当她的
        # 待办讲出去 —— 这正是 2026-08-05 那个「两个孤岛」bug 的形状，只是更隐蔽
        # （不报错，只是内容过期）。**没有源就不该有这一栏**，而不是端上一份假的。
        # 现在：没配 bridge → 跳过，日志说明原因，早报会把它列进「没注册的项」
        # （`planner/daily.py` 的 `missing`，她会看见）。
        if self.bridge is not None:
            self.context.register(TodoProvider(bridge=self.bridge))
            logger.info("TodoProvider 读本地清单（bridge）")
        else:
            logger.info("没配 bridge，跳过 TodoProvider（GitHub todo.md 已退役，不再回退）")
        # 位置。双数据源按优先级仲裁：
        #   1. HA Tracker（person/device_tracker，WiFi 探知，可靠且不耗电）
        #   2. Caelum PWA（Geolocation → Bridge，GPS 精确但需主动上报）
        # 需要至少一个数据源才能注册。高德 key 没有也能跑 —
        # 只是 enrich 那步跳过，只靠 trigger/HA state 判状态。
        if self.cfg.bridge_url or (self.cfg.ha_api_url and self.cfg.ha_api_token):
            self.context.register(LocationProvider(
                bridge_url=self.cfg.bridge_url,
                bridge_token=self.cfg.bridge_token,
                gaode_key=self.cfg.gaode_key,
                ha_api_url=self.cfg.ha_api_url,
                ha_api_token=self.cfg.ha_api_token,
            ))
            parts = []
            if self.cfg.ha_api_url and self.cfg.ha_api_token:
                parts.append("HA Tracker")
            if self.cfg.bridge_url:
                parts.append("PWA")
            parts.append("高德 enrich" if self.cfg.gaode_key else "无高德 key")
            logger.info("LocationProvider 已注册（%s）", " + ".join(parts))
        else:
            logger.info("未配置 NOX_BRIDGE_URL 或 HA API，跳过 LocationProvider")

        # Daily Planner。必须等上面这些 Provider 都注册完 —— 它是拿着
        # registry 一次取全套的那个消费方（架构文档第十五节 Phase 2）。
        #
        # 注册在所有工具最后，位置固定：工具定义进静态前缀，顺序一变
        # 12K 前缀就整段作废（tools/daily.py:238）。以后加工具往后排，别插队。
        planner_tools.register_all(self.loop, self.context)

        # 归档器。摘要走 utility 模型（便宜那个），不占主线的钱和缓存。
        self.archiver = Archiver(self.ob, light or self.loop.adapter)

        logger.info(
            "Nox 就绪 | 主模型=%s | 轻量=%s | 静态前缀=%d 字符 | 工具=%d 个",
            self.cfg.primary.model,
            self.cfg.utility.model if light else "(同主模型)",
            len(self._system),
            len(self.loop.tools),
        )

    def _build_prefix(self) -> personality.StaticPrefix:
        r = self.ob.core_principles()
        if not r.ok:
            # 记忆层挂了不该让 Nox 起不来 —— 他顶多显得健忘一点，
            # 而且 recall_memory 工具还在，之后恢复了照样能查。
            logger.warning("核心准则取不到，先空着启动: %s", r.error)
            return personality.build("")
        logger.info("核心准则 %d 字符已载入并冻结", len(r.text))
        return personality.build(r.text)

    @property
    def system_prompt(self) -> str:
        """只读。想改人设请重启 —— 中途改会让整段缓存作废。"""
        return self._system

    def model_name(self, model: str | None) -> str:
        """这轮实际会用哪个型号。给用量记账用，让 Console 能按模型分开算。"""
        choice = self.cfg.models.get(model or "")
        return choice.model if choice else self.cfg.primary.model

    def adapter_for(self, model: str | None) -> LLMAdapter | None:
        """按前端传的短名取 adapter。返回 None 表示「用默认那个」。

        建好的 adapter 缓存起来 —— 每轮新建一个会把 SDK 的连接池也重建一遍。

        ⚠️ 换模型意味着**提示词缓存整段作废**（缓存按「模型 + 前缀字节」匹配）。
        这是给糖糖偶尔换口味用的，不是让她来回横跳的。
        """
        if not model:
            return None
        choice = self.cfg.models.get(model)
        if not choice:
            # 认不出来就用默认的 —— 不能因为前端传了个没见过的名字就答不上话
            logger.warning("没见过的模型名 %s，这轮用默认模型", model)
            return None
        if choice.model == self.cfg.primary.model:
            return None

        cached = self._adapters.get(model)
        if cached is not None:
            return cached
        try:
            # 用这个模型**自己的后端**建，不是套主模型的地址 ——
            # 主模型切到 DeepSeek 之后，拿 DeepSeek 的 base_url 去请求
            # anthropic/claude-* 只会 404
            cfg = _build_llm(
                backend=choice.backend,
                model=choice.model,
                max_tokens=self.cfg.primary.max_tokens,
            )
            if not cfg.usable:
                logger.warning("模型 %s 的后端 %s 没配 key，这轮用默认", model, choice.backend)
                return None
            adapter = make_adapter(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("模型 %s 建不出 adapter，这轮用默认: %s", model, exc)
            return None
        self._adapters[model] = adapter
        logger.info("切到 %s（%s / %s）—— 缓存前缀作废，这轮要重写",
                    choice.label or choice.model, choice.model, choice.backend)
        return adapter

    def _see(
        self, text: str, images: list[str] | None, model: str | None
    ) -> tuple[str, list[str] | None]:
        """主模型读不了图时，先让视觉模型把图看成文字。

        返回 (新的 text, 还要不要带图)。带图的模型原样放行。

        ⚠️ 这一步必须在进入 loop **之前**做，因为 loop 返回的 messages 会被
        存进会话历史。转成文字之后历史里就没有图片结构了 ——
        2026-08-02「一张图毒死整个会话」就是历史里的图每轮重发导致的。
        顺带也省钱：同一张图只看一次。
        """
        if not images:
            return text, images

        adapter = self.adapter_for(model) or self.loop.adapter
        model_name = getattr(getattr(adapter, "cfg", None), "model", "")
        if supports_vision(model_name):
            return text, images          # 他自己就能看，不用替他看

        desc = vision.describe(images, self.cfg.vision, user_text=text,
                               timeout=self.cfg.vision_timeout)
        if desc is None:
            # 看不了就交给 adapter 那层的降级文案如实说，绝不编一段描述出来
            logger.info("视觉模型不可用，这轮如实告诉她看不见")
            return text, images
        block = vision.wrap(desc, len(images))
        return (f"{text}\n\n{block}" if text else block), None

    # ------------------------------------------------------------ 跨重启的状态

    def _state_store(self):
        """拿 `source_state` 表。拿不到返回 None。

        ⚠️ 必须每次现取，不能在 `__init__` 里存下来 —— 它是
        `api/server.py` 建完 attention 之后挂上来的，比 `Nox.__init__` 晚。

        🔴 **不要写成 `getattr(self, "attention", None).store`**（2026-09-14 踩过）。
        `attention` 是 `create_app` 的局部变量，**全仓没有任何地方把它挂到 core 上**，
        所以那个写法在线上恒为 None —— 落盘一次都不会发生，而且**一个错都不报**。
        我照着 `ResonanceProvider(attention_ref=...)` 抄的，那条本身就是坏的
        （见 `api/server.py` 挂 `state_store` 那段的注释）。
        单测全绿、线上 6 轮对话一个字都没存下来，就是这么来的。
        """
        return getattr(self, "state_store", None)

    def _restore_state_once(self) -> None:
        """第一次用到情绪之前，把上个进程留下的状态接回来。

        失败只记一笔就走 —— 接不回来最坏是这次从平常状态开始，
        绝不能因此让这一轮聊不成（同 `attention/store.py:260` 的取舍）。
        """
        if self._state_restored:
            return
        store = self._state_store()
        if store is None:
            return  # attention 还没起来，下一轮再试
        self._state_restored = True  # 成败都只试这一次，别每轮都去读库
        try:
            saved = store.get_source_state(STATE_KEY) or {}
        except Exception:  # noqa: BLE001
            logger.exception("读不出上个进程的情绪存档，这次从平常状态开始")
            return
        self.mood.restore(saved.get("mood"))
        self.card_debt.restore(saved.get("card_debt"))

    def _save_state(self) -> None:
        """把情绪和欠卡账写回库。每轮一次，**同步写**。

        为什么不等 attention 那条 tick 顺手带走：那条 tick 最长隔几十秒，
        而部署式重启本来就发生在几十秒的尺度上 —— 靠 tick 落盘等于这件事
        有一半的时候仍然会丢。写一次是一个带锁的 UPSERT，在模型已经跑了
        几秒的这条路上可以忽略。

        失败**只警告不抛**：情绪没存住的代价是下次重启回到平常状态，
        而抛出去会把她这一轮的回复整个弄没。但必须留痕（`docs/LOGGING.md`）。
        """
        store = self._state_store()
        if store is None:
            return
        try:
            store.set_source_state(STATE_KEY, {
                "mood": self.mood.to_dict(),
                "card_debt": self.card_debt.to_dict(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("情绪落盘失败（不影响这轮对话），下次重启会回到平常状态：%s", exc)

    def _dynamic(self, text: str, voice: bool, scene: str | None = None,
                 has_images: bool = False,
                 session_id: str | None = None,
                 session_started: datetime | None = None) -> str:
        """组装每轮可变的提示：情绪 +（语音模式下）该情景的通话指令。

        两者都走 dynamic_system，跟在缓存断点之后 —— 静态前缀一个字都不能变。
        """
        # 挂在这里是因为这是情绪和欠卡账**第一次被读**的地方（下面 Provider
        # 渲染 + `session_id in self.card_debt` 都在这条路上）。恢复必须早于
        # 第一次读，晚一步她这轮拿到的就是默认情绪
        self._restore_state_once()
        # 情绪现在走 Context 框架出来（MoodProvider），输出与原来的
        # mood.render() 逐字相同 —— tests/test_mood_provider.py 拿 1000 组基线守着。
        # 以后加 time / memory / home，只要往这个名单里添名字；
        # 加载谁由 Router 决定，别在这里写死（架构文档第八节）。
        # 加载哪些 Provider 由 Router 决定，不在这里写死（架构文档第八节）。
        # 闲聊只有 time+mood（都是本地计算）；提到家电才拉 home，
        # 问到身体才拉 health。轻量路径强制最小集 —— 那条路的意义就是快。
        light = classify(text, has_images=has_images).light
        # 他心里正搁着事的时候，才值得花 650ms 去翻记忆（2026-09-05 解禁）。
        # 由理解层给答案，不在这里另写一套判断 —— 两处写迟早不一致
        names = classify_context(
            text, light=light,
            has_understanding=has_live_anchor(getattr(self, "attention", None)),
        )
        parts = [self.context.render(
            names, turn=Turn(text=text, voice=voice, scene=scene))]
        if voice:
            parts.append(scenes.get(scene).render())
        # 共听模式：检测到音乐相关请求时注入 MUSIC_SCENE
        # 告诉他能力边界和行为准则（不列歌单、不硬推、记得记笔记）
        if not voice and _NEED_MUSIC.search(text):
            parts.append(scenes.MUSIC_SCENE)
        # 这段对话聊了多久。**天级**，所以一天之内不变、不冲缓存
        # （分钟级会让 dynamic_system 每分钟都变，理由见 providers/time.py:78）。
        # 只在跨天时才说 —— 当天开的会话说「已经 0 天」是废话
        span = self._session_span(session_started)
        if span:
            parts.append(span)
        # 上一轮他说卡发了但没发。**当面点破**，否则他会照着自己的
        # 历史继续只说不做（见 __init__ 里 card_debt 那段）
        if session_id in self.card_debt:
            parts.append(
                "【纠正】你上一轮说了「卡发你了」之类的话，"
                "**但你并没有调用 luckin_order，她那边什么都没收到**。\n"
                "她现在正等着一张不存在的卡。这一轮如果她还要下单，"
                "**必须真的调用工具**——只写字不调用等于骗她。"
            )
        return "\n\n".join(parts)

    def _session_span(self, started: datetime | None) -> str:
        """「这段对话是 X 天前开始的」。

        历史里的日期分隔线已经写了开始日期，这里再给一个**算好的差值** ——
        糖糖 2026-08-11 定的原则：换算不交给模型，他心里没有"现在"。

        ⚠️ 入参而不是读 `self.current_session_started`：那是进程级属性，
        并发下会把**别人的**会话开始时间算给他（见 `__init__` 里那段）。
        """
        if started is None:
            return ""
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        days = (now_cst().date() - started.astimezone(CST).date()).days
        if days <= 0:
            return ""
        return f"【这段对话】从 {humanize(days)}开始，中间聊聊停停"

    def _flush_dirty(self, result: RouteResult) -> None:
        """把这一轮**写过**的状态对应的 Provider 缓存打掉。

        ## 为什么必须有这一步

        Provider 是按 TTL 缓存的（`health` 6 小时、`todo` 30 分钟、`music` 3 分钟），
        而写路径（`record_period` / `add_todo` / `eryu_play` …）成功之后
        **没有任何人通知缓存**。于是下一轮拼提示时递给他的还是**写之前**那份快照：

            她：我来例假了       → 他调 record_period，真写进 World Model 了
            他：记下了           → 这句是真的
            （之后最多 6 小时内的任何一轮）
            她：我今天是不是来例假了？ → 他读旧快照 → 说「没有记录」

        **全程不报错。** 从她那头看就是「他不记得」——
        TTL 实际上成了「你刚告诉他的事，他最长能多久当作没听见」。
        （2026-09-12 实测：生产代码里 `invalidate()` **一次都没被调用过**，
        只有 `_client.py` 那个同名的 MCP token 缓存在用。）

        ## 为什么不「每轮全清」

        全清代价太大：`health` 那个 6 小时 TTL 是**故意的**（睡眠/经期一天变几次），
        每轮都清等于每轮都真打一次 health-mcp，慢且贵。
        所以只清**这一轮真的写过**的那几个，由工具自己经
        `ToolContext.wrote()` 登记 —— 见 `tools/context.py` 里的完整说明。

        ## 为什么收口在这一层

        `nox-core` 里只有这里同时握着「loop 这一轮的产物」和「Provider registry」
        （`self.context`）。工具那侧够不着 registry，也不该为了清缓存去 import
        一个全局单例 —— 那是审计点名的老毛病。
        """
        names = list(getattr(result, "dirty_providers", None) or ())
        if not names:
            return
        registry = getattr(self, "context", None)
        if registry is None:      # 假 core / 没启上下文时静默跳过
            return
        for name in names:
            try:
                cleared = registry.invalidate(name)
            except Exception:  # noqa: BLE001 —— 清缓存失败不能拖垮这一轮对话
                logger.exception("打掉 Provider 缓存失败: %s", name)
                continue
            logger.info("这一轮写过 %s → 打掉它的缓存（清掉 %d 条）", name, cleared)

    def chat(
        self,
        text: str,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        voice: bool = False,
        scene: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        session_started: datetime | None = None,
    ) -> RouteResult:
        text, images = self._see(text, images, model)
        # 三层情绪渲染成一段动态提示，跟在缓存断点之后发 ——
        # 每轮都变，绝不能混进静态前缀（见 personality/mood.py 开头）
        dynamic = self._dynamic(text, voice, scene, has_images=bool(images),
                                session_id=session_id,
                                session_started=session_started)
        result = self.router.handle(
            text, history, dynamic_system=dynamic, images=images,
            voice=voice, scene=scene, adapter=self.adapter_for(model),
            session_id=session_id,
        )
        # 这一轮写过什么状态 → 把对应 Provider 的缓存打掉。
        # 放在最前面：后面几步都是文本后处理，不该影响清缓存这件事。
        self._flush_dirty(result)

        # 模型在回复末尾附了 [mood:xxx]，取出来更新状态并从正文剥掉。
        # 忘了附不算错 —— 保持上一轮的情绪即可，不要因此报错或重试。
        cleaned, detected = mood.extract(result.text)
        # 他把 [开心] 直接写进正文（不调 send_meme）时的兜底：抽出来转成
        # 真正的表情事件 —— 分段不分段都能发出（2026-09-06 她报的降级）。
        cleaned, meme_tags = intimate_tools.extract_text_tags(cleaned)
        if meme_tags:
            result.attachments.extend({"type": "meme", "tag": t} for t in meme_tags)
        if detected:
            self.mood.update(detected)
            logger.debug(
                "情绪 → %s | valence=%.2f arousal=%.2f",
                detected, self.mood.valence, self.mood.arousal,
            )
            self._save_state()
        if cleaned != result.text:
            _strip_mood_tag(result, cleaned)
        return result

    def chat_stream(
        self,
        text: str,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        voice: bool = False,
        scene: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        session_started: datetime | None = None,
    ):
        """流式版 chat。

        只走完整路径 —— 轻量路径本来就两三秒，为它再铺一套流式不划算，
        而且短回复流式反而闪。想省钱走 chat()，想要即时反馈走这条。

        情绪标记的处理跟非流式不同：那边等文本收全了统一剥，这边靠
        MoodTagFilter 边流边挡（见 loop.run_stream）。收尾时再从完整
        文本里取一次情绪，更新累积状态。
        """
        text, images = self._see(text, images, model)
        dynamic = self._dynamic(text, voice, scene, has_images=bool(images),
                                session_id=session_id,
                                session_started=session_started)
        for ev in self.loop.run_stream(
            text,
            # 语音走精简的英文/中文前缀，不带那 11.5K 中文核心准则 ——
            # 否则英文指令会被中文语料淹没（见 scenes.Scene.system）
            system=scenes.get(scene).system() if voice else self._system,
            dynamic_system=dynamic,
            history=history, images=images,
            # 语音模式关掉分段：||| 会被 TTS 当成正文念出来
            split=not voice,
            adapter=self.adapter_for(model),
            session_id=session_id,
        ):
            if ev.type == "done":
                result = getattr(ev, "result", None)
                if result is not None:
                    # 这一轮写过什么状态 → 把对应 Provider 的缓存打掉（见 _flush_dirty）
                    self._flush_dirty(result)
                    cleaned, detected = mood.extract(result.text)
                    # 正文里懒写的 [tag] 抽出来转成表情事件（同非流式那条）
                    cleaned, meme_tags = intimate_tools.extract_text_tags(cleaned)
                    if meme_tags:
                        result.attachments.extend(
                            {"type": "meme", "tag": t} for t in meme_tags
                        )
                    if detected:
                        self.mood.update(detected)
                        self._save_state()
                    if cleaned != result.text:
                        result.text = cleaned
                        for m in reversed(result.messages):
                            if m.role == "assistant" and m.text:
                                m.text = cleaned
                                break
            yield ev

    def archive(self, history: list[Message], *, force: bool = False) -> ArchiveResult:
        """把这段对话沉淀进 Ombre Brain。

        对话结束时调（CLI 退出、API 显式请求）。够不够格归档由 Archiver
        判断 —— 打个招呼就走的不算一段对话。
        """
        return self.archiver.archive(history, force=force)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="跟 Nox 聊天")
    ap.add_argument(
        "--session", "-s", default="cli",
        help="会话 id。默认 cli —— 同一个 id 关掉再开会接着上次聊",
    )
    ap.add_argument("--new", action="store_true", help="开一段全新的对话，不接历史")
    args = ap.parse_args()

    # 同 api/server.py：级别由 NOX_LOG_LEVEL 决定（审计 1.3）
    level, complaint = default_config.logging_level()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    if complaint:
        logger.warning(complaint)
    try:
        nox = Nox()
    except RuntimeError as exc:
        print(f"启动失败：{exc}")
        return 1

    from data.store import Store

    store = Store(nox.cfg.db_path)
    sid = uuid.uuid4().hex if args.new else args.session

    history: list[Message] = [] if args.new else store.load(sid, limit=nox.cfg.history_limit)

    print(f"\n静态前缀 {len(nox.system_prompt)} 字符，工具 {list(nox.loop.tools)}")
    print(f"会话 {sid}", end="")
    if history:
        print(f"，接上 {len(history)} 条历史")
        print("--- 上次聊到这儿 ---")
        for m in history[-4:]:
            who = "糖糖" if m.role == "user" else "Nox "
            text = (m.text or "").replace("\n", " ")
            print(f"  {who} > {text[:70]}{'…' if len(text) > 70 else ''}")
        print("---")
    else:
        print("（全新对话）")
    print("输入聊天内容，空行退出。\n")
    while True:
        try:
            line = input("糖糖 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break

        r = nox.chat(line, history)
        history = r.messages
        store.sync(sid, history)   # 每轮落盘，中途关窗口也不丢

        if r.ok:
            print(f"Nox  > {r.text}\n")
        else:
            print(f"[{r.result.outcome}] {r.result.detail or ''}")
            if r.text:
                print(f"Nox  > {r.text}")
            print()

        u = r.result.usage
        path = "轻量" if r.decision.light else "完整"
        print(
            f"       ({path} | {r.result.iterations} 轮 | 输入 {u.input_tokens} "
            f"缓存命中 {u.cache_read_tokens} 写入 {u.cache_write_tokens} "
            f"输出 {u.output_tokens})\n"
        )

    # 退出前归档。够不够格由 Archiver 判断 —— 打个招呼就走的不算一段对话。
    # 失败只提示，不影响退出：糖糖已经聊完了，这时候报错只会吓她一跳。
    if history:
        print("\n正在归档这段对话…")
        res = nox.archive(history)
        if res.archived:
            print(f"已存进长期记忆：\n  {res.summary}\n")
        else:
            print(f"（没归档：{res.reason}）\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
