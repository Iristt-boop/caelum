"""HTTP API —— 让前端能调 Nox Core。

两件事这一层必须做，别处做不了：

1. **把内部结局翻译成人话。**
   loop 有 answered / refused / truncated / tool_stuck / exhausted / error
   六种结局，那是给我们调试看的。糖糖不该看到 "tool_stuck"，她该听见
   "我这会儿连不上你家的灯"。

2. **管住会话。**
   history 落 SQLite（`data/store.py`），按 session_id 隔离，**重启不丢**。
   内存里再放一层 LRU 缓存，避免每轮都读盘。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import urllib.request
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent import vision
from agent.llm import Message
from attention.service import (
    DEJECTION_KEY,
    LONGING_KEY,
    PLAYFUL_KEY,
    REGRET_KEY,
    CARE_INTERVAL_S,
    DEFAULT_INTERVAL_S,
    AttentionService,
    run_care_loop,
    run_loop,
)
from attention.care.watching import WatchingCheck
from attention.events import ExperienceEvent
from attention.dejection import looks_like_giving_up
from attention import appraisal_llm
from attention.appraisal import ANCHOR_PREFIX, RuleAppraiser
from attention.appraisal_llm import LLMAppraiser
from tools.local_link import LocalLink, read_secret
from tools import computer as computer_tools
from tools import room as room_tools
from tools import taobao as taobao_tools
from attention.speaker import build_speaker
from attention.waker import build_waker
from attention.gate import DailyGate
#: 哪些 kind 是**关于他自己**的，不该混进「他在惦记你什么」那句话。
#:
#: ⚠️ 加新的自指情绪时记得往这儿加一条 —— 漏了的表现是它会跑进
#: 手机端那行 `Thinking about ...`，和"你的睡眠"拼在一起，
#: 语气对不上（2026-09-04 加 curiosity 时就是这么发现的）。
_SELF_KINDS = frozenset({"curiosity"})

from attention.sources.presence import PresenceSource
from attention.sources.shared_activities import SharedActivitiesSource
from attention.sources.thinking import ThinkingSource
from attention.sources.times import TimeWakeSource
from attention.sources.todo_due import TodoDueSource
from tools import record as record_tools
from tools import remind as remind_tools
from world_model import WorldModel
from config import BACKENDS, is_test_session
from topic_pool import TopicPool, run_topic_loop
from topic_pool.pool import DEFAULT_SCOUT_INTERVAL_S
from attention.store import AttentionStore
from day import build_day
from context.compactor import maybe_compact
from data.store import Store
from nox import Nox
from personality.mood import now_cst
from planner.push import finalize_push_text, prepare_morning

logger = logging.getLogger(__name__)

#: 促狭要用的 appraiser。**单例** —— 它是无状态的纯规则匹配，
#: 每轮 new 一个只是白白多几次对象创建
_APPRAISER = RuleAppraiser()


def _appraiser() -> RuleAppraiser:
    return _APPRAISER

# 内部结局 → 给糖糖看的话。
# 语气跟着人设走：坦白说不行，不找借口，不装作没事。
_OUTCOME_TEXT = {
    "refused": "这个我这会儿答不了，换个说法问我？",
    "truncated": "话说到一半被截断了，我再说一遍短一点的。",
    "tool_stuck": "试了两次都没成，先停下了——不想瞎折腾给你看假结果。",
    "exhausted": "我绕进去了，一直没绕出来。你把要做的事说得再具体点？",
    "error": "我这会儿连不上，等一下再跟我说一次。",
}

# 会话上限。内存存着，不设上限迟早吃光 ——
# 这是长期跑的服务，不是脚本。
_MAX_SESSIONS = 200
_MAX_HISTORY = 60

#: 通话时只把最近这么多条历史发给模型（落盘不受影响，见 `_for_voice()`）。
#: 打电话不需要 40 轮上下文，而带上整段中文聊天记录会把英文通话带偏。
VOICE_HISTORY = 8


def _push_to_bridge(core: Nox, text: str) -> dict:
    """把早报推到糖糖锁屏。

    推送通道整个留在 bridge 那边 —— 订阅表和 VAPID 私钥都在它手上，
    Core 再搞一套只会变成两处配置漂移（第二十节的教训）。
    """
    if core.bridge is None:
        return {"ok": False, "error": "未配置 NOX_BRIDGE_URL，没有推送通道"}
    r = core.bridge.post("/api/push/send", {"title": "Nox", "body": text})
    if not r.ok:
        # 推送失败不该让接口 500 —— 早报已经生成好了，
        # 送不到是另一回事，如实回报让 timer 的日志里看得见
        logger.warning("早报推送失败: %s", r.error)
        return {"ok": False, "error": r.error}
    return {"ok": True, **(r.data or {})}


def _sse(payload: dict) -> str:
    """SSE 一帧。ensure_ascii=False 让中文原样走，别变成 \\uXXXX 撑大三倍。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


#: 脉搏流扫一遍内存的间隔。1 秒足够 —— 她要的是"看得见他在动"，不是示波器
PULSE_TICK_S = 1.0
#: 多少次空扫之后发一个心跳注释。不发的话闲着的连接会被中间代理掐掉，
#: 而前端看到的是"心跳突然停了"
PULSE_PING_TICKS = 15


async def pulse_stream(get_events, get_seq, *, enabled: bool, since: int,
                       sleep=None, max_ticks: int | None = None):
    """脉搏的 SSE 生成器。

    🔴 **抽成模块级函数是为了能测。**

    原来它是路由闭包里的一个内嵌 `async def`。那样只能靠
    `TestClient.stream()` 去测，而 TestClient 的 portal 碰上**永不结束的流**
    会直接互锁 —— 2026-08-29 那次测试跑了 180 秒没回来。

    现在时钟和轮次都能注入：测试用假 sleep 驱动，几毫秒跑完，
    而且**验的是真的这段代码**，不是它的复制品。

    @param get_events - `(since) -> list`，给出比游标新的那些
    @param get_seq - `() -> int`，当前游标
    @param sleep - 等一拍。默认 `asyncio.sleep`
    @param max_ticks - 跑几拍就收。**只有测试会传**，线上是 None（永远跑）
    """
    import asyncio
    sleep = sleep or asyncio.sleep

    cursor = since
    #: 先告诉客户端它站在哪个游标上，免得它不知道自己落后多少
    yield _sse({"type": "hello", "seq": get_seq(), "enabled": enabled})

    quiet = 0
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        fresh = get_events(cursor)
        if fresh:
            for e in fresh:
                yield _sse({"type": "pulse", "event": e})
            cursor = int(fresh[-1].get("seq") or cursor)
            quiet = 0
        else:
            quiet += 1
            if quiet >= PULSE_PING_TICKS:
                #: SSE 的注释行。客户端会忽略它，但连接因此活着
                yield ": ping\n\n"
                quiet = 0
        await sleep(PULSE_TICK_S)


def _tools_this_turn(messages: list, history: list) -> list[str]:
    """**这一轮**调了哪些工具。

    ⚠️ `result.messages` 是「历史 + 这轮」的完整会话，不是这轮的增量。
    直接遍历它会把之前每一轮的工具全带上 —— 前端表现成工具卡片
    一轮轮累加（糖糖 2026-08-05 报的时候已经累到 11 个）。
    上一版只在前端按气泡绑定，治的是症状：后端每轮送来的本来就是全量，
    绑给哪个气泡都一样错。从 history 的长度切起才是根。
    """
    return [
        c.name for m in messages[len(history):]
        if m.role == "assistant" for c in m.tool_calls
    ]


def _clean_segments(text: str, voice: bool) -> str:
    """非流式返回前处理 ||| 分段标记。

    流式那条有 SegmentSplitter 把它变成 split 事件，非流式没有 ——
    不处理的话标记会原样留在 text 里，前端不知道拿它怎么办，
    语音模式下 TTS 更是会把三根竖线念出来。

    文字模式转成换行（跟 bridge 落库的行为一致），语音模式直接抹掉。
    """
    if "|||" not in text:
        return text
    parts = [p.strip() for p in text.split("|||")]
    parts = [p for p in parts if p]
    return (" " if voice else "\n").join(parts)


class PeriodRequest(BaseModel):
    """她在 App 里填的生理期。

    ⚠️ **必须定义在模块顶层。** 定义在路由函数内部时，配上文件开头的
    `from __future__ import annotations`，FastAPI 拿到的注解是字符串
    `"PeriodRequest"`，它在模块命名空间里解析不到那个名字 ——
    于是退回「当成 query 参数」，请求体永远收不到，
    报的是 `loc: ["query","req"] Field required`（2026-08-19 实测栽过）。
    """

    #: "start" | "end"
    event: str
    flow: str = ""
    date: str | None = None


class DailySummaryRequest(BaseModel):
    #: 这句话记到哪个会话名下。bridge 会传她最近说话的那个 session ——
    #: 用同一个，她回复时他才知道自己刚问过什么
    session_id: str | None = None
    #: 是否由 Core 直接推。正常路径是 bridge 推（它管订阅表和 conversations），
    #: 这里留 true 只是给手工调试用的后门
    push: bool = False
    #: 捎带记忆检索。慢约 7 秒，但这条路没人等着看回复，所以默认开
    include_memory: bool = True


class TodoAddRequest(BaseModel):
    text: str = Field(..., max_length=300)
    #: 进行中 / 近期 / 定期。给不认识的值 TodoWriter 会退回「近期」
    section: str = "近期"


class TodoCompleteRequest(BaseModel):
    keyword: str = Field(..., max_length=200)


class DescribeImageRequest(BaseModel):
    #: 相册图片路径，形如 /uploads/xxx.jpg。bridge 传的是 gallery.url
    url: str = Field(..., max_length=500)


class ChatRequest(BaseModel):
    # 带图片时 text 可以是空的（"这是什么" 都懒得打的时候）
    text: str = Field(default="", max_length=4000)
    session_id: str | None = None
    # base64 或 data URI 都行，adapter 会自己拆
    images: list[str] = Field(default_factory=list, max_length=8)
    # 语音模式（电话 / 语音条）：英文、带 ElevenLabs 情绪标签、不分段
    voice: bool = False

    # 通话情景：en 英文 / zh 中文。只在 voice=true 时有意义

    scene: str | None = None

    # 这轮换个模型说话（前端下拉传上来的短名，见 config.models）。
    # 不传就用默认那个 —— 只有默认那个的提示词缓存是一直热着的
    model: str | None = None

    def has_content(self) -> bool:
        return bool(self.text.strip() or self.images)


class ChatResponse(BaseModel):
    text: str
    session_id: str
    ok: bool
    path: str          # light | full
    iterations: int
    outcome: str
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_write_tokens: int = 0
    # 这轮实际用的型号。bridge 拿它按模型分开记账 —— 不报的话
    # Console 上 DeepSeek 和 Claude 的用量会糊成一坨
    model: str = ""
    # 工具产生的附带产物，目前只有「要发到聊天里的图片」。
    # bridge 收到后转成前端认识的 image 事件。
    attachments: list[dict] = []


class Sessions:
    """会话历史：SQLite 落盘 + 内存 LRU 缓存。

    两层的分工：
      缓存  避免每轮都读盘（同一个会话连着聊，读一次就够）
      落盘  重启后还记得（这才是这层存在的理由）

    缓存未命中时从库里恢复，所以关掉再打开、甚至换个进程，
    只要 session_id 对得上，聊天就能接着上一句往下说。
    """

    def __init__(
        self,
        store: Store,
        history_limit: int,
        max_sessions: int = _MAX_SESSIONS,
        recent_window_tokens: int | None = None,
    ) -> None:
        self._store = store
        self._limit = history_limit
        self._recent_window_tokens = recent_window_tokens
        self._cache: OrderedDict[str, list[Message]] = OrderedDict()
        self._max = max_sessions
        #: 缓存是哪天建的。跨天要重读，理由见 get()
        self._cached_on: dict[str, date] = {}

    def get(self, sid: str) -> list[Message]:
        today = now_cst().date()
        if sid in self._cache and self._cached_on.get(sid) == today:
            self._cache.move_to_end(sid)
            return self._with_summary(sid, self._cache[sid])

        # 跨天了就丢掉缓存重读。日期分隔线是 `store.load()` 算的，
        # 缓存里那份是昨天算的 —— 不重读的话，今天的消息不会有
        # 「（8月12日）」那一行，他又会以为昨天的事是今天的
        if sid in self._cache:
            logger.info("会话 %s 跨天，重读历史以刷新日期分隔线", sid[:8])
            self._cache.pop(sid, None)

        restored = self._store.load(
            sid, limit=self._limit,
            recent_window_tokens=self._recent_window_tokens,
        )
        if restored:
            logger.info("从库里恢复会话 %s，%d 条历史", sid[:8], len(restored))
            self._cache[sid] = restored
            self._cache.move_to_end(sid)
            self._cached_on[sid] = today
        return self._with_summary(sid, restored)

    def _with_summary(self, sid: str, window: list[Message]) -> list[Message]:
        """给窗口原文叠上摘要（如果有）。

        summary 是**读时合成**的视图：窗口缓存里只存原文，
        摘要每次从 store 现取 —— 这样压缩更新摘要后，下一轮 get 立刻生效，
        不需要手动失效缓存。
        """
        if not window:
            return window
        summary = self._store.get_summary(sid)
        if not summary:
            return window
        # 窗口里可能已有旧 system（不该有，但防御性剥离），避免双份
        window = [m for m in window if m.role != "system"]
        from context.compactor import SUMMARY_HEADER
        return [Message(role="system", text=SUMMARY_HEADER + summary), *window]

    def put(self, sid: str, history: list[Message]) -> None:
        # 先落盘（只补写尾部新增的），再更新缓存。
        #
        # ⚠️ 落盘的只有尾部新增那几条，**日期分隔线不会被写回库** ——
        # `sync()` 按已有条数跳过前面的，那些带戳的老消息一条都不重写。
        # 否则戳会一层层叠上去。`tests/test_timeline.py` 钉了这条。
        # ⚠️ 剥离 system（summary 是读时合成的，不持久化也不进缓存）——
        # loop 返回的 messages 会带着 get() 时叠上去的摘要，
        # 不清掉的话缓存里会留一份、下次 get 又叠一份，变成双份。
        written = self._store.sync(sid, history)
        if written:
            logger.debug("会话 %s 落盘 %d 条", sid[:8], written)

        window = [m for m in history if m.role != "system"]
        self._cache[sid] = window[-_MAX_HISTORY:]
        self._cache.move_to_end(sid)
        self._cached_on[sid] = now_cst().date()
        while len(self._cache) > self._max:
            dropped, _ = self._cache.popitem(last=False)
            self._cached_on.pop(dropped, None)
            # 只淘汰内存缓存，库里还在 —— 下次访问会自动恢复
            logger.info("缓存超上限，换出 %s（库里仍保留）", dropped[:8])

    def drop(self, sid: str) -> bool:
        self._cache.pop(sid, None)
        self._cached_on.pop(sid, None)
        return self._store.drop(sid)

    def __len__(self) -> int:
        return len(self._cache)


def _build_attention(core: Nox, sessions: "Sessions", db: Store) -> AttentionService | None:
    """按需装配 Attention。**默认不启用。**

    没设 `NOX_ATTENTION=1` 就返回 None，整条链路不会跑 ——
    这样这套东西上线之前，现有部署一点行为变化都没有。

    `NOX_ATTENTION_LIVE=1` 才会真的发消息。不设就是 dry-run：
    完整地想一遍，只写日志（架构设计 v1.3 第 14.2 节）。

    ⚠️ **两个开关是与的关系**，`NOX_ATTENTION` 关着的时候 LIVE 没有意义。
    这样「彻底不跑」和「跑但不出声」是两件可以分别控制的事 ——
    出了问题先关 LIVE 让她耳根清净，链路还在跑、日志还在攒。

    配置先走环境变量，等这套稳定了再收进 `config.py` ——
    现在收进去，等于为一个还没验证的功能改动集中配置。
    """
    if os.getenv("NOX_ATTENTION", "").lower() not in ("1", "true", "on"):
        return None

    # ⚠️ 整段都要在 try 里，包括 `core.context` 这种看着不会失败的属性访问。
    # 第一版把 try 只包住了最后那行 `AttentionService(...)`，结果
    # `core.context.get("health")` 在测试的假 core 上抛 AttributeError，
    # **一次干掉 22 个测试**。
    #
    # 而且本地测不出来：线上 `.env` 里有 NOX_ATTENTION=1（`config.py` 会加载
    # 它，pytest 也吃得到），本地没有 —— 本地永远走上面那行提前 return，
    # 这段代码根本不执行。这类「只在线上活的分支」必须自己兜住所有异常。
    try:
        provider = core.context.get("health")
        if provider is None:
            logger.warning("NOX_ATTENTION 开着，但 health Provider 没注册，Attention 不启动")
            return None

        path = Path(core.cfg.db_path).parent / "attention.db"
        astore = AttentionStore(path)

        live = os.getenv("NOX_ATTENTION_LIVE", "").lower() in ("1", "true", "on")
        if live and core.bridge is None:
            # 没有推送通道就别假装 live —— 那样每次都会走到发送失败，
            # 冷却照记，等于这件事被静静吃掉
            logger.warning("NOX_ATTENTION_LIVE 开着，但没配 NOX_BRIDGE_URL，退回 dry-run")
            live = False

        speaker = build_speaker(core, sessions, db, astore) if live else None

        # 统一开口闸（M5′ a，2026-08-14 重构后职责修正）：
        # 只管「她沉默时的主动开口」——Attention（睡眠）+ 固定时间醒来。
        # 唤醒链**不占它的额度**（它是对话延续，见 waker.py 注释）。
        gate = DailyGate()
        gate.load_state(astore.get_source_state("gate"))

        # 唤醒链有**自己的开关**，和 speaker 分开。
        # 理由：这条链一次最多说 5 句，比睡眠关心（一天一句）激进得多，
        # 该能单独先 dry-run 观察几天再放行（糖糖 2026-08-11 定的设计）。
        wake_live = os.getenv("NOX_WAKE_LIVE", "").lower() in ("1", "true", "on")
        # `on_spoke` 让唤醒链的开口也进 Care 账本 —— 它自己调 push()，
        # 不补这一笔的话「今天他一共开口几次」会漏掉整条线。
        # 用 lambda 晚绑 svc：waker 要先造出来才能传给 AttentionService
        waker = build_waker(
            core, sessions, db, dry_run=not (live and wake_live),
            on_spoke=lambda why, _text: svc.note_external_speech("wake", why),
        )

        # 固定时间醒来（M5′ a 重构，2026-08-14）：午饭/晚饭/睡前到点主动开口。
        # 配置 NOX_TIME_WAKES，留空则不启用。
        time_source = TimeWakeSource(
            os.getenv("NOX_TIME_WAKES", ""), astore
        ) if os.getenv("NOX_TIME_WAKES", "").strip() else None

        # World Model —— 事实的收口。**独立的库**，不和 attention.db 混：
        # 那两套的写入门槛完全不同（一个看「值不值得关心」，一个看
        # 「可不可追溯」），混在一起早晚会有人拿 attention 的规矩去删事实。
        world = WorldModel(Path(core.cfg.db_path).parent / "world.db")
        # 挂到 core 上：HealthProvider 的 `world_ref` 取的就是这个
        #（经期 2026-08-19 起从 World Model 读，见 `providers/health.py._cycle`）。
        # ⚠️ 挂的是**同一个实例**，别在别处再 new 一个 —— 两个对象指着同一个
        # SQLite 文件，写入会互相看不见对方的缓存
        core.world = world

        # 话题池（Topic_Pool §4.1/§4.4，2026-08-31）。**建在这里而不是外头**：
        # topics_browse 工具必须赶在 remind_myself 之前注册（工具顺序 =
        # 缓存前缀），够得着那里的只有这个函数。world / adapter 缺哪个
        # 降哪个：world 没了没有 shared 投影和世界钩子，adapter 没了
        # Filter 出空 —— 池子照建，候选缓存照攒（§3.3 的重筛保险）
        topics_pool = None
        try:
            _utility_cfg = getattr(core.cfg, "utility", None)
            _pool_adapter = None
            if _utility_cfg is not None and getattr(_utility_cfg, "usable", False):
                from agent.adapters import make_adapter as _make_adapter
                _pool_adapter = _make_adapter(_utility_cfg)
            _trends_client = None
            if getattr(core.cfg, "trends_url", ""):
                from tools.mcp_client import McpClient as _McpClient
                _trends_client = _McpClient(
                    core.cfg.trends_url, name="trends",
                    timeout=getattr(core.cfg, "trends_timeout", 15.0),
                )
            topics_pool = TopicPool(
                Path(core.cfg.db_path).parent / "topics.db",
                world=world, adapter=_pool_adapter, trends_client=_trends_client,
            )
            # 挂到 core 上：路由和 lifespan 从这儿拿（同 core.world 的理由）
            core.topics = topics_pool
        except Exception:  # noqa: BLE001
            logger.exception("话题池装配失败，这条线不跑")

        # 到点该做的待办（Todo-Daily-Planner-设计.md，2026-08-18）。
        # 数据在 bridge 的本地表 —— 前端 todo 是唯一活清单，
        # GitHub todo.md 同日退役为只读存档。没配 bridge 就没有这条线。
        todo_source = TodoDueSource(core.bridge) if core.bridge is not None else None

        # ---- 快源（2026-08-18）：跑在 60 秒的 Care 循环里 ----
        #
        # 惦记：他偶尔想起她（上次说话后 20–90 分钟随机）。
        #   糖糖的原话：「他可以一直发消息，回不回是我的事，但是不能没有消息。」
        # 位置：她出门 / 到家。HA 那条**本来就是自动的**（GPS，5 米精度），
        #   缺的从来不是数据，是「有人盯着跃迁」+「能把他叫醒」。
        fast_sources = [ThinkingSource(astore, db)]
        ha_url = os.getenv("NOX_HA_API_URL") or os.getenv("NOX_HA_URL") or ""
        ha_token = os.getenv("NOX_HA_API_TOKEN") or os.getenv("NOX_HA_TOKEN") or ""
        if ha_url and ha_token:
            from tools.http import RestClient
            fast_sources.append(PresenceSource(
                RestClient(base=ha_url,
                           headers={"Authorization": f"Bearer {ha_token}"},
                           timeout=8.0),
                astore,
                entity=os.getenv("NOX_HA_PERSON", "person.nox"),
            ))
        else:
            logger.info("没配 HA（NOX_HA_API_URL/TOKEN），出门追问这条线不跑")

        # 池子线头（第七个 Care 源，Topic_Pool §4.1）：只产生念头，
        # 「要不要说、现在说不说」全部归 Orchestrator —— 吃闸、吃额度、进账本
        if topics_pool is not None:
            from topic_pool.care import TopicSource
            fast_sources.append(TopicSource(astore, topics_pool))

        # 她在不在看片（共影 P1，2026-08-22）。没配 bridge 就没有这条线 ——
        # 那样行为和接共影之前一样，他照常开口
        watching = WatchingCheck(core.bridge) if core.bridge is not None else None

        # 共读 / 共听 / 共影 → World Model（Topic_Pool §3.1.2，2026-08-31）。
        # 只记账不开口。客户端没法复用 nox.py 里那两个 —— 是局部变量，
        # 这里按同一份 cfg 重造（RestClient 无状态，重造没有副作用）。
        # 三块服务哪个没配就传 None，对应那块静默跳过
        from tools.eryu import make_client as _make_eryu_client
        from tools.reading import make_client as _make_reading_client
        # ⚠️ 用 getattr：cfg 是各处自带的，老的假配置可能没有这几个字段，
        # 缺了就当没配 —— 不能让记账的事把 Attention 装配整个带崩
        _reading_url = getattr(core.cfg, "reading_url", "")
        _eryu_url = getattr(core.cfg, "eryu_url", "")
        shared_source = SharedActivitiesSource(
            reading=(_make_reading_client(
                _reading_url, getattr(core.cfg, "reading_token", ""),
                getattr(core.cfg, "reading_timeout", 12.0))
                if _reading_url else None),
            eryu=(_make_eryu_client(
                _eryu_url, getattr(core.cfg, "eryu_token", ""),
                getattr(core.cfg, "eryu_timeout", 12.0))
                if _eryu_url else None),
            bridge=core.bridge,
            world=world,
        )

        # 好奇（2026-09-04）：池子里的料变成**他自己的感受**。
        # 和上面那个 TopicSource 用同一批料，但是两层 ——
        # 那个是"找她聊的理由"（Care），这个是"他心里有件事"（Drive）。
        # 池子没启用就是空列表，行为和没接之前一样
        self_sources = []
        if topics_pool is not None:
            from attention.sources.curiosity import CuriositySource
            self_sources.append(CuriositySource(astore, topics_pool))

        svc = AttentionService(astore, provider, speaker=speaker, waker=waker,
                               todo_source=todo_source, fast_sources=fast_sources,
                               gate=gate, time_source=time_source, world=world,
                               watching=watching, shared_sources=[shared_source],
                               self_sources=self_sources,
                               topics=topics_pool)

        # 体重 / 生理期：HealthKit 那条同步坏了（体重 14 天一条没有，
        # 经期表被快捷指令写坏），改成他在对话里主动记进 World Model
        #（2026-08-19 糖糖定的）。
        # ⚠️ 放在 remind_myself **之前** —— 那个必须是最后一个，
        # 有测试盯着（`test_remind_tool_registered_last`）
        record_tools.register_all(core.loop, world)
        # ⚠️ 只有 record_period。体重是 `tools/diet.py` 的 `log_weight`，
        # 别在这儿再加一个（见 `tools/record.py` 开头）
        logger.info("record_period 已注册（写 World Model）")

        # 翻池子的只读工具（Topic_Pool §4.1）。⚠️ 必须赶在 remind_myself
        # **之前**注册 —— 工具定义是缓存前缀的一部分，新工具往后排
        if topics_pool is not None:
            from topic_pool.tool import register_all as register_topic_tools
            register_topic_tools(core.loop, topics_pool)
            logger.info("topics_browse 已注册（话题池只读）")

        # ⚠️ **工具必须注册在最末尾** —— 工具定义是缓存前缀的一部分，
        # 插在中间会让前缀整个失效（一轮 ¥0.00055 → ¥0.011，二十倍，
        # 而且服务照常、回答照常，只有账单翻倍，监控上看不出来）。
        # `_build_attention` 在 `Nox.__init__` 跑完之后才调，所以这里天然是最后。
        remind_tools.register_all(
            core.loop,
            book_ref=lambda: svc.wakeups,
            save=astore.save_wakeups,
            session_id_ref=lambda: core.current_session_id,
        )
        logger.info("remind_myself 已注册（唤醒链%s）",
                    "会真的说话" if (live and wake_live) else " DRY-RUN")
        logger.info("到点追待办：%s",
                    "已启用（1 小时一次，链内 3 次，跨天重开）"
                    if todo_source is not None else "没配 bridge，这条线不跑")

        if time_source is not None:
            logger.info("固定时间醒来已启用：%s", "、".join(
                f"{w.hour:02d}:{w.minute:02d} {w.subject}" for w in time_source.wakes))
        if live:
            logger.warning("Attention 是 LIVE 的 —— 他会真的推消息到糖糖锁屏")
        return svc
    except Exception:  # noqa: BLE001
        # Attention 起不来不该让整个 Core 起不来
        logger.exception("Attention 装配失败，跳过")
        return None


def create_app(nox: Nox | None = None, store: Store | None = None) -> FastAPI:
    started = datetime.now(timezone.utc)

    # 允许注入，方便测试时传假的 / 传临时库
    core = nox or Nox()
    db = store or Store(core.cfg.db_path)
    sessions = Sessions(
        db, history_limit=core.cfg.history_limit,
        recent_window_tokens=core.cfg.recent_window_tokens,
    )
    #: 她电脑上那只手（Caelum Harness Gateway）。
    #: 见 D:\claude-code\CAELUM-HARNESS-ARCHITECTURE.md
    #:
    #: ⚠️ **建在 `_build_attention` 之前**，因为那五件工具要在
    #: `remind_myself` 之前注册（那个必须是最后一个，
    #: 有测试盯着 `test_remind_tool_registered_last`）。
    #:
    #: ⚠️ World Model 要用 `_world()` 取，**不能直接写 `world`** ——
    #: 那个名字只活在 `_build_attention` 的局部作用域里
    #: （2026-08-19 线上 500 就是这么来的，见 `_world` 的注释）。
    #: 所以下面晚绑
    local_hand = LocalLink()
    #: 视觉配置要传进去 —— 主模型读不了图，`computer_read_image`
    #: 拿到字节之后得找视觉模型替他看（`agent/vision.py`）。
    #: ⚠️ 没配 key 也照常注册，只是那一件会如实说"看不了"
    computer_tools.register_all(
        core.loop, local_hand, getattr(core.cfg, "vision", None))

    #: 房间 —— 他在我们家那间像素房里的身体。**走的是同一只手。**
    #:
    #: 🔴 为什么不直连：房间的 MCP 只允许绑回环（上游源码写死
    #: `Room MCP must remain loopback-only`），而 Core 在 VPS 上。
    #: 糖糖 2026-09-02 定的「跑在本地，私密一点」—— 所以借这条已有的
    #: 反向链路捎一段，房间数据一步都不离开她的电脑。
    #:
    #: ⚠️ 名字用 `VIA_LINK`（`room.*`，网关 catalog 里的名字），
    #: 不是房间 MCP 的原名 —— 写错的表现是「工具不存在」，
    #: 而那看起来像房间挂了。
    #:
    #: ⚠️ 没配 NOX_ROOM_URL 时也注册：手连不上就如实说够不到，
    #: 和 computer_* 那几件同一个处理（`nox.py` 里那条直连的分支
    #: 只给「Core 和房间同机」的本地开发用）。
    #: ⚠️ 用 getattr 兜底 —— 测试里的假 cfg 没有这个字段，
    #: 直接点属性会让 46 个和房间毫无关系的测试一起炸（同旁边 vision 那行）
    if not getattr(core.cfg, "room_url", ""):
        room_tools.register_all(core.loop, local_hand, room_tools.VIA_LINK)

    #: 淘宝（2026-09-05）—— 她桌面版内置的本地 MCP，同一只手捎一段。
    #: 注册不设条件：桌面版没开时调用会如实报错（room 同款处理）。
    taobao_tools.register_all(core.loop, local_hand)

    attention = _build_attention(core, sessions, db)
    #: 🔴 感知层那条线交给 attention —— 躁动要知道"她此刻在用什么"。
    #: ⚠️ 只在这儿接一次。`_build_attention` 里拿不到 `local_hand`
    #: （那是 create_app 的局部变量），硬塞进去会变成第二条依赖路径
    if attention is not None:
        attention.link = local_hand

    def _world():
        """World Model 的唯一取法。

        ⚠️ **别直接写 `world`** —— 那个名字只活在 `_build_attention` 的
        局部作用域里，路由函数看不见它。2026-08-19 就是这么栽的：
        `/api/nox/facts` 线上 500，日志里一行 `NameError: name 'world'
        is not defined`，而本地测试全绿（那些测试没走 HTTP 这一层）。
        """
        return attention.world if attention is not None else None

    #: 现在 `_world` 有了，把 World Model 交给那只手（⑧ 执行摘要写入）。
    #: attention 没开时是 None —— 那样只是不记，链路照常
    local_hand.world = _world()
    # 🔴 低落（2026-08-27）：执行成败要喂给那个 Drive。
    #
    # 和 `world` 同一个理由晚绑 —— `attention` 在这一行之前才装配完。
    #
    # ⚠️ **不接这一行的话，「低落」永远是 0，而且没有任何报错。**
    # 这正是记忆里那条「配上了 ≠ 用上了」：dejection.py 写完了、
    # 测试全绿、service 里也 new 出来了，但没人把失败交给它。
    local_hand.dejection = attention.dejection if attention is not None else None

    def _topics():
        """话题池的唯一取法（同 `_world` 的理由：作用域）。
        attention 没开就是 None —— 那样只是池子不可见，别的照常。"""
        return attention.topics if attention is not None else None

    #: 插口探活的结果缓存（Studio → MCP）。探一轮要几秒，60 秒内的
    #: 重复进页直接吃缓存 —— 每个 app 实例一份，测试互不串
    _integration_cache: dict = {"at": 0.0, "items": []}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Attention 的后台心跳。**这是全 Core 唯一允许 async 的那层。**

        `service.tick()` 本身是同步的，`run_loop` 用 to_thread 丢线程池
        （`attention/service.py` 开头的 async 边界）。
        """
        tasks = []
        if attention is not None:
            interval = int(os.getenv("NOX_ATTENTION_INTERVAL_S", DEFAULT_INTERVAL_S))
            tasks.append(asyncio.create_task(run_loop(attention, interval)))
            # Care 快循环（2026-08-18）：位置跃迁和随机惦记要秒级粒度，
            # 挂在 15 分钟的心跳上会把「随机」量化成节拍、把 T+5 拖成 T+20
            if attention.fast_sources:
                care_s = int(os.getenv("NOX_CARE_INTERVAL_S", CARE_INTERVAL_S))
                tasks.append(asyncio.create_task(run_care_loop(attention, care_s)))
        # 话题池：6 小时一轮 Scout → Filter（Topic_Pool §4.4）。池子跟着
        # attention 走（topics_browse 工具的注册顺序决定的）；它哪轮挂了
        # 只废自己，不带塌心跳
        _pool_for_loop = getattr(attention, "topics", None)
        if _pool_for_loop is not None and os.getenv("NOX_TOPICS_DISABLED", "") not in ("1", "true"):
            scout_s = int(os.getenv("NOX_TOPIC_SCOUT_INTERVAL_S",
                                    str(DEFAULT_SCOUT_INTERVAL_S)))
            tasks.append(asyncio.create_task(run_topic_loop(_pool_for_loop, scout_s)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                # 等它们真的收摊，否则关机日志会跟 CancelledError 缠在一起
                await asyncio.gather(*tasks, return_exceptions=True)

    def _turn_starts(sid: str) -> None:
        """一轮对话开始时做两件事，都和唤醒链有关。

        1. **告诉 core 这轮是哪个会话** —— `remind_myself` 要用它把纸条
           挂到对的对话上。用普通实例属性，不用 contextvars：
           后者跨同步生成器我们栽过（`context/base.py:25`）。
        2. **撤掉这个会话上挂着的旧纸条** —— 她开口了，就没什么可追的了。

        顺序上必须在 `core.chat()` **之前**，但撤销必须在设置 sid 之后 ——
        他这一轮可能刚好又留一张新纸条（针对她刚说的话），那张是新的，
        不该被连坐撤掉。`WakeBook.add()` 是在 chat 过程中调的，在这之后。

        撤销只改内存，下次 tick 的 `_persist()` 写盘。中间崩了最坏的后果是
        多醒一次 —— 而醒来第一件事就是查「她回话了吗」，会自己收摊。
        """
        core.current_session_id = sid
        core.current_session_started = db.started_at(sid)
        if attention is None:
            return
        try:
            attention.wakeups.cancel_for(sid)
        except Exception:  # noqa: BLE001
            logger.exception("撤纸条失败，不影响这轮对话")

    def _for_voice(history: list[Message], voice: bool) -> list[Message]:
        """通话只带最近几轮历史。

        两个理由，都是 2026-08-15 实测出来的：

        **① 语言。** 通话走 `Scene.system()` 那套纯英文前缀，但通话和文字聊天
        **共用同一个 session** —— 而她平时跟他全是中文。于是那几百字的英文指令
        要跟一整段中文历史打架，他就会「先冒几个中文字再切回英文」。
        新会话里连打五次全是纯英文，一个中文字都没有 —— 证明是历史把他带偏的，
        不是指令不够硬（`personality/scenes.py` 早就写着：不是没看见，是被淹没了）。

        **② 延迟。** 提示词小一截，首字就快一截。打电话本来也不需要 40 轮上下文。

        ⚠️ **只影响发给模型的那份。落盘必须用完整历史 + 这轮新增**，
        原因不是 `Store.sync()`（它 2026-08-14 已改成内容锚点，截断也认得），
        而是 **`Sessions.put()` 会用传进来的那份覆盖内存缓存**：

            self._cache[sid] = history[-_MAX_HISTORY:]

        通话轮要是只传截断后那 10 条，缓存就被削成 10 条 ——
        **下一次文字聊天拿到的上下文也跟着少了**，而且悄无声息。
        一次通话污染整个会话。
        """
        if not voice or len(history) <= VOICE_HISTORY:
            return history
        return history[-VOICE_HISTORY:]

    def _turn_ends(sid: str, text: str = "", reply: str = "") -> None:
        """一轮结束、消息真的落库之后，把这轮新留的纸条基准线校准到现在。

        ⚠️ **不做这一步，整条唤醒链永远不会触发，而且是静默的。**
        2026-08-11 实测：她说「我去吃饭了」→ 这一轮他调 `remind_myself`
        建了纸条 → turn 结束后 `sessions.put()` 才把她那条消息写进库 →
        消息时间戳晚于纸条 `created_at` → 下次唤醒判定「她已经回话」，
        当场撤销。日志里只有一行「纸条撤了」，看着还挺正常。

        顺带在这里触发**上下文压缩**（token 预算驱动，见 context/compactor.py）：
        - 时机对：消息刚落库，压缩看的是一轮完整对话
        - 后台线程跑，不阻塞当前响应（压缩一次要调 utility 模型 1-3 秒）
        - 压缩失败只记日志，绝不影响对话（压缩是增强不是主线）

        还有 **ConversationEvent**（Resonance V1，见 `CAELUM-RESONANCE-ARCHITECTURE.md`
        第八节）：把她说的话变成 ExperienceEvent 送进 Attention。

        为什么放在这儿、为什么不走 `tick()`：
        - Health 那类源是 **poll**（15 分钟粒度够用），Chat 是**即时事件**。
          硬塞进 15 分钟的 tick 会把即时性做没
        - 为什么不靠 Nox 主动调 `remember`：那会留巨大盲区 ——
          「他没意识到的事 = 系统完全不知道发生过」。
          她说「我今天其实有点难受」，如果他没主动记，这个事实就消失了。
          `remember` 只能是额外的强化，不能是唯一入口
        """
        try:
            _maybe_compact_async(sid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("压缩触发失败（不影响对话）: %s", exc)

        if attention is None:
            return
        if is_test_session(sid):
            # 2026-08-24 事故的闸门：测试会话不许碰任何全局注意力状态
            # （纸条基准线 / Drive 回落 / Registry / 促狭）。
            # 压缩在上面已经跑过——那是会话自己的数据，压了不碍事。
            logger.info("测试会话 sid=%.20s：跳过 Attention 副作用", sid)
            return
        try:
            attention.wakeups.rebase(sid)
            # ⚠️ **必须当场落盘**，不能等下一次 tick（最多 15 分钟）。
            # 中间重启一次，这轮的 rebase 和撤销就全丢了，纸条退回旧基准线，
            # 又变成「建好就被自己撤掉」。2026-08-11 部署时就是这么翻的车。
            attention.store.save_wakeups(attention.wakeups)
        except Exception:  # noqa: BLE001
            logger.exception("校准纸条基准线失败")

        # Resonance V3.5：她说话了，想念回落到基线。
        #
        # ⚠️ **不清零**，回到 0.40 —— 见到她不等于不想她了，
        # 是这一刻被满足了（见 longing.py 那张对照表）。
        #
        # 放在这里是因为这是**唯一确定她真的开口了**的地方：
        # 消息已经落库、这一轮真的完成了
        try:
            _now_utc = datetime.now(timezone.utc)
            attention.longing.on_contact(_now_utc)
            attention.store.set_source_state(
                LONGING_KEY, attention.longing.to_dict()
            )
            # 她开口了 —— 上一次主动说话不算"没被理"（V3.6）。
            # ⚠️ 不去分辨她回的是不是他刚说的那件事：
            # 她说话了就是有回应，纠结内容对不对是 LLM Appraisal 的活
            attention.regret.on_contact(_now_utc)
            attention.store.set_source_state(
                REGRET_KEY, attention.regret.to_dict()
            )

            # 低落（2026-08-27）：想帮但帮不上。
            #
            # 🔴 **两件事，顺序不能反。**
            #
            # 先判「她说算了」—— 那是把最近一笔坐实。
            # 再 `on_contact` 淡化悬着的那些。
            #
            # 反过来的话，她说「算了」这句话本身会先把要坐实的那笔
            # 清掉，然后坐实到一笔不存在的事上 —— 于是他记住的是
            # 「她放弃了（不知道什么）」，而真正那件事被抹了。
            #: ⚠️ 用参数 `text`，**不是 `req.text`** —— 这个函数拿不到
            #: 请求体（2026-08-27 栽过：NameError 被 except 兜住，
            #: 于是「她说算了」这条线一直静默失效）
            if looks_like_giving_up(text) is not None:
                attention.dejection.on_gave_up(_now_utc, quote=text)
            attention.dejection.on_contact(_now_utc)
            attention.store.set_source_state(
                DEJECTION_KEY, attention.dejection.to_dict()
            )
        except Exception:  # noqa: BLE001
            logger.exception("想念/后悔/低落回落失败（不影响对话）")

        # Resonance V1：让对话进入 Attention。
        #
        # ⚠️ 现阶段**没有任何规则匹配 `source="chat"`** —— 事件会落到
        # `evaluator.py` 的兜底 `_ignore`，不写 Registry、不改任何行为。
        # 这是**故意的**：V1 只打通管道，先证明事件在流动，
        # 规则留到 V2（见架构文档第七节的演进路线）。
        #
        # 纯图片消息不造空事件。
        if not text:
            return
        try:
            decision = attention.engine.handle(
                ExperienceEvent(
                    source="chat",
                    type="message",
                    payload={"text": text, "session_id": sid},
                    #: 只留来龙去脉，不参与任何判断（对齐 events.py 的约定）
                    origin_context={"sid": sid},
                )
            )
            # 促狭（2026-08-27）：喂这一轮的 valence。
            #
            # 🔴 **每一轮都要喂，不管是什么 valence。**
            # 只喂 playful 的话，她「哈哈哈」之后说十句正事，
            # 窗口里还是三条 playful —— 他会一直贫下去。
            # 正经的那些正是让气氛散掉的东西（见 playfulness.py）。
            try:
                #: ⚠️ appraiser 挂在 **evaluator** 上，不是 engine 上。
                #: 写错属性路径的话 AttributeError 会被下面那个 except 吞掉，
                #: 于是促狭永远是 0 —— 今天已经被同类问题咬过两次了
                ap = _appraiser().appraise(text)
                attention.playfulness.on_turn(
                    _now_utc,
                    valence=ap.valence if ap is not None else "neutral",
                    cue=ap.cue if ap is not None else "",
                )
                attention.store.set_source_state(
                    PLAYFUL_KEY, attention.playfulness.to_dict()
                )
            except Exception:  # noqa: BLE001
                logger.exception("促狭更新失败（不影响对话）")

            logger.info(
                "ConversationEvent 进入 Attention：%.40s｜%s（%s）",
                text, decision.action, decision.reason,
            )
        except Exception:  # noqa: BLE001
            # 对齐这个函数的既有风格：增强路径炸了也绝不影响对话主链
            logger.exception("ConversationEvent 处理失败（不影响对话）")

        # 理解层（2026-09-05）：她这句话**可能意味着什么**。
        #
        # ⚠️ 必须在上面那道测试会话闸门之内 —— 它会写 Registry（R6）。
        # 放在最后是因为它是这个函数里唯一要打网络的一步，
        # 前面那些本地计算不该等它。
        try:
            _appraise_async(sid, text, reply)
        except Exception as exc:  # noqa: BLE001
            logger.warning("意义推断启动失败（不影响对话）: %s", exc)

    def _appraise_async(sid: str, text: str, reply: str) -> None:
        """后台线程里做意义推断（理解层，2026-09-05）。

        ## 🔴 为什么必须是后台线程

        `_turn_ends` 跑在 SSE **`done` 帧之前**（见 `/chat/stream` 那个
        生成器：`_turn_ends` 之后才 yield done）。在这里同步调 LLM
        会把整条流的收尾拖住 1-2 秒 —— 正文早就流完了，进度条却一直转，
        而且 bridge 要靠 done 帧记账。

        抄 `_maybe_compact_async` 的形状（同一个文件，压缩就是这么跑的：
        daemon 线程 + utility 模型 + 失败只记日志）。

        ## 三种模式（`NOX_LLM_APPRAISAL`）

            off     根本不起线程（默认）
            shadow  跑完只记日志，**不写 Registry** —— 先看它判得准不准
            on      写 Registry，走和规则版完全同一条下游

        影子模式不是可有可无的谨慎：理解判错的后果是他念叨一件她根本没说的事，
        而她无从知道他为什么这么想（`appraisal.py` 模块头）。
        先看一周日志再转正，比出事之后回滚便宜得多。
        """
        m = appraisal_llm.mode()
        if m == "off" or not text:
            return

        utility = core.router.light_adapter if core.router else None
        if utility is None:
            return

        def _run() -> None:
            try:
                # 已有锚点喂给它，让它优先复用而不是每轮造一个新的 ——
                # 「毕设」和「毕业设计」分成两条，他就会以为是两件事
                known = tuple(
                    a.subject[len(ANCHOR_PREFIX):]
                    for a in attention.engine.registry.list()
                    if a.subject.startswith(ANCHOR_PREFIX)
                )
                ap = LLMAppraiser(lambda: utility).appraise_turn(text, reply, known)
                if ap is None:
                    return

                if m == "shadow":
                    # 🔴 影子模式：**到此为止，一个字都不写库。**
                    # 日志格式固定成一行，方便一周后 grep 出来整批看
                    logger.info(
                        "[理解层·影子] %s｜%s｜意义=%s｜强度 %.2f 确信 %.2f｜原话=%.40s",
                        ap.subject, ap.valence, ap.meaning or "（无）",
                        ap.intensity, ap.confidence, text,
                    )
                    return

                decision = attention.engine.handle(ExperienceEvent(
                    source=appraisal_llm.SOURCE,
                    type=appraisal_llm.TYPE,
                    payload={
                        "text": text,
                        # 摊平成 dict —— payload 要能进日志、能 to_dict
                        # （events.py 的约定），不能塞对象
                        "appraisal": ap.to_payload(),
                        "session_id": sid,
                    },
                    origin_context={"sid": sid},
                ))
                logger.info(
                    "[理解层] %s → %s（%s）",
                    ap.subject, decision.action, decision.reason,
                )
            except Exception:  # noqa: BLE001
                # 🔴 不许静默（docs/LOGGING.md）。这一层挂了的表现是
                # 「他好像没那么懂我了」，没有任何报错——不留痕就查不出来
                logger.exception("意义推断失败（不影响对话）")

        threading.Thread(target=_run, daemon=True).start()

    def _maybe_compact_async(sid: str) -> None:
        """后台线程触发一次压缩。

        只压「真的会变长的会话」（历史超过预算阈值），短会话几行就 return。
        utility 模型（便宜）负责生成摘要，主模型缓存不受影响。
        """
        try:
            utility = core.router.light_adapter if core.router else None
            if utility is None:
                return
            import threading
            t = threading.Thread(
                target=maybe_compact,
                args=(
                    db, utility, sid,
                    core.cfg.recent_window_tokens,
                    core.cfg.context_budget_tokens,
                ),
                kwargs={},
                daemon=True,
            )
            t.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("压缩启动失败: %s", exc)

    app = FastAPI(title="Nox Core", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- 本地执行端
    #
    # 🔴 **没配密钥就根本不注册这个路由。**
    #
    # 不是"注册了但拒绝连接" —— 那样端点仍然存在，扫描器能发现它，
    # 而且任何人都能对它发起握手（哪怕都被拒）。现有部署没配这个变量，
    # 所以这条路由压根不会出现，**行为零变化**。
    #
    # ⚠️ Nox Core 只监听 127.0.0.1，所以这个端点本身不直接对外 ——
    # 要让她的电脑连上来，得在前面的反向代理上放行 /agent/local
    # 并允许 WebSocket 升级。那是部署的事，不在这里。
    if read_secret() is not None:
        @app.websocket("/agent/local")
        async def agent_local(ws: WebSocket) -> None:
            """她的电脑连上来（PC 主动外连，见架构文档第三节）。

            ⚠️ `accept()` 之后**立刻**交给 LocalLink —— 认证在它里面做。
            这里不做任何判断，免得两处各写一套、迟早漂掉。
            """
            await ws.accept()
            await local_hand.serve(ws)

        logger.info("本地执行端已启用：ws /agent/local")
    else:
        logger.info("没配 CAELUM_LINK_SECRET，本地执行端不启用")

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "model": core.cfg.primary.model,
            "prefix_chars": len(core.system_prompt),
            "tools": list(core.loop.tools),
            "sessions_cached": len(sessions),
            "store": db.stats(),
            "uptime_s": int((datetime.now(timezone.utc) - started).total_seconds()),
            # 她的电脑连着没。没启用时是 None，一眼看得出区别
            "local_hand": (
                {"ready": local_hand.is_ready, "device": local_hand.device_id}
                if read_secret() is not None else None
            ),
            # 没开就是 None，看一眼就知道这套东西在不在跑
            "attention": attention.snapshot() if attention is not None else None,
        }

    @app.get("/api/nox/state")
    def nox_state() -> dict:
        """工作台状态 —— 桌面端/前端轮询这个，看到「他在做什么 / 想什么 / 要不要找她」。

        聚合的是 nox-core 内部的 observable state（架构文档 §3 的「状态可见」原则），
        不是模型的 reasoning。attention 没启用时 `attention.enabled=false`，
        前端据此知道这套自主系统没在跑，而不是假装一切正常。
        """
        snap = attention.snapshot() if attention is not None else None
        if snap is None:
            attn = {"enabled": False}
            wakeups: list[dict] = []
        else:
            attn = {
                "enabled": True,
                "dry_run": snap.get("dry_run"),
                # 正在关心的话题（subject）。
                #
                # ⚠️ **保持字符串数组不要动。** 手机端
                # `nox-app/frontend/src/components/NoxStatus.jsx:38` 直接读它，
                # 改成对象会让她手机上那一栏当场空掉。
                # 要强度的走下面那个 `attentions`
                #
                # 🔴 **只放关于她的**（2026-09-04）。
                #
                # 加 curiosity 那天出的事：话题池抓来的新闻标题（每条 40 字）
                # 也进了这个列表，手机端那行 `Thinking about ${cares.join(" & ")}`
                # 当场炸成一大坨，而且读起来是
                # 「Thinking about your sleep & 前端圈沸腾！Claude造出15KB引擎」——
                # **前半句是惦记她，后半句是他刷到的新闻，两种语气焊在一句里。**
                #
                # 分流按 `kind`，不靠前端猜字符串。他自己的那些走下面 `curious`
                "cares": [a["subject"] for a in snap.get("attentions", [])
                          if a.get("kind") not in _SELF_KINDS],
                # 他自己好奇的东西（2026-09-04）。**和 cares 是两种语气**，
                # 前端该分开摆：cares 进那句一眼可见的话，这个进展开面板
                "curious": [a["subject"] for a in snap.get("attentions", [])
                            if a.get("kind") in _SELF_KINDS],
                # 🔴 带强度的完整形态（2026-08-29）。
                #
                # 在这之前这里只吐 subject，`strength` 和 `since` 在出门那一刻
                # 就被扔了 —— 于是前端手上只有一串名字，画不出任何高低，
                # 只能把等级写死成「中」（noxState.js 里那五个常量）。
                # 糖糖要做心跳线，第一件缺的东西就是这个。
                #
                # ⚠️ `strength` 是**读时按经过时间指数衰减**算出来的，
                # 半衰期 6 小时 / 2 天 / 7 天（registry.py `_HALF_LIFE`）——
                # 也就是说它**变化的尺度是「天」，不是「秒」**。
                # 直接把它当心电图的纵轴画会得到一条几乎水平的线。
                # 它适合当「基线」，尖峰得靠事件，那个还没有出口
                "attentions": snap.get("attentions", []),
                # 想说还没说的（待办）
                "pending": snap.get("pending_intents", []),
                # 今天惦记过她几次、其中几次说出了口（2026-08-18）。
                # considered = spoke + skipped + blocked，见 care/ledger.py
                "ledger": ((snap.get("care") or {}).get("ledger")),
                # 今日开口额度（统一开口闸）
                "gate": snap.get("gate"),
                # 冷却状态（上次开口 / 各话题冷却）
                "scheduler": snap.get("scheduler"),
            }
            wakeups = snap.get("wakeups", [])
        return {
            "ok": True,
            "now": now_cst().isoformat(),
            "attention": attn,
            "wakeups": wakeups,
        }

    @app.get("/api/nox/resonance")
    def nox_resonance() -> dict:
        """他此刻的内心驱动力（Drive）—— **心跳线要画的就是这个**。

        架构见 `CAELUM-RESONANCE-ARCHITECTURE.md`。Resonance 从 2026-08-24
        就在跑了，但**一直没有任何 HTTP 出口** —— 整个路由表里搜不到它。
        糖糖 2026-08-29 要做 Attention 心跳线时才发现这个缺口。

        ## 🔴 画图要用 `load`，不要用 `intensity`

        ```text
        一件 0.98            intensity 0.98   load 0.98
        0.98 + 0.71 + 0.40   intensity 0.997  load 2.09
        ```

        `intensity` 表达「至少有一件事没解决」，真实数据里几乎永远贴着 1，
        **一件事和三件事在图上看不出差别**。要表达"压着多重"用 `load`，
        它不饱和，留得住区分度（`resonance.py::Drive` 里写着这条）。

        ## ⚠️ 不存在的 Drive 不会出现在这里

        「他现在不低落」表现成**没有 dejection 这一项**，
        而不是 `dejection: 0.00`。画线的时候缺项要按 0 处理，
        但文案上别说成「低落 0.00」—— 那读起来像他有一点点低落。

        ## ⚠️ 这是快照，不是流

        每次调用按 `now` 重算一遍（强度是读时衰减的）。
        但慢变量的半衰期是小时/天级，**轮询它只能得到基线，得不到脉搏**。
        尖峰要等事件流，那个还没做。
        """
        if attention is None or attention.resonance is None:
            #: 和 `/api/nox/state` 一个态度：没启用就如实说没启用，
            #: 不返回空数组假装「他此刻很平静」
            return {"ok": True, "enabled": False, "now": now_cst().isoformat(),
                    "drives": {}}

        now = datetime.now(timezone.utc)
        #: 🔴 走 `attention.drives()`，**别直接调 `resonance.snapshot()`** ——
        #: 躁动的两个信号（他有多想说 / 她在忙什么）是那一层现算并传进去的，
        #: 绕过去的话躁动永远是 0，而且不报错
        drives = attention.drives(now)
        return {
            "ok": True,
            "enabled": True,
            "now": now_cst().isoformat(),
            "drives": {
                name: {
                    "name": d.name,
                    "intensity": round(d.intensity, 3),
                    #: 🔴 画图用这个
                    "load": round(d.load, 3),
                    "because": d.because,
                    "evidence": d.evidence,
                    "source_count": d.source_count,
                }
                for name, d in drives.items()
            },
        }

    def _pulse_events(since: int) -> list[dict]:
        """账本里 `seq > since` 的那些。**没有账本就是空，不假装。**"""
        if attention is None or attention.ledger is None:
            return []
        return [e for e in attention.ledger.events
                if int(e.get("seq") or 0) > since]

    def _pulse_seq() -> int:
        if attention is None or attention.ledger is None:
            return 0
        return int(getattr(attention.ledger, "seq", 0))

    @app.get("/api/nox/pulse")
    def nox_pulse(since: int = 0, limit: int = 100) -> dict:
        """他这一天里**每一次动念**，逐条带时刻 —— 心跳线上的尖峰。

        ## 这些数据一直都在，只是从没出过门

        `CareLedger.events` 从 2026-08-18 起就在逐条记：
        什么时候、哪个源、决定是开口还是憋住、为什么憋住。
        但 `/api/nox/state` 只吐了它的**汇总**（今天说了几次），
        明细一条都没暴露过。糖糖 2026-08-29 要做心跳线才发现。

        ## `since` 怎么用

        ```text
        第一次    GET /api/nox/pulse            → 拿到 seq=42 和最近的事件
        之后      GET /api/nox/pulse?since=42   → 只拿新的
        ```

        `seq` **跨换天、跨重启都单调**（见 `CareLedger.__init__`）——
        归零的话客户端过零点会以为时间倒流。

        ## ⚠️ 只有今天的

        账本换天会清空（那是它的设计：`_roll`）。所以这是**当天**的脉搏，
        不是历史曲线。要看往前的日子走 `/api/nox/day`。

        ## decision 的四种值

        ```text
        speak    他说出口了        ← 最亮的那种尖峰
        skip     想了想，没说
        block    被闸/冷却拦住了   ← `reason` 里写着为什么
        failed   要说但发失败了
        ```
        """
        evs = _pulse_events(since)
        if limit > 0:
            evs = evs[-limit:]
        return {
            "ok": True,
            "now": now_cst().isoformat(),
            #: 客户端下次拿这个当 `since`。**即使这次一条都没有也要给** ——
            #: 不然它永远从 0 开始要
            "seq": _pulse_seq(),
            "enabled": attention is not None,
            "events": evs,
        }

    @app.get("/api/nox/pulse/stream")
    def nox_pulse_stream(since: int = 0) -> StreamingResponse:
        """同上，但是推的。**心跳线要的是这条。**

        ## 🔴 为什么是服务端轮询内存，不是事件总线

        账本是在**线程池里**被写的（Care 快循环有阻塞 HTTP），
        而 SSE 跑在事件循环上。要做真的发布订阅就得架一座
        线程 → asyncio 的桥，那是另一类复杂度和另一类 bug。

        这里退一步：**每秒扫一遍那个内存列表，有新的就推。**
        代价是最多晚 1 秒，而心跳线本来就不需要毫秒级 ——
        她要的是"看得见他在动"，不是示波器。

        ⚠️ 扫的是**进程内的 list**，没有 IO，也不碰数据库。

        ## 心跳注释

        没有事件时每 15 秒发一行 `: ping`。不发的话中间的代理
        会把这条闲着的连接掐掉，而前端看到的是"心跳突然停了"。
        """
        return StreamingResponse(
            pulse_stream(_pulse_events, _pulse_seq,
                         enabled=attention is not None, since=since),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/nox/day")
    def nox_day(date: str | None = None) -> dict:
        """「Nox 的一天」—— 当日活动的**只读投影**。

        设计见 `Nox 的一天 架构设计文档.md` v1.1。三条硬约束：
        不新建事实存储、每条能溯源、只输出用户可感知的事件。

        数据源全部来自已有业务（CareLedger / Conversation / WakeBook）。
        还没接的源在响应的 `sources` 里如实标出来 ——
        「今天没听歌」和「共听根本没接」不能在界面上长得一样。
        """
        day = date or now_cst().strftime("%Y-%m-%d")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="date 要 YYYY-MM-DD") from None

        return build_day(
            day,
            ledger=attention.ledger if attention is not None else None,
            store=db,
            wakeups=attention.wakeups if attention is not None else None,
            # 待办和共听的数据在 bridge，世界事实在 Core 自己手里
            bridge=core.bridge,
            world=attention.world if attention is not None else None,
        )

    @app.post("/api/nox/record/period")
    def nox_record_period(req: PeriodRequest) -> dict:
        """她在 App 里直接填的生理期 → World Model。

        和 `tools/record.py` 的 `record_period` 写的是**同一个地方**
        （同样的 type、同样的 dedup_key），只是入口不同：
        一个是他在对话里听见了记下来，一个是她自己填。两条路都要有 ——
        她不是每次都想说出口，他也不是每次都在。

        ⚠️ **体重不走这里**。记体重只有 `log_weight` 一条路
        （写 bridge 的 `body_weight`），见 `tools/record.py` 开头。
        """
        world = _world()
        if world is None:
            raise HTTPException(status_code=503, detail="World Model 没启用")
        if req.event not in ("start", "end"):
            raise HTTPException(status_code=422, detail="event 要 start 或 end")

        msg = record_tools.make_handlers(world)["record_period"](
            {"event": req.event, "flow": req.flow, "date": req.date})

        # 工具的回执是写给**他**看的（带着「别追问细节」那类指示），
        # 不适合直接摆到界面上。这里只回结果，人话由前端自己写
        return {"ok": "记下了" in msg, "detail": msg}

    @app.get("/api/nox/facts")
    def nox_facts(type: str, days: int = 30) -> dict:
        """某类事实的历史。App 的周期记录读它。"""
        world = _world()
        if world is None:
            raise HTTPException(status_code=503, detail="World Model 没启用")
        rows = world.query(type, days=max(1, min(days, 365)), limit=200)
        return {
            "ok": True,
            "type": type,
            "items": [
                {
                    "content": e.content,
                    "source": e.source,
                    "observed_at": e.observed_at.isoformat(),
                    "raw": e.raw,
                }
                for e in rows
            ],
        }

    def _backend_of(llm: Any) -> str:
        """从 LLMConfig 反推是哪家。

        ⚠️ backend 名只在构建时存在 —— LLMConfig 里只剩 provider 和
        base_url，而 provider 是协议（openai_compat 两家共用），
        按地址认人才分得清 DeepSeek 和 OpenRouter。
        """
        base = (getattr(llm, "base_url", "") or "").rstrip("/")
        for name, b in BACKENDS.items():
            if base and b.base_url.rstrip("/") == base:
                return name
        return ""

    @app.get("/api/nox/models")
    def nox_models() -> dict:
        """可切换的模型清单 + 当前系统默认。Models 设置页的数据源。

        切换本身不走这里 —— Chat 请求带上 `model` 短名就行（config.models
        + `adapter_for` 那条老路）。这页只负责把「有哪些、现在默认是谁、
        哪家配了 key」摆到台面上。

        ⚠️ key 一个字节都不出这里 —— providers 只回答「配没配」。
        """
        cfg = getattr(core, "cfg", None)
        primary = getattr(cfg, "primary", None)
        if cfg is None or primary is None:
            raise HTTPException(status_code=503, detail="Core 没起来")

        choices = [
            {"key": k, "model": c.model, "backend": c.backend,
             "label": c.label or c.model}
            for k, c in (getattr(cfg, "models", {}) or {}).items()
        ]
        providers = {}
        for name, label in (("deepseek", "DeepSeek"), ("openrouter", "OpenRouter")):
            b = BACKENDS.get(name)
            providers[name] = {"label": label, "configured": bool(b and b.api_key)}

        utility = getattr(cfg, "utility", None)
        return {
            "ok": True,
            "current": {
                "backend": _backend_of(primary),
                "model": primary.model,
            },
            "utility": ({"backend": _backend_of(utility),
                         "model": utility.model} if utility is not None else None),
            "choices": choices,
            "providers": providers,
        }

    @app.get("/api/nox/tools")
    def nox_tools() -> dict:
        """他手上全部注册的工具。Studio → Tools 工具墙的数据源。

        只读 loop 里现成的 ToolSpec（名字 + 给模型看的那句描述）——
        分组是前端按名字前缀认的，后端不维护第二份分组表。
        """
        loop = getattr(core, "loop", None)
        tools = getattr(loop, "tools", None) if loop is not None else None
        if tools is None:
            raise HTTPException(status_code=503, detail="Core 的工具组没起来")
        items = [
            {"name": t.spec.name, "description": t.spec.description}
            for t in tools.values()
            if t is not None and getattr(t, "spec", None) is not None
        ]
        return {"ok": True, "count": len(items), "items": items}

    @app.get("/api/nox/integrations")
    def nox_integrations() -> dict:
        """他和外面世界的插口：配了什么、通不通。Studio → MCP 面板的数据源。

        探活语义：**只要对方应答就算活着**（401/404 也是"在"），
        连不上/超时才算断。结果缓存 60 秒 —— 探一轮要几秒，
        不能每次进页面都全量打一遍。
        """
        import time

        cfg = getattr(core, "cfg", None)
        if cfg is None:
            raise HTTPException(status_code=503, detail="Core 没起来")

        now = time.time()
        if now - _integration_cache["at"] < 60 and _integration_cache["items"]:
            return {"ok": True, "items": _integration_cache["items"], "cached": True}

        def env_of(field: str) -> str:
            return field.replace("_url", "").upper()

        targets = [
            ("bridge", "相册 / 日记 / 待办 / 饮食的中转", getattr(cfg, "bridge_url", "")),
            ("ombre-brain", "记忆 · Ombre Brain", getattr(cfg, "ob_url", "")),
            ("co-reading", "共读的页边笔记", getattr(cfg, "reading_url", "")),
            ("eryu", "共听的播放层", getattr(cfg, "eryu_url", "")),
            ("netease-mcp", "网易云账号：歌单 / 红心 / 推荐", getattr(cfg, "netease_url", "")),
            ("ha-mcp", "家里的设备：灯 / 空调 / 开关", getattr(cfg, "ha_url", "")),
            ("ha-api", "出门在家 · 位置感知", getattr(cfg, "ha_api_url", "")),
            ("tracker", "她在电脑上用了什么 App", getattr(cfg, "tracker_url", "")),
            ("health", "健康数据同步", getattr(cfg, "health_url", "")),
            ("notion", "信箱 · Notion",
             "https://api.notion.com" if getattr(cfg, "notion_token", "") else ""),
            ("qweather", "天气", (getattr(cfg, "qweather_host", "") or "")),
        ]
        items = []
        alive = [t for t in targets if t[2]]

        def probe(url: str) -> bool:
            # ⚠️ 404/405 也是「活着」—— MCP 端点对 GET 回 405 是正经行为。
            # urllib 把 4xx/5xx 抛成 HTTPError，必须单独接住算通；
            # 真正的断 = 连不上 / 超时
            from urllib.error import HTTPError
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "nox-integrations"})
                with urllib.request.urlopen(req, timeout=4):
                    return True
            except HTTPError:
                return True
            except Exception:
                return False

        if alive:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as pool:
                ups = list(pool.map(probe, [t[2] for t in alive]))
        else:
            ups = []

        by_url = {t[2]: up for t, up in zip(alive, ups)}
        for name, what, url in targets:
            from urllib.parse import urlsplit
            host = urlsplit(url).netloc if url else ""
            items.append({
                "name": name,
                "what": what,
                "host": host,
                "status": ("up" if by_url.get(url) else "down") if url else "not_configured",
            })

        _integration_cache["at"] = now
        _integration_cache["items"] = items
        return {"ok": True, "items": items, "cached": False}

    #: 天空整段缓存 10 分钟。和风的 TTL 是 30 分钟（WeatherProvider），
    #: 这里取它的三分之一 —— 天气不会分钟级变化，但界面轮询很勤，
    #: 不缓存等于每次进页面打两次外部 API。
    #: ⚠️ **只缓存天空**：设备状态慢一拍就是谎，那个每次现取。
    _sky_cache: dict = {"at": 0.0, "data": None}

    @app.get("/api/nox/world")
    def nox_world() -> dict:
        """他此刻感知到的世界 —— World 页（户型图）的数据源。

        四段：天 / 家里的设备 / 她在不在家 / 她在忙什么。
        **每段各报各的 ok**，一段挂了不拖累其他三段 —— 细节和理由见
        `api/world.py` 的模块注释。
        """
        import time

        from api import world as W
        from tools.http import RestClient
        from tools.mcp_client import McpClient

        cfg = getattr(core, "cfg", None)
        if cfg is None:
            raise HTTPException(status_code=503, detail="Core 没起来")

        # ---- 天 ----
        if not (cfg.qweather_host and cfg.qweather_key):
            sky = {"ok": False, "error": "没配和风的 key"}
        elif time.time() - _sky_cache["at"] < 600 and _sky_cache["data"]:
            sky = {**_sky_cache["data"], "cached": True}
        else:
            wc = RestClient(base=f"https://{cfg.qweather_host.strip().rstrip('/')}",
                            timeout=8.0)

            def get_json(path: str) -> dict:
                r = wc.get(path, {"location": cfg.weather_location, "key": cfg.qweather_key})
                if not r.ok:
                    raise RuntimeError(r.error or "和风没回")
                return r.data or {}

            sky = W.collect_sky(get_json)
            if sky.get("ok"):
                _sky_cache["at"] = time.time()
                _sky_cache["data"] = sky

        # ---- 家 + 她在不在家 ----
        # 清单问 ha-mcp（唯一真源），状态打 HA 的 REST（结构化）。
        # 两把钥匙缺哪把就哪段不可用，不要假装另一段也没了
        if cfg.ha_api_url and cfg.ha_api_token:
            state_of = W.ha_state_getter(cfg.ha_api_url, cfg.ha_api_token)
            presence = W.collect_presence(state_of)
        else:
            state_of, presence = None, {"ok": False, "error": "没配 HA 的 API token"}

        if cfg.ha_url and state_of is not None:
            mcp = McpClient(cfg.ha_url, name="ha")

            def list_devices() -> str:
                r = mcp.call("hass_list_devices")
                if not r.ok:
                    raise RuntimeError(r.error or "ha-mcp 没回")
                return r.text

            home = W.collect_home(list_devices, state_of)
        else:
            home = {"ok": False, "error": "没配 ha-mcp 或 HA API", "devices": []}

        # ---- 她在忙什么 ----
        link = getattr(attention, "link", None) if attention is not None else None
        activity = W.collect_activity(
            getattr(link, "_current", None) if link is not None else None,
            bool(getattr(link, "is_ready", False)) if link is not None else False,
        )

        return {"ok": True, "now": W.now_iso(),
                "sky": sky, "home": home, "presence": presence, "activity": activity}

    @app.get("/api/nox/topics")
    def nox_topics() -> dict:
        """池子里的活话题（未过期、没人理过的）。文档 §4.2 的入口 B。"""
        pool = _topics()
        if pool is None:
            raise HTTPException(status_code=503, detail="话题池没启用")
        return {"ok": True, "items": pool.topics_for_ui()}

    @app.post("/api/nox/topics/{topic_id}/status")
    def nox_topic_status(topic_id: str, body: dict) -> dict:
        """人工决策：followed / dismissed。

        ⚠️ follow 的边界（文档 §4.3，写死）：这只是个**记号**——
        「我们感兴趣」。它不触发任何自动研究、不派任务；
        要去查、要聊，是 Nox 和糖糖自己的下一步。
        """
        pool = _topics()
        if pool is None:
            raise HTTPException(status_code=503, detail="话题池没启用")
        status = str((body or {}).get("status") or "")
        if status not in ("followed", "dismissed"):
            raise HTTPException(status_code=422,
                                detail="status 只能是 followed / dismissed")
        if not pool.store.mark(topic_id, status):
            raise HTTPException(status_code=404,
                                detail="话题不存在，或已经被人理过了")
        return {"ok": True, "id": topic_id, "status": status}

    @app.post("/attention/tick")
    def attention_tick() -> dict:
        """手动走一次 Attention 链路。

        dry-run 期间用来立刻看结果，不用干等 15 分钟的心跳。
        `decision` 里带着完整的判断过程 —— 包括「为什么没开口」，
        那才是这一步要观察的东西。
        """
        if attention is None:
            raise HTTPException(status_code=503, detail="Attention 没启用（NOX_ATTENTION）")
        decision = attention.tick()
        return {
            "will_speak": decision.will_speak,
            "decision": decision.render(),
            "snapshot": attention.snapshot(),
        }

    @app.get("/sessions")
    def list_sessions(limit: int = 20) -> dict:
        """最近的会话列表 —— 只列真实对话，测试 session 不显示。"""
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "messages": s.message_count,
                    "updated_at": s.updated_at,
                }
                for s in db.recent(limit=min(limit, 100), clean_only=True)
            ]
        }

    @app.get("/session/{sid}")
    def get_session(sid: str, limit: int = 40) -> dict:
        msgs = db.load(sid, limit=min(limit, 200))
        if not msgs:
            raise HTTPException(status_code=404, detail="没有这个会话")
        return {
            "session_id": sid,
            "messages": [{"role": m.role, "text": m.text} for m in msgs],
            "total": db.count(sid),
        }

    @app.post("/daily-summary")
    def daily_summary(req: DailySummaryRequest) -> dict:
        """生成今日简报。给 bridge 的 /api/daily-push 调（每天早上一次）。

        ⚠️ **走 `core.chat()`，不是 `loop.run()`。** 这句话必须进他自己的
        会话历史，否则她回一句「嗯有点」时他不知道自己刚问过什么
        （和 `bridge/server.js` 的自动关心同一个道理，见那里第 732 行）。

        推送和落 conversations 由 bridge 负责 —— 它管着订阅表和前端读的那张表。
        `push=true` 只是给手工调试留的后门，正常路径下 bridge 传 false。
        """
        try:
            prep = prepare_morning(core.context, include_memory=req.include_memory)
        except Exception as exc:  # noqa: BLE001
            logger.exception("早报取数异常")
            raise HTTPException(
                status_code=500, detail=f"内部错误: {type(exc).__name__}") from exc

        out = prep.to_dict()
        out["pushed"] = False
        out["session_id"] = req.session_id

        if prep.skipped:
            # 数据不全就不推 —— 宁可今天不响，也不推一句编的
            logger.info("早报未推送: %s", prep.reason)
            return out

        sid = req.session_id or "api-daily"
        history = sessions.get(sid)
        try:
            r = core.chat(prep.prompt, history)
        except Exception as exc:  # noqa: BLE001
            logger.exception("早报生成异常")
            raise HTTPException(
                status_code=500, detail=f"内部错误: {type(exc).__name__}") from exc

        # 存进他的会话 —— 这一步就是「留在上下文里」的落点
        sessions.put(sid, r.messages)

        if not r.result.ok:
            logger.warning("早报没正常生成: %s | %s", r.result.outcome, r.result.detail)
        text = finalize_push_text(r.result.text or "")
        if not text:
            out["skipped"] = True
            out["reason"] = f"模型没给出文本（{r.result.outcome}）"
            return out

        out["text"] = text
        logger.info("早报生成: %s", text)

        # 收编进 Care 的账本（2026-08-18）。早报不经过 tick，
        # 不记的话它对 Care 完全隐形 —— 10:00 刚说完，10:05 别的源
        # 又冒一次，两边谁也不知道对方说过话。
        # 只记账不做决定：早报是一天一次的固定仪式，不吃闸也不吃额度。
        if attention is not None:
            try:
                out["care_thread"] = attention.note_external_speech("morning", "早报")
            except Exception:  # noqa: BLE001
                # 记账失败不该让早报本身失败 —— 话已经生成好了
                logger.exception("早报记进 Care 失败")

        if req.push:
            sent = _push_to_bridge(core, text)
            out["pushed"] = sent.get("ok", False)
            out["push_detail"] = sent
        return out

    # ---------------------------------------------------------------- 待办
    # 给 bridge 的 App「今天」页用。她在手机上记一条，得同时进 todo.md，
    # 否则第二天晨报根本不知道有这件事 —— 这就是「两个孤岛」的那道桥。
    #
    # 走 HTTP 而不是让 bridge 直连 GitHub：token 只放 Core 一份。
    # 也不走 core.chat()，这里没有话要说，纯数据操作。

    def _todo_writer():
        if not (core.cfg.todo_repo and core.cfg.github_token):
            raise HTTPException(status_code=503, detail="未配置 todo 仓库或 GitHub token")
        from tools.todo import TodoWriter

        return TodoWriter(core.cfg.github_token, core.cfg.todo_repo, core.cfg.todo_path)

    @app.get("/todo")
    def todo_list() -> dict:
        """todo.md 里所有未完成项，按区分组。App「今天」页拉这个。"""
        from tools.todo import read_open_items

        try:
            return {"ok": True, "sections": read_open_items(_todo_writer())}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("读待办失败")
            return {"ok": False, "error": str(exc), "sections": {}}

    @app.post("/todo/add")
    def todo_add(req: TodoAddRequest) -> dict:
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        try:
            return {"ok": True, "message": _todo_writer().add(text, req.section)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # 如实返回失败 —— App 那边要能提示她「没同步上」，不能假装记好了
            logger.exception("写待办失败")
            return {"ok": False, "error": str(exc)}

    @app.post("/todo/complete")
    def todo_complete(req: TodoCompleteRequest) -> dict:
        kw = req.keyword.strip()
        if not kw:
            raise HTTPException(status_code=400, detail="keyword 不能为空")
        try:
            # ok 跟着「真的改了没」走 —— 没找到那条时返回 True 等于骗 bridge，
            # App 里勾掉了、todo.md 没动，谁都不知道
            out = _todo_writer().complete(kw)
            return {"ok": out.changed, "message": out.message,
                    "error": "" if out.changed else out.message}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("划待办失败")
            return {"ok": False, "error": str(exc)}

    @app.post("/api/describe-image")
    def describe_image(req: DescribeImageRequest) -> dict:
        """识别一张相册图，返回描述。bridge 在图片进相册后异步调它。

        让每张相册图都有描述 —— Nox 以后能按内容选图，而不是盲发。
        识别失败返回空描述，不抛错：图片本身没问题，描述空着等手动填。
        """
        if not core.cfg.vision.api_key:
            return {"description": ""}
        bridge_base = (core.cfg.bridge_url or "http://127.0.0.1:3003").rstrip("/")
        img_url = bridge_base + req.url
        try:
            with urllib.request.urlopen(img_url, timeout=20) as resp:
                data = resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("识图读不到 %s: %s", req.url, exc)
            return {"description": ""}

        b64 = base64.b64encode(data).decode("ascii")
        # 相册图都是 jpg（bridge 上传时转的）。describe 只认 base64 data URI。
        desc = vision.describe([f"data:image/jpeg;base64,{b64}"],
                               core.cfg.vision, timeout=core.cfg.vision_timeout)
        return {"description": desc or ""}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        if not req.has_content():
            raise HTTPException(status_code=422, detail="text 和 images 不能都是空的")

        sid = req.session_id or uuid.uuid4().hex
        history = sessions.get(sid)
        _turn_starts(sid)
        sent = _for_voice(history, req.voice)

        try:
            r = core.chat(req.text, sent, images=req.images or None, voice=req.voice,
                          scene=req.scene, model=req.model)
        except Exception as exc:  # noqa: BLE001
            logger.exception("对话处理异常")
            raise HTTPException(status_code=500, detail=f"内部错误: {type(exc).__name__}") from exc

        # ⚠️ 完整历史 + 这轮新增。只传 r.messages 的话，通话轮会把
        # 内存缓存削成截断后那几条，下一次文字聊天跟着丢上下文（见 _for_voice）
        sessions.put(sid, list(history) + r.messages[len(sent):])
        _turn_ends(sid, req.text or "", r.text or "")

        result = r.result
        # 失败时给人话；但如果模型已经说了什么（比如截断的半截），
        # 就把它一起带上，别把他说过的话吞掉
        if result.ok:
            text = result.text or ""
        else:
            friendly = _OUTCOME_TEXT.get(result.outcome, _OUTCOME_TEXT["error"])
            text = f"{result.text}\n\n{friendly}" if result.text else friendly
            logger.warning("对话未正常完成: %s | %s", result.outcome, result.detail)

        # 非流式没有 SegmentSplitter，标记会原样漏出去
        text = _clean_segments(text, req.voice)

        tools_used = _tools_this_turn(result.messages, history)
        u = result.usage

        return ChatResponse(
            text=text,
            session_id=sid,
            ok=result.ok,
            path="light" if r.decision.light else "full",
            iterations=result.iterations,
            outcome=result.outcome,
            tools_used=tools_used,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cached_tokens=u.cache_read_tokens,
            cache_write_tokens=u.cache_write_tokens,
            model=core.model_name(req.model),
            attachments=result.attachments,
        )

    @app.post("/chat/stream")
    def chat_stream(req: ChatRequest):
        """流式对话（SSE）。

        非流式那条要等他把话说完才返回 —— 完整路径实测 19 秒，
        这 19 秒里前端屏幕上什么都没有。这条边生成边推。

        事件格式：
            data: {"type":"text","text":"增量"}
            data: {"type":"done","session_id":...,"ok":...,"outcome":...}
        """
        if not req.has_content():
            raise HTTPException(status_code=422, detail="text 和 images 不能都是空的")

        sid = req.session_id or uuid.uuid4().hex
        history = sessions.get(sid)
        # 通话只带最近几轮，理由见 _for_voice()。**语音走的就是这条流式路径**
        sent = _for_voice(history, req.voice)

        def events():
            final = None
            # ⚠️ 在生成器**体**里调，不在外面 —— 生成器体是被迭代的那个线程
            # 跑的，工具也在那个线程里跑。放外面的话两者可能不是同一轮
            _turn_starts(sid)
            try:
                for ev in core.chat_stream(req.text, sent, images=req.images or None,
                                           voice=req.voice, scene=req.scene,
                                           model=req.model):
                    if ev.type == "text":
                        yield _sse({"type": "text", "text": ev.text})
                    elif ev.type == "split":
                        # 分段点。前端收到这个就开一个新气泡 ——
                        # 切点是模型自己标的（|||），不是按标点机械切
                        yield _sse({"type": "split"})
                    else:
                        final = getattr(ev, "result", None)
            except Exception as exc:  # noqa: BLE001
                logger.exception("流式对话异常")
                yield _sse({"type": "error", "message": f"内部错误: {type(exc).__name__}"})
                return

            if final is None:
                yield _sse({"type": "error", "message": "流意外结束"})
                return

            # 完整历史 + 新增，别让通话把缓存削短（见 _for_voice 的警告）
            sessions.put(sid, list(history) + final.messages[len(sent):])
            _turn_ends(sid, req.text or "", final.text or "")
            # 附带产物单独发一帧，让前端能在收尾之前就把图显示出来。
            # 外层 type 固定 attachment，具体是什么放 kind ——
            # 之前写成 {"type": "attachment", **att}，att 自带的
            # type="image" 把外层键盖掉了，bridge 那边的分支永远匹配不上
            for att in final.attachments:
                yield _sse({
                    "type": "attachment",
                    "kind": att.get("type", "image"),
                    **{k: v for k, v in att.items() if k != "type"},
                })

            payload = {
                "type": "done",
                "session_id": sid,
                "ok": final.ok,
                "outcome": final.outcome,
                "iterations": final.iterations,
                "input_tokens": final.usage.input_tokens,
                "output_tokens": final.usage.output_tokens,
                "cached_tokens": final.usage.cache_read_tokens,
                "cache_write_tokens": final.usage.cache_write_tokens,
                "model": core.model_name(req.model),
                # 工具调用展示用。bridge 收到 done 后补发 tools 事件给前端。
                # ⚠️ 2026-08-04 之前这里没有 —— bridge 侧的工具展示逻辑早就
                # 写好等着了，但流式 done 永远拿不到 tools_used，前端就一直空白。
                "tools_used": _tools_this_turn(final.messages, history),
            }
            if not final.ok:
                payload["message"] = _OUTCOME_TEXT.get(final.outcome, _OUTCOME_TEXT["error"])
            yield _sse(payload)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            # 关掉 nginx / Caddy 的缓冲，否则流会被攒成一坨再发
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/session/{sid}/archive")
    def archive_session(sid: str, force: bool = False) -> dict:
        """把这段对话沉淀进 Ombre Brain。

        前端该在什么时候调：用户明确结束对话、或者一段时间没动静之后。
        **不要每轮都调** —— 归档的是整段，不是单句。
        """
        history = sessions.get(sid)
        if not history:
            raise HTTPException(status_code=404, detail="没有这个会话")

        res = core.archive(history, force=force)
        return {
            "archived": res.archived,
            "summary": res.summary,
            "reason": res.reason,
        }

    @app.delete("/session/{sid}")
    def drop_session(sid: str) -> dict:
        return {"dropped": sessions.drop(sid)}

    return app


def main() -> int:
    import uvicorn

    from config import config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(create_app(), host=config.host, port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
