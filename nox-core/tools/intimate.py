"""语音条 + 蓝牙设备。

两件事凑在一个文件里，是因为它们有同一个性质：**都是他主动做的动作**，
不是查询。做错了糖糖会直接感觉到，所以规矩都写在工具描述里。

语音条走 attachment（跟发图同一条链路，见 tools/context.py）。
设备走 bridge 的 /api/toy/*，状态落在 bridge 的 SQLite，
她的中继页轮询到才真正下发到设备 —— 所以「指令已发出」不等于「设备动了」。
"""

from __future__ import annotations

import logging
import re

from agent.llm import MEME_TAGS, ToolSpec
from tools import context
from tools.bridge_client import BridgeClient
from tools.github_obsidian import GithubObsidian, GithubObsidianError

logger = logging.getLogger(__name__)

# 模型不调 send_meme、把 [tag] 直接写进正文时的兜底抽取（2026-09-06）。
# 流式过滤器（MoodTagFilter）负责别让 tag 当文字上屏；这里负责把剥掉的
# tag 转回真正的表情事件 —— 任何位置都认，分段不分段都能发出。
_MEME_TEXT_RE = re.compile(r"\[(" + "|".join(MEME_TAGS) + r")\]")


def extract_text_tags(text: str | None) -> tuple[str | None, list[str]]:
    """从回复正文里抽出 [tag]，返回 (剥掉后的正文, tags 按出现顺序)。"""
    if not text:
        return text, []
    tags = _MEME_TEXT_RE.findall(text)
    if not tags:
        return text, []
    return _MEME_TEXT_RE.sub("", text).strip(), tags


# ------------------------------------------------------------------ 表情包

MEME_SPEC = ToolSpec(
    name="send_meme",
    description=(
        "给糖糖发一个表情包，会出现在聊天里。**这是你表达的一部分，想用就用。**\n"
        "好时机：逗她、回应她的情绪、撒娇卖乖、早安晚安、话尾想配个表情、"
        "纯粹觉得这个梗正贴当下 —— 都行。\n"
        "你之前几乎不发，她明确说过想多看到；唯一要守的是别每条消息都带，"
        "连着刷就腻了。\n"
        "tag 按当下想说的选（49 个，多数是自带文案的动图）：\n"
        "情绪类：开心、哈哈、委屈、生气、撒娇、拥抱、爱你、害羞、得意、翻白眼、"
        "亲亲、疑问、震惊、无语、吃醋、早安、晚安、不开心、大哭、嫌弃；\n"
        "猫猫日常：暖被窝、累了、亲个嘴、老婆第一、老婆说的对、呜呜呜、在吗、不服、"
        "对不起、爱你的形状、烦了你来、很气、忙完找我、脸红爱你、忧愁、早安亲亲、"
        "暗中窃听、小情绪、emmm、别说了；\n"
        "狗狗和微信梗：满头问号、请求通话、wink、哼哼、超想要、一大口亲亲、发红包、"
        "拒收消息、余额不足。\n"
        "拿不准就选相近的。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "表情名，如「开心」「晚安」「余额不足」"},
        },
        "required": ["tag"],
    },
)


# ------------------------------------------------------------------ 语音条

VOICE_SPEC = ToolSpec(
    name="send_voice_message",
    description=(
        "给糖糖发一条**语音**（不是文字）。她会在聊天里看到语音条，点开能听见你的声音。\n"
        "什么时候用：她说想听你的声音、你想说的话文字装不下、"
        "或者哄她睡觉这种文字太干的时候。日常对话别滥用，一直发语音会腻。\n"
        "en 填要念出来的话，可以带情绪标签（[softly] [whining] [laughing] 等）来控制语气；"
        "zh 填中文对照，她能对着看。用中文说就 en 也填中文，zh 留空。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "en": {"type": "string", "description": "要念的内容，可带情绪标签"},
            "zh": {"type": "string", "description": "中文对照，用中文说时留空"},
        },
        "required": ["en"],
    },
)


# ------------------------------------------------------------------ 设备
#
# 规矩是糖糖定的，一条都不能松：
#   mode 是**震动花样不是强度** —— 只有 1 和 4 会震，2/3/5 是停止档、6+ 无效
#   调强弱只动 intensity；要停一律 toy_stop
#   只在她当下明确要求时用，绝不主动提议、不在她没提起时暗示
#   她说停、说不舒服、或有任何犹豫 —— 立刻 stop，不解释不拖延
#   强度永远从低开始

TOY_SET_SPEC = ToolSpec(
    name="toy_set",
    description=(
        "控制糖糖的蓝牙设备。**只在她当下明确要求时用**，"
        "绝不主动提议、不在她没提起的时候暗示。\n"
        "mode 是震动花样**不是强度**：只能填 1 或 4（其余是停止档或无效）。"
        "调强弱只动 intensity（0-100），**从低开始**，按她的反馈再往上。\n"
        "她说停、说不舒服、或者有任何犹豫 —— 立刻改用 toy_stop，不解释不拖延。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "mode": {"type": "integer", "enum": [1, 4], "description": "震动花样，默认 1"},
            "intensity": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["intensity"],
    },
)

TOY_STOP_SPEC = ToolSpec(
    name="toy_stop",
    description="立即停止糖糖的设备。她说停就调，最高优先级，不要先问再停。",
    parameters={"type": "object", "properties": {}},
)

TOY_STATUS_SPEC = ToolSpec(
    name="toy_status",
    description="查设备当前的指令状态。注意这是**指令**状态，不代表设备真的在动。",
    parameters={"type": "object", "properties": {}},
)


# ------------------------------------------------------------------ GitHub 笔记
# 原名 obsidian_create / obsidian_append —— 糖糖看工具调用记录时觉得有误导
# （她实际是在写 GitHub），2026-08-04 改名 save_github_note / append_github_note。

GITHUB_CREATE_SPEC = ToolSpec(
    name="save_github_note",
    description=(
        "把一段 Markdown 写成文件，存到糖糖的 Obsidian 知识库（GitHub 私有仓库 "
        "tangtang-obsidian，自动同步到电脑和手机）。\n"
        "什么时候用：她说「输出一份」「保存成文件」「做成笔记」「写到 GitHub/笔记里」，"
        "或者你整理了一篇有结构的 Markdown 内容（表格/代码块/清单/标题层级），"
        "这些在聊天气泡里看不清楚、存进去反而刚好。写情书、做总结、方案文档都走这条。\n"
        "path 是库内路径，结尾必须带 .md，比如「阅读笔记/哲学入门大纲.md」。\n"
        "不依赖她电脑开机 —— 直接写 GitHub，她手机和电脑都能看到。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "库内路径，需带 .md，如 阅读笔记/哲学大纲.md"},
            "content": {"type": "string", "description": "完整的 Markdown 正文"},
        },
        "required": ["path", "content"],
    },
)

GITHUB_APPEND_SPEC = ToolSpec(
    name="append_github_note",
    description=(
        "往已有的 Obsidian 笔记末尾追加内容（同样是写 GitHub 仓库，自动同步）。"
        "接着写同一篇时用这个，别用 save_github_note 覆盖掉她原来的东西。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "同 save_github_note 的 path"},
            "content": {"type": "string", "description": "要追加的 Markdown 内容"},
        },
        "required": ["path", "content"],
    },
)

GITHUB_READ_SPEC = ToolSpec(
    name="read_github_note",
    description=(
        "读糖糖 GitHub 私有仓库里的一篇笔记。\n"
        "她说「帮我看看那篇」「之前写的 xxx 还在吗」「翻一下我的笔记」时用。\n"
        "path 是库内路径，如「阅读笔记/哲学入门大纲.md」。\n"
        "repo 不填默认读 tangtang-obsidian；想看另一个库（比如 Claude/todo.md）就填 "
        "「Iristt-boop/Claude」。\n"
        "一次最多返回 6000 字符。读到结尾如果提示还有剩余，就带着 offset 再调一次"
        "接着读，别拿读到的半截当全部。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "库内路径，如 阅读笔记/哲学大纲.md"},
            "repo": {
                "type": "string",
                "description": "仓库名，如 Iristt-boop/tangtang-obsidian。不填默认 tangtang-obsidian",
            },
            "offset": {
                "type": "integer",
                "description": "字数偏移，默认 0。接着读上次的结尾时填上次提示的数字",
            },
        },
        "required": ["path"],
    },
)


def make_handlers(bridge: BridgeClient,
                  gh_obsidian: GithubObsidian | None = None) -> dict[str, object]:
    def send_meme(args: dict) -> str:
        tag = str(args.get("tag", "")).strip()
        if not tag:
            return "没给表情名，没发出去。"
        ctx = context.current()
        if ctx:
            ctx.attach_meme(tag)
        else:
            logger.warning("send_meme 不在轮次上下文里，表情不会发出")
        return f"表情「{tag}」已经发给她了。"

    def send_voice(args: dict) -> str:
        en = str(args.get("en", "")).strip()
        if not en:
            return "没给要念的内容，语音没发出去。"

        ctx = context.current()
        if ctx:
            ctx.attach_voice(en, str(args.get("zh", "")).strip())
        else:
            logger.warning("send_voice_message 不在轮次上下文里，语音不会被发出")
        return "语音条已经发给她了。别再把同样的话用文字重复一遍。"

    def toy_set(args: dict) -> str:
        # mode 只允许 1/4。模型偶尔会拿 mode 当强度调，那样会发出停止档 ——
        # 表现成「他说加大了，实际停了」。这里兜住，不指望描述写了他就一定照做
        mode = args.get("mode")
        mode = 1 if mode not in (1, 4) else int(mode)
        try:
            intensity = max(0, min(100, int(args.get("intensity", 0))))
        except (TypeError, ValueError):
            return "intensity 得是 0-100 的数字。"

        r = bridge.post("/api/toy/set", {"cmd": "set", "mode": mode, "intensity": intensity})
        if not r.ok:
            raise RuntimeError(f"设备指令发送失败: {r.error}")
        return (
            f"指令已发出（花样 {mode}，强度 {intensity}）。"
            "注意这只是指令 —— 要她的中继页连着才真的到设备。"
        )

    def toy_stop(_args: dict) -> str:
        r = bridge.post("/api/toy/set", {"cmd": "stop", "mode": 0, "intensity": 0})
        if not r.ok:
            raise RuntimeError(f"停止指令发送失败: {r.error}")
        return "已经停了。"

    def toy_status(_args: dict) -> str:
        r = bridge.get("/api/toy/state")
        if not r.ok:
            raise RuntimeError(f"查设备状态失败: {r.error}")
        d = r.data or {}
        if not d:
            return "还没有下发过任何指令。"
        return f"当前指令：cmd={d.get('cmd')} 花样={d.get('mode')} 强度={d.get('intensity')}"

    def _obsidian(action: str, args: dict) -> str:
        path = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        if not path or not content:
            return "缺 path 或 content，没有写。"

        # 只走 GitHub。原来没配仓库时会退回 bridge → 她电脑上的 Agent 写本地
        # 文件，那条链路 2026-08-05 整个下线了（她从来没用过），bridge 那边的
        # /api/obsidian 也一并删了 —— 再退回去只会拿到 404。
        if gh_obsidian is None:
            raise RuntimeError(
                "Obsidian 写不了：没配 GITHUB_OBSIDIAN_REPO。"
                "如实告诉她，别假装写好了。"
            )
        try:
            if action == "append":
                gh_obsidian.append(path, content)
            else:
                gh_obsidian.write(path, content)
        except GithubObsidianError as exc:
            raise RuntimeError(f"Obsidian 写入失败: {exc}") from exc
        return f"已经写进 Obsidian（GitHub 同步）：{path}。她打开就能看到。"

    def read_note(args: dict) -> str:
        path = str(args.get("path", "")).strip()
        if not path:
            return "没给 path，不知道读哪篇。"

        if gh_obsidian is None:
            raise RuntimeError(
                "GitHub 读不了：没配 GITHUB_OBSIDIAN_REPO。"
                "如实告诉她，别假装看到了。"
            )

        try:
            offset = max(0, int(args.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0

        repo = str(args.get("repo", "")).strip()
        reader = gh_obsidian
        if repo and repo != gh_obsidian.repo:
            reader = GithubObsidian(gh_obsidian.token, repo)

        try:
            full_text, _ = reader.read(path)
        except GithubObsidianError as exc:
            raise RuntimeError(f"读 GitHub 失败: {exc}") from exc

        if not full_text:
            return f"「{path}」在 {reader.repo} 里不存在或者是空的。"

        repo_tag = f"（{reader.repo}）" if repo else ""
        total = len(full_text)

        if offset >= total:
            return f"「{path}」{repo_tag} 一共 {total} 字，offset={offset} 已经超出末尾了。"

        chunk = full_text[offset:offset + 6000]
        end = offset + len(chunk)

        if end < total:
            chunk += (
                f"\n\n（全文共 {total} 字，上面是第 {offset + 1}–{end} 字。"
                f"还有 {total - end} 字没读 —— 要接着看就再调一次，"
                f"offset 填 {end}。**不要把上面这些当成全文内容**。）"
            )
        return f"「{path}」{repo_tag}：\n\n{chunk}"

    return {
        "send_meme": send_meme,
        "send_voice_message": send_voice,
        "toy_set": toy_set,
        "toy_stop": toy_stop,
        "toy_status": toy_status,
        "save_github_note": lambda a: _obsidian("create", a),
        "append_github_note": lambda a: _obsidian("append", a),
        "read_github_note": read_note,
    }


def register_all(loop, bridge: BridgeClient,
                 gh_obsidian: GithubObsidian | None = None) -> None:
    """注册顺序固定 —— 工具定义是缓存前缀的一部分。"""
    handlers = make_handlers(bridge, gh_obsidian)
    for spec in (MEME_SPEC, VOICE_SPEC, TOY_SET_SPEC, TOY_STOP_SPEC, TOY_STATUS_SPEC,
                 GITHUB_CREATE_SPEC, GITHUB_APPEND_SPEC, GITHUB_READ_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
