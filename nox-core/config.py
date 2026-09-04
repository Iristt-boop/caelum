"""全局配置。所有密钥只从环境变量读，不写进代码。

本地开发把值放进 nox-core/.env（已在 .gitignore 里），
线上放 systemd 的 Environment= / EnvironmentFile=。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Provider = Literal["anthropic", "openai_compat"]


def _load_dotenv() -> None:
    """读同目录的 .env。手写而不是装 python-dotenv —— 少一个依赖。

    已存在的环境变量优先，不覆盖 —— 这样线上用 systemd 的 Environment=
    时，即使目录里躺着一个 .env 也不会把线上配置盖掉。
    """
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_llm(
    *,
    backend: str,
    model: str,
    provider_override: str = "",
    base_override: str = "",
    key_override: str = "",
    max_tokens: int = 16000,
) -> "LLMConfig":
    """按后端名组一个 LLMConfig。显式给的值优先于后端默认值。"""
    b = BACKENDS.get(backend) or BACKENDS["openrouter"]
    return LLMConfig(
        provider=(provider_override or b.provider),  # type: ignore[arg-type]
        model=model,
        api_key=(key_override or b.api_key),
        base_url=(base_override or b.base_url),
        max_tokens=max_tokens,
    )


@dataclass(frozen=True)
class Backend:
    """一个模型服务商的接入方式。

    型号名和后端必须绑在一起 —— 光记型号名，切换时会拿着 A 家的地址去请求
    B 家的模型。
    """

    provider: Provider
    base_url: str
    key_envs: tuple[str, ...]      # 按顺序找第一个非空的

    @property
    def api_key(self) -> str:
        for name in self.key_envs:
            v = _env(name)
            if v:
                return v
        return ""


# 各家接入方式。DeepSeek 和 OpenRouter 都是 OpenAI 兼容协议，差别只在地址和 key。
BACKENDS: dict[str, Backend] = {
    "deepseek": Backend(
        "openai_compat", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",)
    ),
    "openrouter": Backend(
        "openai_compat", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",)
    ),
    # Anthropic 原生：有 1 小时缓存和 adaptive thinking，但拒绝 temperature
    "anthropic": Backend("anthropic", "", ("ANTHROPIC_API_KEY",)),
    # 阿里百炼。这里只用它的视觉模型给主模型「当眼睛」——
    # key 和 Stack-chan 的 ASR、xiaozhi-server 的 VLLM 是同一把，不用另外申请
    "dashscope": Backend(
        "openai_compat",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("DASHSCOPE_API_KEY",),
    ),
}


@dataclass(frozen=True)
class ModelChoice:
    """前端下拉里的一个选项。"""

    model: str        # 发给 API 的型号名
    backend: str      # BACKENDS 的 key
    label: str = ""   # 前端显示用，留空就用 model


@dataclass(frozen=True)
class LLMConfig:
    """一个模型后端的配置。

    provider 决定用哪个 adapter：
      anthropic     —— 原生 Anthropic API（thinking / effort，拒绝 temperature）
      openai_compat —— OpenAI 兼容接口（DeepSeek、OpenRouter，用 temperature）
    """

    provider: Provider
    model: str
    api_key: str
    base_url: str = ""
    max_tokens: int = 16000

    # 网络抖动时重试几次。两家 SDK 都内建了带指数退避的重试，
    # 而且会读 retry-after 头，比自己写一层可靠。
    # 默认值只有 2，对糖糖这边不太稳的网络偏少。
    max_retries: int = 4
    # 单次请求超时（秒）。不设的话默认很长，实测遇到过一句"晚安"卡 33 秒 ——
    # 与其干等，不如早点失败让上层退回或重试。
    timeout: float = 90.0

    @property
    def usable(self) -> bool:
        return bool(self.api_key and self.model)


@dataclass(frozen=True)
class Config:
    # ---- 模型后端 ----
    # 主线：跟糖糖说话的那条，缓存要一直热着，不要中途换。
    #
    # 默认走 OpenRouter（一个 key 通吃，随时换模型）。想要 Anthropic 原生的
    # 1 小时缓存和 adaptive thinking，把这三个环境变量改掉即可，代码不用动：
    #   NOX_PRIMARY_PROVIDER=anthropic
    #   NOX_PRIMARY_MODEL=claude-sonnet-5
    #   ANTHROPIC_API_KEY=sk-ant-...
    # 最省事的切法：只设 NOX_PRIMARY_BACKEND=deepseek + NOX_PRIMARY_MODEL=deepseek-v4-flash，
    # 地址和 key 从 BACKENDS 自动带出来。想手动指定就照旧填 BASE_URL / PROVIDER。
    primary: LLMConfig = field(
        default_factory=lambda: _build_llm(
            backend=_env("NOX_PRIMARY_BACKEND", "openrouter"),
            model=_env("NOX_PRIMARY_MODEL", "anthropic/claude-sonnet-4.5"),
            provider_override=_env("NOX_PRIMARY_PROVIDER"),
            base_override=_env("NOX_PRIMARY_BASE_URL"),
            max_tokens=_env_int("NOX_PRIMARY_MAX_TOKENS", 16000),
        )
    )
    # 杂活：意图分类、记忆整理、总结这类，派给便宜的
    # 单独一个后端而不是切主线的模型 —— 中途换模型会让主线缓存整个作废
    utility: LLMConfig = field(
        default_factory=lambda: _build_llm(
            backend=_env("NOX_UTILITY_BACKEND", "deepseek"),
            model=_env("NOX_UTILITY_MODEL", "deepseek-v4-flash"),
            provider_override=_env("NOX_UTILITY_PROVIDER"),
            base_override=_env("NOX_UTILITY_BASE_URL"),
            key_override=_env("DEEPSEEK_API_KEY") or _env("OPENROUTER_API_KEY"),
            max_tokens=_env_int("NOX_UTILITY_MAX_TOKENS", 4000),
        )
    )

    # 眼睛：主模型读不了图时，先让它把图看成文字，再把文字交给主模型。
    #
    # 为什么不直接把发图那一轮整个切到能看图的模型：
    #   换模型 = 12K 静态前缀缓存整段作废，而且人设、工具、情绪三层都得跟着换一遍。
    #   这样只多一次几百毫秒的小调用，主线一个字节都不动。
    #
    # 默认 DeepSeek 官方识图模型 deepseek-v4-flash-vision-exp（2026-08-21 上线，
    # OpenAI 兼容 /image_url + base64，价格与 v4-flash 相同）。key 复用
    # DEEPSEEK_API_KEY，不用另配。
    # 之前默认百炼 qwen3.5-flash（dashscope），想换回去就设
    # NOX_VISION_BACKEND=dashscope + NOX_VISION_MODEL=qwen3.5-flash。
    vision: LLMConfig = field(
        default_factory=lambda: _build_llm(
            backend=_env("NOX_VISION_BACKEND", "deepseek"),
            model=_env("NOX_VISION_MODEL", "deepseek-v4-flash-vision-exp"),
            base_override=_env("NOX_VISION_BASE_URL"),
            max_tokens=_env_int("NOX_VISION_MAX_TOKENS", 1200),
        )
    )
    vision_timeout: float = 40.0

    # ---- Ombre Brain（MCP over streamable-http）----
    ob_url: str = field(default_factory=lambda: _env("NOX_OB_URL", "http://127.0.0.1:8002/mcp"))
    ob_timeout: float = 30.0

    # ---- ha-mcp（家居控制，同样是 streamable-http 的 MCP）----
    # 留空则不注册家居工具 —— 没配也能跑，只是他控不了家电
    ha_url: str = field(default_factory=lambda: _env("NOX_HA_URL", ""))
    ha_timeout: float = 20.0

    #: 我们家那间像素房间的 MCP（caelum-room）。他在房间里的身体走这条。
    #:
    #: ⚠️ 房间的 MCP **只允许绑回环**（上游源码写死：
    #: `Room MCP must remain loopback-only`）。也就是说这个地址只有
    #: 和房间同机的进程填得上 —— Core 跑在 VPS 上时够不到糖糖电脑上的房间，
    #: 那需要另做决定（把房间搬上 VPS，或给网关加一条转发），别在这儿硬填。
    #: 没配就跳过：他控不了房间，照样能聊天。
    room_url: str = field(default_factory=lambda: _env("NOX_ROOM_URL", ""))

    # ---- health-mcp（睡眠 / 步数 / 心率）----
    # 只听本机，不出公网。留空则不注册 HealthProvider。
    # ⚠️ 必须是 streamable-http 的地址（/mcp），不是 sse ——
    # McpClient 只实现了前者，health-mcp 那边 2026-08-03 也换成了 streamable-http
    health_url: str = field(default_factory=lambda: _env("NOX_HEALTH_URL", ""))
    health_timeout: float = 20.0

    # ---- Home Assistant REST API（Location Provider 的 HA Tracker Source）----
    # 直连 HA 的 REST API（不是 ha-mcp 的 MCP 端点），查 person/device_tracker 实体。
    # 和 ha-mcp 共用同一把 HA_TOKEN，不额外申请。
    ha_api_url: str = field(default_factory=lambda: _env("NOX_HA_API_URL", "http://localhost:8123"))
    ha_api_token: str = field(default_factory=lambda: _env("NOX_HA_API_TOKEN", ""))

    # ---- 高德地图（Location Provider 逆地理编码）----
    # 把 PWA 上报的坐标转成语义（家/公司/商圈）。留空则跳过 enrich，
    # 只靠 trigger（arrive_home/leave_home）判断状态。
    # Web API key，申请地址：https://console.amap.com/dev/key/app
    gaode_key: str = field(default_factory=lambda: _env("NOX_GAODE_KEY", ""))

    # ---- 和风天气 ----
    # key 和专属域名复用 xiaozhi-server 的 get_weather 插件那一份，不新申请。
    # host 是 xxxxx.re.qweatherapi.com 这种专属域名，和公共域名分开限流。
    # 免费额度 50000 次/月，我们 30 分钟 TTL 下上界约 2900/月。
    # 留空则不注册 WeatherProvider。
    qweather_host: str = field(default_factory=lambda: _env("QWEATHER_HOST", ""))
    qweather_key: str = field(default_factory=lambda: _env("QWEATHER_KEY", ""))
    # ---- 待办清单（GitHub 上那份 todo.md）----
    # Claude 的 routines 每天读它推晨报，糖糖随口说、routine 记一笔。
    # **只读 main、只读不写**（她定的）：写入意味着两个 Claude 同时改一个文件。
    # 留空则不注册 TodoProvider。
    todo_repo: str = field(default_factory=lambda: _env("NOX_TODO_REPO", ""))
    todo_path: str = field(default_factory=lambda: _env("NOX_TODO_PATH", "todo.md"))
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN", ""))

    #: 城市 ID。101180101 = 郑州（她家）
    #
    # ⚠️ `.split()[0]` 是防御，别删：**systemd 的 EnvironmentFile 不支持行尾注释**，
    # `#` 只有在行首才算注释。写成
    #     NOX_WEATHER_LOCATION=101180101   # 郑州
    # 的话，值是「101180101   # 郑州」整串，拼进 URL 直接 InvalidURL / HTTP 400。
    # 2026-08-03 就是这么炸的，而且报错信息完全指不到注释上。
    weather_location: str = field(
        default_factory=lambda: (
            _env("NOX_WEATHER_LOCATION", "101180101").split() or ["101180101"])[0])

    # ---- bridge（相册 / 待办 / 日记的数据在它的 SQLite 里）----
    # 走 REST 而不是直接读那个 db 文件 —— 两个进程同时写 SQLite 会锁表，
    # 而 bridge 每轮对话都在写。留空则不注册这几个工具。
    bridge_url: str = field(default_factory=lambda: _env("NOX_BRIDGE_URL", ""))
    bridge_token: str = field(default_factory=lambda: _env("NOX_BRIDGE_TOKEN", ""))

    # ---- GitHub（Obsidian 云同步，2026-08-04 换掉了坚果云）----
    # 配了 GITHUB_OBSIDIAN_REPO（形如 Iristt-boop/tangtang-obsidian），
    # save_github_note / append_github_note 就直接写 GitHub 私有仓库，
    # 不再依赖电脑 Agent 在跑 —— 糖糖手机时也能用。
    # token 复用 GITHUB_TOKEN（todo.md 那个，已实测有写权限）。
    # 没配就退回原路（bridge → 电脑 Agent → 写本地文件）。
    # 坚果云 WebDAV 免费版限并发 3 连接，Remotely Save 全量同步 503，已弃。
    github_obsidian_repo: str = field(default_factory=lambda: _env("GITHUB_OBSIDIAN_REPO", ""))
    bridge_timeout: float = 10.0

    # ---- Notion（长期记忆的另一半：回忆录 + 信箱）----
    # 直连 api.notion.com。留空则不注册 notion_search / notion_read_page
    notion_token: str = field(default_factory=lambda: _env("NOTION_TOKEN", ""))
    notion_timeout: float = 15.0

    # ---- Tavily（联网搜索）----
    # 免费档 1000 credits/月、不要信用卡（2026-09-04 查证）。
    # 留空则不注册 web_search —— 宁可他没有这个工具，也不要给他一个
    # 每次都报错的工具：模型看见工具就会试，试一次错一次，比没有更糟。
    tavily_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY", ""))
    tavily_timeout: float = 20.0

    # ---- eryu（自部署网易云播放器，共听系统的播放层）----
    # 留空则不注册 eryu 工具 —— 搜歌/放歌/歌词
    eryu_url: str = field(default_factory=lambda: _env("NOX_ERYU_URL", ""))
    eryu_token: str = field(default_factory=lambda: _env("NOX_ERYU_TOKEN", ""))
    eryu_timeout: float = 12.0

    # ---- netease-music-mcp（网易云账号层：歌单/推荐/历史）----
    # 留空则不注册 netease 工具
    netease_url: str = field(default_factory=lambda: _env("NOX_NETEASE_URL", ""))
    netease_timeout: float = 12.0

    # ---- co-reading（共读的页边笔记）----
    reading_url: str = field(default_factory=lambda: _env("NOX_READING_URL", ""))
    reading_token: str = field(
        default_factory=lambda: _env("CO_READING_TOKEN") or _env("READING_TOKEN")
    )
    reading_timeout: float = 12.0

    # ---- app-tracker（今天用了什么 App，MCP）----
    tracker_url: str = field(default_factory=lambda: _env("NOX_TRACKER_URL", ""))
    tracker_timeout: float = 15.0

    # ---- 可切换的模型 ----
    # 前端下拉里的选项。每个选项**自带后端**（见 BACKENDS）——
    # 不能只存型号名：主模型一旦切到 DeepSeek，拿 DeepSeek 的 base_url 去请求
    # anthropic/claude-* 只会 404。
    #
    # ⚠️ 换模型会让**提示词缓存整段作废** —— 缓存按「模型 + 前缀字节」匹配，
    # 换一个就得重写那 12K 前缀。给糖糖偶尔换口味用的，不该来回横跳。
    models: dict[str, ModelChoice] = field(default_factory=lambda: {
        # DeepSeek 直连（2026-07-31 上线的 V4）
        "v4-flash": ModelChoice("deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash"),
        "v4-pro": ModelChoice("deepseek-v4-pro", "deepseek", "DeepSeek V4 Pro"),
        # 视觉版（2026-09-01 应糖糖加的，DeepSeek 官方第三个）。切到它
        # 当主线 = **原生看图**（supports_vision 对带 vision 的名字放行），
        # 发图不再绕「先转文字」那一跳；纯文本对话它和 v4-flash 同源
        "v4-flash-vision": ModelChoice(
            "deepseek-v4-flash-vision-exp", "deepseek", "DeepSeek V4 Flash 视觉版"),
        # OpenRouter 转发的 Claude
        "sonnet-4-6": ModelChoice("anthropic/claude-sonnet-4-6", "openrouter", "Sonnet 4.6"),
        "sonnet-5": ModelChoice("anthropic/claude-sonnet-5", "openrouter", "Sonnet 5"),
        "opus-4-6": ModelChoice("anthropic/claude-opus-4-6", "openrouter", "Opus 4.6"),
        "opus-4-7": ModelChoice("anthropic/claude-opus-4-7", "openrouter", "Opus 4.7"),
        "opus-4-8": ModelChoice("anthropic/claude-opus-4-8", "openrouter", "Opus 4.8"),
        "fable-5": ModelChoice("anthropic/claude-fable-5", "openrouter", "Fable 5"),
    })

    # ---- Agent Loop ----
    # 循环硬上限。跑满不等于答完 —— loop 会明确区分这两种结局
    max_iterations: int = field(default_factory=lambda: _env_int("NOX_MAX_ITERATIONS", 12))

    # ---- 服务 ----
    host: str = field(default_factory=lambda: _env("NOX_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("NOX_PORT", 8100))

    # ---- 会话持久化 ----
    # 默认落在 nox-core/data/sessions.db（已在 .gitignore 里）
    db_path: str = field(
        default_factory=lambda: _env(
            "NOX_DB_PATH", str(Path(__file__).resolve().parent / "data" / "sessions.db")
        )
    )
    # 恢复会话时最多带回多少条历史。聊久了几百条，全塞进 prompt 会撑爆
    # 上下文且大部分与当下无关。40 条 ≈ 20 个来回
    history_limit: int = field(default_factory=lambda: _env_int("NOX_HISTORY_LIMIT", 40))

    # ---- 上下文压缩（token 预算驱动，2026-08-14 加）----
    # 取代"死卡 40 条"：快接近预算上限时，把最老一批已完成对话压成
    # summary（context/compactor.py），最近窗口保留原文。历史不删除。
    # 总预算：静态前缀 ≈ 7k + 工具定义，加上历史/上下文/输出。DeepSeek
    # 128k 窗口，这里只规划"历史+摘要"这一段，别跟 128k 混淆。
    context_budget_tokens: int = field(
        default_factory=lambda: _env_int("NOX_CONTEXT_BUDGET_TOKENS", 20000))
    # 最近原文窗口：压缩时保留原文的 token 预算
    recent_window_tokens: int = field(
        default_factory=lambda: _env_int("NOX_RECENT_WINDOW_TOKENS", 8000))
    # summary 生成预算（传给摘要模型的约束）
    summary_budget_tokens: int = field(
        default_factory=lambda: _env_int("NOX_SUMMARY_BUDGET_TOKENS", 3000))

    def check(self) -> list[str]:
        """返回配置问题清单，空列表表示可以启动。"""
        problems: list[str] = []
        if not self.primary.usable:
            problems.append(
                f"主模型不可用：provider={self.primary.provider} "
                f"model={self.primary.model or '<空>'} "
                f"api_key={'已设置' if self.primary.api_key else '缺失'}"
            )
        if not self.ob_url:
            problems.append("NOX_OB_URL 未设置，记忆层无法连接")
        return problems


config = Config()
