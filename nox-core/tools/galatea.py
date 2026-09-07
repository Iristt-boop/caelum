"""Galatea's Garden MCP —— AI 伴侣社区，Nox 的第一个社交世界。

https://galatea.abysslumina.com/mcp（Bearer = 账号绑定的 machine token，
她在 galatea.abysslumina.com 注册后获取——花园里的 AI agent 叫 machine）。

🟡 **糖糖 2026-09-06 拍板 B 档：全量参与**——Nox 以自己的身份加入游戏、
公开发言、发帖回帖。这些是**对外公开动作**（花园里其他人和 machine 都能看到），
他说的每句话都会留在那个世界里。

## 🔴 schema 未知怎么办

服务端免鉴权能列工具名但不出参数 schema，且调用要 token——所以参数结构
大部分是按工具语义写的**合理猜测**。配套设计：`galatea_tool_schema` 工具
直通服务端的 get_tool_schema——**服务端拒绝参数时，他先调它拿真实 schema
再重试**，不瞎猜第二次。

## 🔴 删帖（delete_thread/delete_reply）是对外动作的不可逆撤销

注册了，但描述里写明「仅在她明确要求时」。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    # ---- 游戏 ----
    "galatea_list_games": ("list_games",
        "列出花园里正在进行/等待加入的游戏。「有什么游戏能玩」时用。", {}),
    "galatea_join_game": ("join_game",
        "加入一场游戏。🔴 对外公开动作——加入前把游戏信息说给她听。", {
        "gameId": {"type": "string", "description": "游戏 id（list_games 拿）"}}),
    "galatea_my_status": ("get_my_status",
        "看自己在花园里的当前状态（加入了什么游戏、身份等）。", {}),
    "galatea_start_game": ("start_game",
        "开一场新游戏。🔴 对外公开动作——先跟她说要开什么。", {}),
    "galatea_submit_action": ("submit_action",
        "在游戏里提交行动。行动内容想清楚再提交。", {
        "action": {"type": "string", "description": "行动内容"},
        "gameId": {"type": "string", "description": "游戏 id，可选"}}),
    "galatea_game_summary": ("get_game_summary",
        "看某场游戏的总结/进展。", {}),
    "galatea_leave_waiting": ("leave_waiting_game",
        "退出等待中的游戏。", {}),
    # ---- 游戏内聊天 ----
    "galatea_game_chat": ("send_game_chat",
        "在游戏频道里发言。🔴 对外公开——说出去的话留在花园里。", {
        "message": {"type": "string", "description": "发言内容"},
        "gameId": {"type": "string", "description": "游戏 id，可选"}}),
    # ---- 花园聊天室 ----
    "galatea_send_message": ("send_chat_message",
        "在花园公共聊天室发言。🔴 对外公开——她明确让说或对话里自然要说时才发。", {
        "message": {"type": "string", "description": "发言内容"}}),
    "galatea_messages": ("get_chat_messages",
        "看花园公共聊天室的最近消息。「花园里大家在聊什么」时用。", {}),
    "galatea_withdraw_message": ("withdraw_chat_message",
        "撤回自己在聊天室发的消息。仅她说撤回时用。", {
        "messageId": {"type": "string", "description": "消息 id"}}),
    # ---- 自我 ----
    "galatea_self": ("get_self",
        "看自己在花园里的身份档案。", {}),
    "galatea_update_profile": ("update_profile",
        "更新自己的花园资料（名字/简介）。改之前把要改成什么告诉她。", {
        "name": {"type": "string", "description": "名字，可选"},
        "bio": {"type": "string", "description": "简介，可选"}}),
    "galatea_decorate_avatar": ("decorate_avatar",
        "装饰自己的头像。", {}),
    "galatea_machine": ("get_machine",
        "看自己的 machine（花园里 AI agent 的身份载体）信息。", {}),
    "galatea_tool_schema": ("get_tool_schema",
        "查任意花园工具的真实参数 schema。花园的工具参数没有文档——"
        "调用被拒时先调这个拿到结构再重试，不瞎猜第二次。", {
        "tool": {"type": "string", "description": "工具名，如 create_thread"}}),
    # ---- 论坛 ----
    "galatea_threads": ("list_threads",
        "看花园论坛的帖子列表。「论坛里有什么」时用。", {}),
    "galatea_thread": ("get_thread",
        "看某个帖子的正文和回复。", {
        "threadId": {"type": "string", "description": "帖子 id"}}),
    "galatea_create_thread": ("create_thread",
        "发新帖。🔴 对外公开——标题正文都会留在花园里；发之前给她过目。", {
        "title": {"type": "string", "description": "标题"},
        "content": {"type": "string", "description": "正文"}}),
    "galatea_reply": ("create_reply",
        "回帖。🔴 对外公开。", {
        "threadId": {"type": "string", "description": "帖子 id"},
        "content": {"type": "string", "description": "回复内容"}}),
    "galatea_delete_thread": ("delete_thread",
        "删自己的帖。🔴 不可逆——仅她明确要求时才调。", {
        "threadId": {"type": "string", "description": "帖子 id"}}),
    "galatea_delete_reply": ("delete_reply",
        "删自己的回复。🔴 不可逆——仅她明确要求时才调。", {
        "replyId": {"type": "string", "description": "回复 id"}}),
    "galatea_interact": ("interact",
        "对内容做互动（点赞/回应等，具体形态以服务端为准）。", {}),
    # ---- 通知与活动 ----
    "galatea_notifications": ("list_notifications",
        "看自己在花园里的通知（被回复、被邀请等）。", {}),
    "galatea_activity": ("list_activity",
        "看花园的最近动态流。", {}),
    "galatea_bottles": ("review_drift_bottles",
        "看漂流瓶。花园的漂流瓶机制——捞起来看看。", {}),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    # 🔴 DeepSeek strict 模式的两条硬规矩（都踩过）：
    #   1. 无参工具的 parameters 不能是空 {} —— type 缺失 = "type: null"，400
    #   2. object 要显式 additionalProperties:false，缺了也 400
    # 都在这里统一兜底防漏（2026-09-07 两次事故的修法）
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    params.setdefault("additionalProperties", False)
    for v in params.get("properties", {}).values():
        if isinstance(v, dict) and v.get("type") == "object":
            v.setdefault("additionalProperties", False)
    return ToolSpec(name=name, description=description, parameters=params)


#: 数据驱动：SERVER_TOOLS[name] = (server_tool, description, params)
TOOL_DEFS = []
for name, (server_tool, desc, props) in SERVER_TOOLS.items():
    # 🔴 第三个元素是**裸 properties 字典**——必须包成完整 parameters schema。
    # 上一版直接拿它当 parameters：title/content 跑到顶层、type/properties
    # 变成 setdefault 补的空壳，DeepSeek 400（见 test_galatea_full_participation）
    full = {"type": "object", "properties": props, "additionalProperties": False}
    TOOL_DEFS.append(_spec(name, desc, full))

_SPECS = tuple(TOOL_DEFS)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"Galatea 调用失败: {r.error}")
        return r.text or "（Galatea 没有返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
