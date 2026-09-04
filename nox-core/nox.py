"""Nox Core 组装入口。

把各层拼起来，并守住那条最重要的规矩：
**静态前缀生成一次就冻住，绝不在对话中途重建。**
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from agent import vision
from agent.adapters import make_adapter, supports_vision
from agent.llm import LLMAdapter, Message
from agent.loop import AgentLoop
from config import Config, _build_llm, config as default_config
from context import ContextProviderRegistry
from context.base import Turn
from context.timeline import humanize
from context.providers import (
    HealthProvider, HomeProvider, LocationProvider, MemoryProvider,
    MoodProvider, MusicProvider, ResonanceProvider, TimeProvider, TodoProvider,
    WeatherProvider,
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
from tools import daily as daily_tools
from tools import diet as diet_tools
from tools import eryu as eryu_tools
from tools import ha as ha_tools
from tools import intimate as intimate_tools
from tools import misc as misc_tools
from tools import netease as netease_tools
from tools import notion as notion_tools
from tools import planner as planner_tools
from tools import reading as reading_tools
from tools import room as room_tools
from tools import search as search_tools
from tools import tracker as tracker_tools
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


class Nox:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or default_config

        problems = self.cfg.check()
        if problems:
            raise RuntimeError("配置有问题:\n  " + "\n  ".join(problems))

        # 按需建的备用模型 adapter，建一次留着（见 adapter_for）
        self._adapters: dict[str, LLMAdapter] = {}

        #: 这轮是哪个会话。`remind_myself` 要用它把纸条挂到对的对话上。
        #: 由 `api/server.py` 的 `_turn_starts()` 在开跑前设。
        self.current_session_id: str | None = None
        #: 这个会话是什么时候开始的。同样由 `_turn_starts()` 设。
        #: 用来在 dynamic_system 里告诉他「这段对话已经聊了几天」——
        #: 他心里没有"现在"，时间差必须算好了递给他。
        self.current_session_started: datetime | None = None

        self.ob = OmbreBrain(self.cfg.ob_url, timeout=self.cfg.ob_timeout)
        self.loop = AgentLoop(
            adapter=make_adapter(self.cfg.primary),
            max_iterations=self.cfg.max_iterations,
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

        # 累积情绪。进程内常驻，带惯性 —— 连续几轮甜蜜会慢慢热起来，
        # 不会因为一句平淡的话瞬间冷掉。重启会重置回默认，
        # 这是有意的：隔了一天再开口，本来也该是平常状态。
        self.mood = mood.Mood()

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
        # 待办。**2026-08-18 起读 bridge 的本地清单**（前端 todo 是唯一活清单，
        # GitHub todo.md 退役为只读存档，见 Todo-Daily-Planner-设计.md）。
        # 没配 bridge 才回退去读 GitHub —— 留着兜底，不删代码。
        if self.bridge is not None:
            self.context.register(TodoProvider(bridge=self.bridge))
            logger.info("TodoProvider 读本地清单（bridge）")
        elif self.cfg.todo_repo:
            self.context.register(TodoProvider(
                repo=self.cfg.todo_repo, path=self.cfg.todo_path,
                token=self.cfg.github_token))
            logger.info("TodoProvider 回退读 GitHub todo.md（没配 bridge）")
        else:
            logger.info("既没有 bridge 也没有 NOX_TODO_REPO，跳过 TodoProvider")
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

    def _dynamic(self, text: str, voice: bool, scene: str | None = None,
                 has_images: bool = False) -> str:
        """组装每轮可变的提示：情绪 +（语音模式下）该情景的通话指令。

        两者都走 dynamic_system，跟在缓存断点之后 —— 静态前缀一个字都不能变。
        """
        # 情绪现在走 Context 框架出来（MoodProvider），输出与原来的
        # mood.render() 逐字相同 —— tests/test_mood_provider.py 拿 1000 组基线守着。
        # 以后加 time / memory / home，只要往这个名单里添名字；
        # 加载谁由 Router 决定，别在这里写死（架构文档第八节）。
        # 加载哪些 Provider 由 Router 决定，不在这里写死（架构文档第八节）。
        # 闲聊只有 time+mood（都是本地计算）；提到家电才拉 home，
        # 问到身体才拉 health。轻量路径强制最小集 —— 那条路的意义就是快。
        light = classify(text, has_images=has_images).light
        names = classify_context(text, light=light)
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
        span = self._session_span()
        if span:
            parts.append(span)
        return "\n\n".join(parts)

    def _session_span(self) -> str:
        """「这段对话是 X 天前开始的」。

        历史里的日期分隔线已经写了开始日期，这里再给一个**算好的差值** ——
        糖糖 2026-08-11 定的原则：换算不交给模型，他心里没有"现在"。
        """
        started = self.current_session_started
        if started is None:
            return ""
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        days = (now_cst().date() - started.astimezone(CST).date()).days
        if days <= 0:
            return ""
        return f"【这段对话】从 {humanize(days)}开始，中间聊聊停停"

    def chat(
        self,
        text: str,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        voice: bool = False,
        scene: str | None = None,
        model: str | None = None,
    ) -> RouteResult:
        text, images = self._see(text, images, model)
        # 三层情绪渲染成一段动态提示，跟在缓存断点之后发 ——
        # 每轮都变，绝不能混进静态前缀（见 personality/mood.py 开头）
        dynamic = self._dynamic(text, voice, scene, has_images=bool(images))
        result = self.router.handle(
            text, history, dynamic_system=dynamic, images=images,
            voice=voice, scene=scene, adapter=self.adapter_for(model),
        )

        # 模型在回复末尾附了 [mood:xxx]，取出来更新状态并从正文剥掉。
        # 忘了附不算错 —— 保持上一轮的情绪即可，不要因此报错或重试。
        cleaned, detected = mood.extract(result.text)
        if detected:
            self.mood.update(detected)
            logger.debug(
                "情绪 → %s | valence=%.2f arousal=%.2f",
                detected, self.mood.valence, self.mood.arousal,
            )
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
    ):
        """流式版 chat。

        只走完整路径 —— 轻量路径本来就两三秒，为它再铺一套流式不划算，
        而且短回复流式反而闪。想省钱走 chat()，想要即时反馈走这条。

        情绪标记的处理跟非流式不同：那边等文本收全了统一剥，这边靠
        MoodTagFilter 边流边挡（见 loop.run_stream）。收尾时再从完整
        文本里取一次情绪，更新累积状态。
        """
        text, images = self._see(text, images, model)
        dynamic = self._dynamic(text, voice, scene, has_images=bool(images))
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
        ):
            if ev.type == "done":
                result = getattr(ev, "result", None)
                if result is not None:
                    cleaned, detected = mood.extract(result.text)
                    if detected:
                        self.mood.update(detected)
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
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
