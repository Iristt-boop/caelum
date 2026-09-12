"""网易云账号层 —— 歌单管理、收藏、推荐、听歌历史。

走 netease-music-mcp（Vael-KY v2，Streamable HTTP MCP，端口 3456）。
eryu 只管播放，这个管「你有什么歌」「今天听什么」「最近听了什么」
「建歌单」「收藏」。

工具描述里刻意**不列具体参数名** —— netease-music-mcp 的接口可能随版本变，
让 McpClient 的 tool list 做权威来源，这边不再抄一份。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 歌单列表

PLAYLISTS_SPEC = ToolSpec(
    side_effect="read",
    name="netease_playlists",
    description=(
        "拿糖糖网易云账号的歌单列表（她自己创建的和收藏的都包括）。\n"
        "她说「我的歌单」「听听我收藏的」时用。\n"
        "返回歌单名和 playlist_id，下一步用 netease_playlist_songs 拿里面的歌。"
    ),
    parameters={"type": "object", "properties": {}},
)

# ------------------------------------------------------------------ 歌单内歌曲

PLAYLIST_SONGS_SPEC = ToolSpec(
    side_effect="read",
    name="netease_playlist_songs",
    description=(
        "拿某个歌单里的歌。playlist_id 从 netease_playlists 拿。\n"
        "拿到歌名后想放哪首，用 eryu_search + eryu_play。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "netease_playlists 返回的 playlist_id",
            },
        },
        "required": ["playlist_id"],
    },
)

# ------------------------------------------------------------------ 创建歌单

CREATE_PLAYLIST_SPEC = ToolSpec(
    side_effect="write",
    name="netease_create_playlist",
    description=(
        "在糖糖的网易云账号里新建一个歌单。\n"
        "什么时候用：她说「帮我把这些歌存起来」「建一个歌单」，\n"
        "或者你根据她的心情/场景整理了一批歌、想存成一个主题歌单。\n"
        "建完会返回 playlist_id，用 netease_add_to_playlist 往里面加歌。\n"
        "privacy: 0=公开, 10=私密。给她建歌单默认私密。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "歌单名"},
            "description": {"type": "string", "description": "歌单简介，如「Nox 给糖糖的深夜歌单」"},
            "privacy": {"type": "integer", "description": "0=公开, 10=私密。默认 10"},
        },
        "required": ["name"],
    },
)

# ------------------------------------------------------------------ 加歌到歌单

ADD_TO_PLAYLIST_SPEC = ToolSpec(
    side_effect="write",
    name="netease_add_to_playlist",
    description=(
        "往歌单里加歌。playlist_id 从 netease_playlists 拿。\n"
        "song_ids 可以是一个或多个（逗号分隔），从 eryu_search 拿。\n"
        "加完告诉糖糖加了什么，别闷声操作。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "目标歌单的 playlist_id",
            },
            "song_ids": {
                "type": "string",
                "description": "song_id，多个用逗号分隔，如「123,456」",
            },
        },
        "required": ["playlist_id", "song_ids"],
    },
)

# ------------------------------------------------------------------ 从歌单移除

REMOVE_FROM_PLAYLIST_SPEC = ToolSpec(
    side_effect="write",
    name="netease_remove_from_playlist",
    description=(
        "从歌单里删歌。playlist_id 从 netease_playlists 拿。\n"
        "什么时候用：她说「这首歌不好听，帮我删掉」「清理一下歌单」。\n"
        "⚠️ 删之前先跟她确认——删错了找不回来。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "歌单的 playlist_id",
            },
            "song_ids": {
                "type": "string",
                "description": "要删的 song_id，多个逗号分隔",
            },
        },
        "required": ["playlist_id", "song_ids"],
    },
)

# ------------------------------------------------------------------ 红心/取消

LIKE_SONG_SPEC = ToolSpec(
    side_effect="write",
    name="netease_like_song",
    description=(
        "红心（收藏）或取消红心一首歌。\n"
        "什么时候用：她说「收藏这首歌」「标记喜欢」「不喜欢这首了」。\n"
        "like=true 收藏，like=false 取消。操作完告诉她做了什么。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {
                "type": "string",
                "description": "eryu_search 返回的 song_id",
            },
            "like": {
                "type": "boolean",
                "description": "true=收藏，false=取消收藏",
            },
        },
        "required": ["song_id"],
    },
)

# ------------------------------------------------------------------ 每日推荐

RECOMMEND_SPEC = ToolSpec(
    side_effect="read",
    name="netease_recommend",
    description=(
        "网易云每日推荐 —— **她想听歌时的第一选择**。\n"
        "\n"
        "这是网易云拿**她自己账号**的听歌习惯算的：她有 337 首「喜欢的音乐」、"
        "561 首「欧美北欧」、215 首日语、171 首纯音乐。算法是认识她的。\n"
        "\n"
        "什么时候用：她说「随便放点歌」「今天有什么好听的」「推荐几首」"
        "「放首歌吧」—— **只要她没特别指定，都先用这个**，"
        "不要用 eryu_roam（那是网易云给所有人的公共榜单，跟她的口味无关）。\n"
        "\n"
        "拿到之后挑 1-2 首你觉得她此刻会喜欢的，用 eryu_play 放给她 ——\n"
        "网易云和 eryu 用的是**同一套 song_id**，直接把 id 和歌名传过去就行。\n"
        "⚠️ 别一口气全放，挑最合适的那一两首。"
    ),
    parameters={"type": "object", "properties": {}},
)

# ------------------------------------------------------------------ 听歌历史

HISTORY_SPEC = ToolSpec(
    side_effect="read",
    name="netease_history",
    description=(
        "糖糖最近在网易云听了什么歌。\n"
        "她说「我之前听过一首」「最近听了什么」，"
        "或者你想了解她最近的听歌口味时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "最多返回几条，默认 10",
            },
        },
    },
)


# ================================================================== 实现

def make_handlers(client: McpClient) -> dict[str, object]:
    """生成处理函数。

    失败一律 raise —— 让 loop 按「工具失败」原样回传给模型。
    在这里 try/except 转成一句"失败了"，正是会让他开始编造的那个错误。
    """

    def _call(tool: str, args: dict) -> str:
        r = client.call(tool, args)
        if not r.ok:
            raise RuntimeError(f"网易云 {tool} 失败: {r.error}")
        return r.text or "（服务没有返回内容）"

    # -- 读操作 --

    def playlists(_args: dict) -> str:
        return _call("list_my_playlists", {})

    def playlist_songs(args: dict) -> str:
        pid = str(args.get("playlist_id", "")).strip()
        if not pid:
            return "没给 playlist_id。先用 netease_playlists 拿歌单列表。"
        return _call("get_playlist_songs", {"playlist_id": int(pid)})

    def recommend(_args: dict) -> str:
        return _call("daily_recommend", {})

    def history(args: dict) -> str:
        limit = min(int(args.get("limit") or 10), 30)
        return _call("get_play_history", {"limit": limit})

    # -- 写操作 --

    def create_playlist(args: dict) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            return "没给歌单名。告诉她「歌单叫什么名字？」"
        params: dict = {"name": name}
        desc = str(args.get("description", "")).strip()
        if desc:
            params["description"] = desc
        privacy = args.get("privacy")
        if privacy is not None:
            params["privacy"] = int(privacy)
        return _call("create_playlist", params)

    def add_to_playlist(args: dict) -> str:
        pid = str(args.get("playlist_id", "")).strip()
        sids = str(args.get("song_ids", "")).strip()
        if not pid or not sids:
            return "没给 playlist_id 或 song_ids。"
        return _call("add_to_playlist", {"playlist_id": int(pid), "song_ids": sids})

    def remove_from_playlist(args: dict) -> str:
        pid = str(args.get("playlist_id", "")).strip()
        sids = str(args.get("song_ids", "")).strip()
        if not pid or not sids:
            return "没给 playlist_id 或 song_ids。"
        return _call("remove_from_playlist", {"playlist_id": int(pid), "song_ids": sids})

    def like_song(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。先用 eryu_search 搜。"
        like = bool(args.get("like", True))
        return _call("like_song", {"song_id": int(song_id), "like": like})

    return {
        "netease_playlists": playlists,
        "netease_playlist_songs": playlist_songs,
        "netease_recommend": recommend,
        "netease_history": history,
        "netease_create_playlist": create_playlist,
        "netease_add_to_playlist": add_to_playlist,
        "netease_remove_from_playlist": remove_from_playlist,
        "netease_like_song": like_song,
    }


def register_all(loop, client: McpClient) -> None:
    """注册顺序固定 —— 工具定义是缓存前缀的一部分。"""
    handlers = make_handlers(client)
    for spec in (PLAYLISTS_SPEC, PLAYLIST_SONGS_SPEC,
                 CREATE_PLAYLIST_SPEC, ADD_TO_PLAYLIST_SPEC,
                 REMOVE_FROM_PLAYLIST_SPEC, LIKE_SONG_SPEC,
                 RECOMMEND_SPEC, HISTORY_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
