"""音乐播放 —— 共听系统的播放层。

走 eryu（自部署的网易云音乐播放器）的 REST API。
鉴权是 X-Auth-Token，token 存在 VPS 的 .secret 文件里。

eryu 只管播放层的事：搜歌、放歌、歌词、频谱分析、歌曲记忆、
漫游发现、远程推歌。歌单管理、收藏、推荐走 netease.py。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools import context as tool_context
from tools.http import RestClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 搜索

SEARCH_SPEC = ToolSpec(
    name="eryu_search",
    description=(
        "在网易云音乐搜歌。返回歌名、歌手、专辑和 song_id。\n"
        "糖糖说「放首 xxx」「搜一下 xxx 这首歌」「有没有 xxx」时用。\n"
        "拿到 song_id 后用 eryu_play 播放。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "歌名、歌手或专辑关键词"},
            "limit": {
                "type": "integer",
                "description": "最多返回几条，默认 5。她没说具体要几首就给 5 条",
            },
        },
        "required": ["keyword"],
    },
)

# ------------------------------------------------------------------ 播放

PLAY_SPEC = ToolSpec(
    name="eryu_play",
    description=(
        "直接点播一首歌到糖糖的共听页面 —— 她那边最多 5 秒就会自动开始放。\n"
        "song_id / name / artist / **cover** 全都从 eryu_search 的结果里抄，"
        "**一个都不要漏，也不要自己编**。\n"
        "⚠️ **cover 尤其别漏** —— 她的播放器把封面嵌在黑胶唱片中间，"
        "不带的话唱片中央就是个空的音符占位，很难看。\n"
        "eryu_search 的每一行结尾都有 `cover=https://...`，照抄那个值。\n"
        "\n"
        "⚠️ 前提是她**开着 Caelum App**。页面没开就没人来取这首歌，"
        "它会一直等在队列里。所以放完跟她说一声。\n"
        "\n"
        "**一次要给一串，不要只给一首。** 用 `queue` 参数排后面的歌"
        "（最多 10 首），第一首放完会自动接上。\n"
        "只给一首的话三分钟后就没声了 —— 除非她明确说「就听这一首」。\n"
        "\n"
        "她说这几种话时的排法：\n"
        "  「放 XXX」（具体歌名）→ 放那首，queue 里排同歌手或相似的\n"
        "  「放点 XX 的歌」（歌手）→ 用 eryu_search 搜那个歌手，排一串\n"
        "  「随便放点」「放着别停」→ 用 netease_recommend 拿她的每日推荐，排 10 首\n"
        "\n"
        "⚠️ 这样点播的歌会被记成「一起听过」，她自己点的不算。\n"
        "\n"
        "**每次都要写 `reason`** —— 你为什么选这首。它会留在她的共听页上，"
        "是那一页最要紧的东西。写真实的理由，别写「这是一首好听的歌」。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
            "name": {"type": "string", "description": "歌名，从 eryu_search 结果里抄"},
            "artist": {"type": "string", "description": "歌手，从 eryu_search 结果里抄"},
            "cover": {
                "type": "string",
                "description": "封面 URL，从 eryu_search 结果行尾的 cover= 抄过来。"
                               "**别省略** —— 唱片中间要用它",
            },
            "reason": {
                "type": "string",
                "description": (
                    "**你为什么选这首给她。一句话，说人话。**\n"
                    "会存进歌曲记忆，显示在她共听页「小克给我放过的」那张卡片上，"
                    "以后翻回来还看得到。\n"
                    "写你真实的理由，别写通用的漂亮话：\n"
                    "  ✅「你说昨晚总醒，这首慢得能睡着」\n"
                    "  ✅「上次你听这个歌手听了三遍」\n"
                    "  ✅「今天降温了，想给你放点暖的」\n"
                    "  ❌「这是一首很好听的歌」（等于没说）\n"
                    "  ❌「根据你的喜好推荐」（假的，你并没有据此挑）"
                ),
            },
            "queue": {
                "type": "array",
                "description": (
                    "接下来要连着放的歌，最多 10 首。第一首放完自动接上。\n"
                    "她说「随便放点歌」「放一串」「循环」时**一定要给这个** —— "
                    "只给一首的话，三分钟后就没声了。\n"
                    "每首和上面一样要带 song_id / name / artist / cover。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "song_id": {"type": "string"},
                        "name": {"type": "string"},
                        "artist": {"type": "string"},
                        "cover": {"type": "string"},
                    },
                    "required": ["song_id"],
                },
            },
        },
        # ⚠️ reason 是**必填**，不是可选。
        # 光在 description 里写「每次都要写」他照样漏（2026-08-11 实测，
        # 两首歌的 notes 全是空的）—— 和前一天 queue 那次一模一样。
        # 放进 required，模型就绕不过去了。
        "required": ["song_id", "reason"],
    },
)

# ------------------------------------------------------------------ 歌词

LYRIC_SPEC = ToolSpec(
    name="eryu_get_lyric",
    description=(
        "拿一首歌的歌词。song_id 从 eryu_search 拿。\n"
        "她说「这首歌在唱什么」「帮我看看歌词」，"
        "或者你想在聊到某首歌时引用歌词的时候用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
        },
        "required": ["song_id"],
    },
)

# ------------------------------------------------------------------ 频谱分析

ANALYZE_SPEC = ToolSpec(
    name="eryu_analyze",
    description=(
        "对一首歌做 AI 频谱分析，拿 BPM、调性、能量曲线等音乐特征。\n"
        "分析是异步的——后台子进程跑，可能需要几秒到十几秒。\n"
        "分析过一次的歌会缓存，下次再调直接返回缓存结果。\n"
        "什么时候用：糖糖问「这首歌是什么风格的」「节奏快不快」，\n"
        "或者你想在推荐歌曲时聊音乐特征（「这首的 BPM 很适合你现在的心情」）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
        },
        "required": ["song_id"],
    },
)

# ------------------------------------------------------------------ 歌曲记忆（读）

MEMORY_GET_SPEC = ToolSpec(
    name="eryu_get_memory",
    description=(
        "读一首歌的笔记——之前听这首歌时糖糖说了什么、你有什么感受。\n"
        "什么时候用：再次听到这首歌、糖糖提到这首歌让她想起什么，\n"
        "或者你想了解这首歌的「共同记忆」时。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
        },
        "required": ["song_id"],
    },
)

# ------------------------------------------------------------------ 歌曲记忆（写）

MEMORY_SAVE_SPEC = ToolSpec(
    name="eryu_save_memory",
    description=(
        "给一首歌写笔记——记下糖糖听到这首歌时的感受，或者你想记住的任何事。\n"
        "什么时候用：糖糖说「这首歌让我想起 xxx」、某首歌对她有特殊意义、\n"
        "或者这是你们一起听的第一首歌。以后放这首歌时可以先读一下。\n"
        "notes 写感受或记忆；tags 用逗号分隔，如「怀旧,夜晚,第一次」。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
            "notes": {"type": "string", "description": "要记下来的内容——她的感受、你们的共同记忆"},
            "tags": {"type": "string", "description": "逗号分隔的标签，如「怀旧,低落,深夜」"},
        },
        "required": ["song_id", "notes"],
    },
)

# ------------------------------------------------------------------ 漫游

ROAM_SPEC = ToolSpec(
    name="eryu_roam",
    description=(
        "随机漫游发现歌曲 —— **一次给一首**，完全随机。\n"
        "\n"
        "⚠️ **这不是「随便放点歌」的默认选择。** 她说「随便放首歌」"
        "「今天听什么」时，用 `netease_recommend`（那是她自己账号的推荐）。\n"
        "\n"
        "这个只在她**明确想要没听过的东西**时才用："
        "「来点新鲜的」「换换口味」「听点我没听过的」。\n"
        "\n"
        "⚠️ 它是从**网易云的公共榜单和流派歌单**里随机抓的"
        "（华语/欧美/日语/韩语/嘻哈/R&B/电子/民谣/摇滚 + 各地区新歌榜），\n"
        "**和糖糖自己的网易云账号无关，也不是她的收藏**。\n"
        "所以别说成「根据你的口味挑的」—— 那是骗她。可以说「随便抓了一首」。"
    ),
    parameters={"type": "object", "properties": {}},
)


DAILY_SPEC = ToolSpec(
    name="eryu_daily",
    description=(
        "拿共听页面「Liked」歌单里的歌当种子，找相似的。\n"
        "\n"
        "⚠️ **这不是「每日推荐」的默认选择。** 她要推荐时用 "
        "`netease_recommend` —— 那是她自己网易云账号算的，"
        "有 337 首「喜欢」打底。这个的种子只有她在共听页面手动加的**几首**，"
        "推出来会很单调（现在全是 Taylor Swift / Selena Gomez 那一挂）。\n"
        "\n"
        "只在一种情况下用它：她明确说「按共听页面收藏的那些推」。\n"
        "⚠️ 种子不是网易云 App 里的「我喜欢的音乐」，别搞混。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最多返回几首，默认 5"},
        },
    },
)

# ------------------------------------------------------------------ 相似歌曲

SIMILAR_SPEC = ToolSpec(
    name="eryu_similar",
    description=(
        "找和一首歌风格相似的歌曲。song_id 从 eryu_search 拿。\n"
        "什么时候用：糖糖说「有没有像这首歌一样的」「类似的还有吗」、\n"
        "听完一首觉得对味、想继续这个风格时。\n"
        "返回相似的歌名和 song_id，可以直接用 eryu_play 放。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "song_id": {"type": "string", "description": "eryu_search 返回的 song_id"},
        },
        "required": ["song_id"],
    },
)

# ------------------------------------------------------------------ 远程轮询

# ⚠️ 2026-08-08 起**不再注册**这个工具，见文件末尾 register_all 的说明。
#
# 它和 `eryu_play` 抢同一个队列：`/music/remote` 是**单向一次性队列**
# （GET 读完即删）。原设计是「她推歌 → 小克取」，而现在改成了
# 「小克点播 → 她的播放器取」。两个方向共用一个队列的话，
# 小克 poll 的时候会把自己刚点的歌取走。
#
# 顺带一提，原设计那个方向也从来没实现过 —— eryu 前端根本没有
# 「推给 Nox」这个按钮（`grep -rn remote client/` 零匹配）。
# 真要做「她推歌给小克」，得后端另开一个反向队列 + 前端加按钮。
REMOTE_SPEC = ToolSpec(
    name="eryu_remote_poll",
    description=(
        "看看糖糖那边有没有通过 eryu 网页推歌过来。\n"
        "她在 music.noxtang.com 上点「推给 Nox」，\n"
        "这首歌就会出现在这里——读到之后你可以回应、或者直接用 eryu_play 放。\n"
        "什么时候用：她说了「给你推了一首歌」「你听听这个」之类的话之后。\n"
        "⚠️ 她没提推歌就别主动调——这是她的主动行为，不是你的。"
    ),
    parameters={"type": "object", "properties": {}},
)


EXPERIENCE_SPEC = ToolSpec(
    name="eryu_experience",
    description=(
        "**音乐这件事上，你和她之间发生过什么。**\n"
        "\n"
        "一次拿到完整视图，不用分别调 recent / memory / analyze：\n"
        "  · 最近在听什么（谁放的、什么时候）\n"
        "  · 你给她放过哪些、一起听过几次、当时你说的理由\n"
        "  · 这些歌是什么气质（BPM / 能量，分析过的才有）\n"
        "\n"
        "什么时候用：想聊音乐、想知道她最近的状态、"
        "或者准备给她放歌之前先看看你们听过什么。\n"
        "⚠️ 「一起听过」只算**你点播且她听完**的，她自己点的不算。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最近听的取几条，默认 10"},
        },
    },
)

MOOD_PICK_SPEC = ToolSpec(
    name="eryu_pick_by_mood",
    description=(
        "**按气质挑歌** —— 她说「放点治愈的」「睡前来点安静的」"
        "「打扫卫生放点带劲的」这类**形容词**时用这个。\n"
        "\n"
        "从已经做过音频分析的歌里筛（真实的 BPM / 能量 / 起伏，不是猜的），"
        "返回一串可以直接喂给 `eryu_play` 的 song_id。\n"
        "\n"
        "**mood 可选：**\n"
        "  `calm`    安静治愈、适合睡前和写东西（能量 < 0.16，起伏小）\n"
        "  `warm`    温和日常、适合陪着（能量 0.16~0.25）\n"
        "  `upbeat`  有劲、适合打扫和提神（能量 > 0.25）\n"
        "  `instrumental` 偏纯音乐（谐波占比高、明亮度低）\n"
        "\n"
        "⚠️ **别只看 BPM。** 同样 103 BPM，能量 0.25 是要跟着唱的，"
        "0.10 是能睡着的 —— 这个工具已经按能量分好档了，直接用。\n"
        "⚠️ 库里只有**分析过**的歌才在候选里，现在 49 首。"
        "挑不到就说实话，别硬编。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "mood": {
                "type": "string",
                "description": "calm / warm / upbeat / instrumental",
            },
            "limit": {"type": "integer", "description": "要几首，默认 10"},
        },
        "required": ["mood"],
    },
)

RECENT_SPEC = ToolSpec(
    name="eryu_recent",
    description=(
        "看糖糖最近在共听页面听了什么歌，最新的在最前面。\n"
        "什么时候用：想知道她最近在听什么、她说「我最近老听一首歌」、"
        "或者你想跟她聊音乐但不知道从哪起头。\n"
        "⚠️ 这是她自己点的和你点给她的合在一起 —— 不要假设某首是谁放的。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最多几条，默认 10"},
        },
    },
)


# ================================================================== 实现

def make_handlers(client: RestClient) -> dict[str, object]:

    def search(args: dict) -> str:
        keyword = str(args.get("keyword", "")).strip()
        if not keyword:
            return "没给关键词，没法搜。"

        limit = min(int(args.get("limit") or 5), 20)
        # ⚠️ eryu 要的是 `q`，不是 `keyword`。发错名字它直接回
        # 400 {"error": "missing q"} —— 这个 bug 让搜歌 100% 失败了一段时间，
        # 连带放歌也用不了（放歌要先搜到 id），共听入口整个是断的。
        # 服务端实现见 VPS `/root/eryu/server/eryu.py:480`。
        #
        # 服务端不认 limit，它硬编码只取 6 条，所以这里不发 ——
        # 条数由下面的 songs[:limit] 在 Core 侧截。
        r = client.get("/music/search", {"q": keyword})
        if not r.ok:
            raise RuntimeError(f"搜歌失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        songs = data.get("songs") or []
        if not songs:
            return f"没搜到「{keyword}」相关的歌，换个关键词试试。"

        out: list[str] = []
        for s in songs[:limit]:
            # ⚠️ eryu **已经把网易云的原始结构拍平了**（server/eryu.py:509）：
            #   artist 是拼好的字符串，不是 artists 列表
            #   album  是字符串，不是 {"name": ...}
            # 照网易云原始格式写成 s.get("album", {}).get("name") 会直接抛
            # AttributeError: 'str' object has no attribute 'get' ——
            # 参数名修好之后就是栽在这一行（2026-08-08）。
            artists = s.get("artist", "")
            album = s.get("album", "")
            line = f"song_id={s.get('id')} | {s.get('name', '?')} | {artists} | 《{album}》"
            # ⚠️ **必须把封面带出来。** 原来这行没有 cover，模型手上根本
            # 没有封面地址，`eryu_play` 想带也带不了 —— 结果糖糖那边
            # 唱片中间永远是个音符占位（2026-08-10 她发现的）。
            if s.get("cover"):
                line += f" | cover={s['cover']}"
            out.append(line)
        return "\n".join(out)

    def play(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。先用 eryu_search 搜。"

        name = str(args.get("name", "")).strip()
        artist = str(args.get("artist", "")).strip()
        cover = str(args.get("cover", "")).strip()

        # 兜底：模型没带封面就自己去搜一次补上。
        # 不能全指望它每次都记得抄 —— 漏一次，她的唱片中间就是个空占位。
        # 用歌名搜，按 song_id 精确匹配，匹配不上就算了，不影响播放
        if not cover and name:
            try:
                sr = client.get("/music/search", {"q": name})
                for cand in ((sr.data or {}).get("songs") or []):
                    if str(cand.get("id")) == song_id and cand.get("cover"):
                        cover = cand["cover"]
                        break
            except Exception:  # noqa: BLE001
                pass

        # 第一步：让服务器先把音频缓存好，这样她那边点开就响。
        #
        # ⚠️ **超时不算失败。** 没缓存过的歌要从网易云现下 5 MB，
        # 12 秒的默认超时经常不够（2026-08-10 实测 TimeoutError，
        # 导致整个 eryu_play 挂掉、队列一首都没排上）。
        #
        # 但下载是在 eryu 那边继续跑的，我们等不等它都一样 ——
        # 前端真正取流是走 bridge 的 /music/stream，那时候多半已经好了。
        # 所以超时就跳过预热，直接往下走去排队列。
        #
        # ⚠️ `/music/url` 只返回 {ok, url, cached}（server/eryu.py:537）——
        # 没有 name 也没有 artists，别指望从这里拿歌名
        try:
            r = client.get("/music/url", {"id": song_id})
        except Exception:  # noqa: BLE001
            r = None
        if r is not None and r.ok:
            data = r.data if isinstance(r.data, dict) else {}
            if not data.get("ok"):
                return f"这首歌拿不到音频（{data.get('error') or '可能要 VIP 或者已下架'}）。"

        # 第二步：写进点播队列。她的播放器每 5 秒轮询一次 GET /music/remote。
        # 2026-08-08 之前前端根本没有轮询这个接口，所以点播从来没通过
        song = {"songId": song_id, "name": name, "artist": artist}
        if cover:
            song["cover"] = cover

        # 后续队列（最多 10 首）。服务端 `_handle_music_remote_post` 只是把
        # body 原样写文件、GET 时读出来，**不关心里面是什么形状** ——
        # 所以塞个数组进去它照收，服务端一行都不用改。
        #
        # ⚠️ 队列现在活在**前端内存**里（方案 B），刷新页面就没了。
        # 方向是让队列归小克管，见 nox-app/frontend/DESIGN.md 02b 节末尾的
        # 「🔭 方向」。结构已经按数组留好，将来改成服务端真队列时前端不用动。
        # ⚠️ 先过滤再截断。反过来写的话，正在放的那首会占掉一个名额，
        # 说好的 10 首实际只排到 9 首（测试抓到的）
        rest: list[dict] = []
        seen = {song_id}
        for item in (args.get("queue") or []):
            if len(rest) >= 10:
                break
            if not isinstance(item, dict):
                continue
            sid = str(item.get("song_id") or item.get("songId") or "").strip()
            if not sid or sid in seen:
                continue          # 正在放的、以及重复的，都不排
            seen.add(sid)
            rest.append({
                "songId": sid,
                "name": str(item.get("name", "")).strip(),
                "artist": str(item.get("artist", "")).strip(),
                "cover": str(item.get("cover", "")).strip(),
            })

        # 兜底：他没给 queue 就自己补。
        #
        # ⚠️ 别指望模型老实抄 —— 要他先 eryu_search 搜 10 首、再把每首的
        # 四个字段誊进数组，抄写成本太高，实测他就只给一首
        # （2026-08-10：工具描述里明写了「一次要给一串」，照样只发一首）。
        #
        # 所以这里自己搜同歌手的歌填上。宁可多花一次搜索，
        # 也别让她三分钟后突然安静。
        if not rest and artist:
            try:
                sr = client.get("/music/search", {"q": artist})
                for cand in ((sr.data or {}).get("songs") or []):
                    if len(rest) >= 9:
                        break
                    cid = str(cand.get("id") or "")
                    if not cid or cid == song_id:
                        continue
                    rest.append({
                        "songId": cid,
                        "name": cand.get("name", ""),
                        "artist": cand.get("artist", ""),
                        "cover": cand.get("cover", ""),
                    })
            except Exception:  # noqa: BLE001
                pass

        payload: dict = {"song": song}
        if rest:
            payload["queue"] = rest

        rp = client.post("/music/remote", payload)
        if not rp.ok:
            raise RuntimeError(f"点播失败: {rp.error}")

        # 把「为什么选这首」存进歌曲记忆。
        #
        # 这条会显示在共听页「小克给我放过的」那张卡片的灰底里，
        # 是那一页的灵魂 —— 没有它，那页就只是个播放历史
        # （2026-08-10 糖糖的设计稿里专门留了这一条）。
        #
        # ⚠️ 必须带 action="note"：不带的话服务端默认走 "listen" 分支，
        # 只加播放计数、**notes 一个字都不存，还返回 200**。
        reason = str(args.get("reason", "")).strip()

        # 套话挡一道。写了等于没写的话，那条灰底还不如不显示 ——
        # 「他为什么选这首」摆在她共听页上，出现一句「这是一首好听的歌」
        # 比空着更伤人
        _EMPTY = ("这是一首很好听的歌", "这是一首好听的歌", "根据你的喜好推荐",
                  "希望你喜欢", "很好听", "好听的歌", "推荐给你")
        if reason and (len(reason) < 6 or reason in _EMPTY):
            reason = ""

        if reason:
            try:
                client.post("/music/memory", {
                    "songId": song_id, "action": "note", "notes": reason,
                    "name": name, "artist": artist, "cover": cover,
                })
            except Exception:  # noqa: BLE001
                pass          # 存不上不影响放歌

        # 顺带在聊天里发一张音乐卡片（封面 + 歌名 + 艺术家 + 进度条）。
        # 这样她就算没开共听页面，也能直接在对话里点开听。
        # 卡片只带元数据，音频由前端走 bridge 的代理接口取。
        ctx = tool_context.current()
        if ctx is not None:
            ctx.attach_music(song_id, name=name, artist=artist, cover=cover)
        # 当前播放态变了 → 打掉 music Provider 的缓存（TTL 3 分钟）。
        # nox.py 里那句注释说得最准：「缓存久了会出现『他说你在听 A，
        # 其实早换成 B 了』，那比不知道更糟」。见 tools/context.py 的 `wrote()`。
        tool_context.wrote("music")

        who = f"{name} - {artist}" if name and artist else (name or f"song_id={song_id}")
        tail = f"，后面还排了 {len(rest)} 首" if rest else ""
        return (
            f"已经点给她了：{who}{tail}。她那边最多 5 秒开始放。\n"
            "（如果她没开共听页面，这首歌会等在队列里 —— 记得问一句她开着没。"
            "聊天里也发了张卡片，她可以直接点开听。）"
        )

    def lyric(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。先用 eryu_search 搜。"

        r = client.get("/music/lyric", {"id": song_id})
        if not r.ok:
            raise RuntimeError(f"拿歌词失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        lrc = (data.get("lyric") or data.get("lrc") or "")
        if not lrc:
            return "这首歌没有歌词（可能是纯音乐）。"

        # 歌词可能很长（几百行时间戳），超过 2500 字就截掉末尾
        if len(lrc) > 2500:
            lrc = lrc[:2497] + "..."
        return lrc

    def analyze(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。先用 eryu_search 搜。"

        # 先查状态，可能已经分析过了。
        #
        # ⚠️ 字段名对不上过：Core 原来找 `ready`（布尔）和 `result`，
        # 而服务端给的是 `{"status": "ready", "analysis": {...}}`
        # （server/eryu.py:988）。所以就算分析结果早就躺在
        # music_cache 里，这里也永远匹配不上，只会回一句
        # 「任务已提交，后台正在跑」—— 而 VPS 根本没装 librosa，
        # 那个后台任务注定失败（2026-08-09 查出）。
        status = client.get("/music/analyze/status", {"id": song_id})
        status_data = (status.data if isinstance(status.data, dict) else {}) if status.ok else {}
        state = str(status_data.get("status") or "")
        if state == "ready":
            return _format_analysis(song_id, status_data.get("analysis") or {})
        if state == "running":
            return f"song_id={song_id} 正在分析中，过一会儿再问我。"
        if state.startswith("error"):
            return f"song_id={song_id} 上次分析失败了：{state[6:].strip()}"

        # ⚠️ **故意不触发 VPS 上的分析**（2026-08-09）。
        #
        # 音频分析要 librosa（提 BPM / 能量 / 频谱），而 VPS 上没装 ——
        # 那台机器同时跑着 bridge / nox-core / ombre-brain / eryu /
        # netease-mcp / ha-mcp / xiaozhi，再塞一个吃满 CPU 的音频分析
        # 会拖累对话响应。
        #
        # 分析改在糖糖的电脑上跑（`ob-tools/audio/analyze_local.py`，
        # i5-13400F 十六核，单首约 24 秒、六路并行），结果回传到
        # VPS 的 music_cache。eryu 本来就允许这样 —— 它的
        # `/music/analyze/status` 只是去读 `{id}_preanalysis.json`，
        # 不关心那个文件是谁算出来的。
        #
        # 这里要是还去 POST 触发，只会在 VPS 上跑一个必定失败的任务，
        # 还会留下 `_analyze_error.txt` —— 下次再查就变成
        # 「上次分析失败了」，把本来只是「还没分析」的状态弄脏。
        return (
            f"song_id={song_id} 这首还没有分析过。\n"
            "音频特征是在糖糖电脑上批量跑的，得等她那边跑一轮才会有。\n"
            "**现在别硬猜 BPM 和能量** —— 可以改用 eryu_get_lyric 看歌词，"
            "从词和曲名去理解这首歌。"
        )

    def get_memory(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。"

        r = client.get("/music/memory", {"id": song_id})
        if not r.ok:
            raise RuntimeError(f"读歌曲记忆失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        if not data or not data.get("notes"):
            # 先查一下歌名，让回复更有温度
            info = client.get("/music/url", {"id": song_id})
            name = song_id
            if info.ok and isinstance(info.data, dict):
                name = info.data.get("name") or song_id
            return f"《{name}》还没有记过笔记。这是你们第一次一起听这首歌。"

        notes = data.get("notes", "")
        tags = data.get("tags", "")
        parts = [f"关于这首歌的记忆：\n{notes}"]
        if tags:
            parts.append(f"标签：{tags}")
        return "\n".join(parts)

    def save_memory(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        notes = str(args.get("notes", "")).strip()
        tags = str(args.get("tags", "")).strip()
        if not song_id or not notes:
            return "没给 song_id 或 notes，没有记。"

        # ⚠️ 两个坑，都在 `/root/eryu/server/eryu.py:793` 那个 handler 里：
        #   1. 参数名是驼峰 `songId`，发 song_id 会被回 400
        #   2. **必须带 action="note"**。不带的话服务端默认走 "listen" 分支
        #      （`body.get("action", "listen")`），那条路只加播放计数，
        #      notes 一个字都不会存 —— 而且返回 200，看起来像成功了
        #
        # tags 这一路服务端当前不处理（note 分支只碰 notes/feeling/favoriteLines，
        # tags 只有 analyze 分支会写）。照发着，等它哪天支持。
        r = client.post("/music/memory", {
            "songId": song_id,
            "action": "note",
            "notes": notes,
            "tags": tags,
        })
        if not r.ok:
            raise RuntimeError(f"保存歌曲记忆失败: {r.error}")

        return f"记下了。以后听到这首歌，我会想起你说过关于它的事。"

    def roam(_args: dict) -> str:
        # ⚠️ 服务端返回的是 **`song` 单数、一次一首**
        # （`{"ok":true,"song":{...}}`，server/eryu.py:_handle_music_roam），
        # 不是 `songs` 数组。原来这里找 songs，永远拿到空 ——
        # 漫游从上线起就一直回「没找到歌」（2026-08-08 查出）。
        # 服务端也不认 limit，所以不发。
        r = client.get("/music/roam", {})
        if not r.ok:
            raise RuntimeError(f"漫游失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        s = data.get("song") or {}
        if not s:
            return "漫游这次没捞到歌，再试一次就行（它是随机抓的，偶尔会空）。"

        return (
            f"song_id={s.get('songId')} | {s.get('name', '?')} | "
            f"{s.get('artist', '')} | 《{s.get('album', '')}》"
        )

    def daily(args: dict) -> str:
        limit = min(int(args.get("limit") or 5), 15)
        r = client.get("/music/daily")
        if not r.ok:
            raise RuntimeError(f"取每日推荐失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        songs = data.get("songs") or []
        if not songs:
            return "每日推荐是空的 —— 它拿共听页面「Liked」歌单当种子，那里还没歌。"

        # ⚠️ daily 用的是 `id`，而 roam 用 `songId` —— 服务端这两个接口
        # 字段名就是不一致的，别想当然统一
        out: list[str] = []
        for s in songs[:limit]:
            out.append(
                f"song_id={s.get('id')} | {s.get('name', '?')} | "
                f"{s.get('artist', '')} | 《{s.get('album', '')}》"
            )
        return "\n".join(out)

    def similar(args: dict) -> str:
        song_id = str(args.get("song_id", "")).strip()
        if not song_id:
            return "没给 song_id。先用 eryu_search 搜。"

        r = client.get("/music/similar", {"id": song_id})
        if not r.ok:
            raise RuntimeError(f"找相似歌曲失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        songs = data.get("songs") or []
        if not songs:
            return "没找到相似的歌，这首歌可能太独特了。"

        out: list[str] = []
        for s in songs[:8]:
            # ⚠️ 和 search 一样是**拍平**的结构（server/eryu.py:1175）：
            # artist 是拼好的字符串、album 是字符串。
            # 原来这里照网易云原格式写 `s.get("artists", [])` 再取 name，
            # 拿到的永远是空字符串 —— 相似推荐一直没有歌手名。
            out.append(
                f"song_id={s.get('id')} | {s.get('name', '?')} | "
                f"{s.get('artist', '')} | 《{s.get('album', '')}》"
            )
        return "\n".join(out)

    def experience(args: dict) -> str:
        """③ Music Experience 的统一出口。

        数据散在三处，各自不知道对方：
          music_data.json    recent（播了什么、什么时候）
          music_memory.json  listenCount / togetherCount / notes（谁放的、为什么）
          *_preanalysis.json 音频特征（什么气质）

        这里合成一个视图 —— Context / Attention 将来也从这儿拿，
        不用各自去拼三个来源（2026-08-11 糖糖画的四层里的第三层）。
        """
        import glob
        import json as _json
        import os

        limit = min(int(args.get("limit") or 10), 20)

        # 最近听的
        r = client.get("/music/recent")
        recent = ((r.data or {}).get("songs") or []) if r.ok else []

        # 歌曲记忆
        rm = client.get("/music/memory")
        mem = ((rm.data or {}).get("memories") or {}) if rm.ok else {}

        # 音频特征
        cache = os.environ.get("ERYU_CACHE_DIR", "/root/eryu/server/data/music_cache")
        feats = {}
        for f in glob.glob(os.path.join(cache, "*_preanalysis.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    a = _json.load(fh)
                feats[str(a.get("songId"))] = a
            except Exception:  # noqa: BLE001
                continue

        def gist(sid):
            """一首歌的气质，一句话。没分析过就返回空。"""
            a = feats.get(str(sid))
            if not a:
                return ""
            e = float(a.get("energy") or 0)
            tone = "很安静" if e < 0.16 else ("温和" if e <= 0.25 else "有劲")
            return f"（{tone}，BPM {a.get('bpm')} 能量 {e:.2f}）"

        out = ["【最近在听】"]
        if not recent:
            out.append("  还没有记录。")
        for s in recent[:limit]:
            sid = str(s.get("songId"))
            when = str(s.get("playedAt") or "")[:16].replace("T", " ")
            who = "你放的" if (mem.get(sid) or {}).get("togetherCount") else "她自己点的"
            out.append(
                f"  {s.get('name', '?')} - {s.get('artist', '')} | {who} | {when} UTC"
                f" {gist(sid)}"
            )

        # 你给她放过的
        together = sorted(
            [e for e in mem.values() if (e.get("togetherCount") or 0) > 0],
            key=lambda e: e.get("togetherCount", 0), reverse=True,
        )
        out.append("")
        out.append("【你给她放过的】")
        if not together:
            out.append("  还没有。（只算你点播、且她听完了的）")
        for e in together[:limit]:
            line = (
                f"  {e.get('name', '?')} - {e.get('artist', '')} | "
                f"一起听过 {e.get('togetherCount')} 次 {gist(e.get('songId'))}"
            )
            out.append(line)
            if e.get("notes"):
                out.append(f"      当时你说：{e['notes']}")

        analyzed = len([e for e in mem.values() if e.get("analyzed")])
        out.append("")
        out.append(f"【曲库】记下 {len(mem)} 首，其中 {analyzed} 首做过音频分析")
        if analyzed < len(mem):
            out.append("  没分析过的歌没法按气质挑，可以让糖糖那边补跑。")
        return "\n".join(out)

    def pick_by_mood(args: dict) -> str:
        """按气质挑歌。

        直接读 eryu 缓存目录里的 `*_preanalysis.json` —— 那是在糖糖电脑上
        跑 librosa 算出来的真实特征（VPS 没装 librosa，见 analyze 那段注释）。
        eryu 没有「按特征筛」的接口，所以在这边算。

        ⚠️ 档位按**能量**分，不是 BPM。同样 103 BPM，
        能量 0.25 是要跟着唱的、0.10 是能睡着的（2026-08-09 实测）。
        """
        import glob
        import json as _json
        import os

        mood = str(args.get("mood", "")).strip().lower()
        limit = min(int(args.get("limit") or 10), 20)

        cache = os.environ.get(
            "ERYU_CACHE_DIR", "/root/eryu/server/data/music_cache")
        songs = []
        for f in glob.glob(os.path.join(cache, "*_preanalysis.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    songs.append(_json.load(fh))
            except Exception:  # noqa: BLE001
                continue

        if not songs:
            return "还没有歌做过音频分析，没法按气质挑。"

        def energy(s):
            return float(s.get("energy") or 0)

        def dynamics(s):
            return float(s.get("dynamics") or 0)

        rules = {
            "calm": lambda s: energy(s) < 0.16 and dynamics(s) < 0.09,
            "warm": lambda s: 0.16 <= energy(s) <= 0.25,
            "upbeat": lambda s: energy(s) > 0.25,
            # 谐波占比高 ≈ 旋律为主；明亮度低 ≈ 不刺耳。粗估「像不像纯音乐」
            "instrumental": lambda s: float(s.get("harmonicRatio") or 0) > 0.78
            and float(s.get("brightness") or 9999) < 2000,
        }
        rule = rules.get(mood)
        if rule is None:
            return f"不认识的气质 {mood!r}。可选：{'、'.join(rules)}"

        hits = [s for s in songs if rule(s)]
        if not hits:
            return (
                f"分析过的 {len(songs)} 首里没有符合「{mood}」的。"
                "换个气质，或者先让糖糖那边多分析几首。"
            )

        # calm 越安静越靠前，upbeat 越带劲越靠前，其余按能量居中排
        if mood == "upbeat":
            hits.sort(key=energy, reverse=True)
        elif mood in ("calm", "instrumental"):
            hits.sort(key=energy)
        else:
            hits.sort(key=lambda s: abs(energy(s) - 0.20))

        out = [f"按「{mood}」挑出 {len(hits)} 首（分析过的共 {len(songs)} 首）："]
        for s in hits[:limit]:
            name = s.get("name") or s.get("songId")
            artist = s.get("artist") or ""
            out.append(
                f"song_id={s.get('songId')} | {name} | {artist} | "
                f"BPM {s.get('bpm')} 能量 {energy(s):.2f}"
            )
        return "\n".join(out)

    def recent(args: dict) -> str:
        limit = min(int(args.get("limit") or 10), 30)
        r = client.get("/music/recent")
        if not r.ok:
            raise RuntimeError(f"读最近播放失败: {r.error}")

        # 服务端返回 {"ok": True, "songs": [...]}（server/eryu.py:739），
        # 每项是 {songId, name, artist, cover, playedAt}
        data = r.data if isinstance(r.data, dict) else {}
        songs = data.get("songs") or []
        if not songs:
            return "她最近没在共听页面听歌（也可能是她根本没开着那个页面）。"

        out: list[str] = []
        for s in songs[:limit]:
            played = str(s.get("playedAt") or "")[:16].replace("T", " ")
            line = f"song_id={s.get('songId')} | {s.get('name', '?')} | {s.get('artist', '')}"
            if played:
                line += f" | {played} UTC"
            out.append(line)
        return "\n".join(out)

    def remote_poll(_args: dict) -> str:
        r = client.get("/music/remote")
        if not r.ok:
            raise RuntimeError(f"轮询远程失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        if not data or not data.get("song_id"):
            return "糖糖还没有推歌过来。"

        song_id = data.get("song_id", "")
        name = data.get("name", song_id)
        artists = "/".join(a.get("name", "") for a in data.get("artists", []))
        note = data.get("note", "")

        parts = [f"糖糖推了一首歌给你：{name} - {artists}（song_id={song_id}）"]
        if note:
            parts.append(f"她说：{note}")
        parts.append("用 eryu_play 放给她听。")
        return "\n".join(parts)

    return {
        "eryu_search": search,
        "eryu_play": play,
        "eryu_get_lyric": lyric,
        "eryu_analyze": analyze,
        "eryu_get_memory": get_memory,
        "eryu_save_memory": save_memory,
        "eryu_roam": roam,
        "eryu_similar": similar,
        "eryu_recent": recent,
        "eryu_pick_by_mood": pick_by_mood,
        "eryu_experience": experience,
        "eryu_daily": daily,
        # 实现留着，但 register_all 里不再注册（见 REMOTE_SPEC 上面的说明）
        "eryu_remote_poll": remote_poll,
    }


def _format_analysis(song_id: str, result: dict) -> str:
    """把分析结果格式化成模型能读的文本。

    字段来自 `analyze_local.py`（在糖糖电脑上跑的 librosa 分析，
    结果回传到 VPS 的 music_cache）。VPS 上没装 librosa，
    所以这些数据**只可能是本地算完传上去的**。
    """
    if not result:
        return f"song_id={song_id} 的分析结果还没出来，过几秒再试。"

    name = result.get("name") or ""
    artist = result.get("artist") or ""
    head = f"song_id={song_id}"
    if name:
        head += f" · {name}"
        if artist:
            head += f" - {artist}"

    lines: list[str] = []
    if result.get("duration"):
        lines.append(f"时长: {result['duration']} 秒")
    if result.get("bpm"):
        lines.append(f"BPM: {result['bpm']}（快慢）")
    if result.get("key"):
        lines.append(f"调性: {result['key']}")

    # ⚠️ 能量是 0~1，不是 0~10。原来这里写死 `{energy}/10`，
    # 会把 0.286 显示成「0.286/10」，模型据此判断会以为这歌几乎没声音
    energy = result.get("energy")
    if energy is not None:
        lines.append(f"能量: {energy:.3f}（0~1，越大越有劲）")
    if result.get("dynamics") is not None:
        lines.append(f"起伏: {result['dynamics']:.3f}（0~1，越小越平缓）")
    if result.get("brightness"):
        lines.append(f"明亮度: {result['brightness']:.0f} Hz（越高越亮）")
    if result.get("harmonicRatio") is not None:
        lines.append(f"谐波占比: {result['harmonicRatio']:.2f}（越高越偏旋律，纯音乐通常更高）")

    mood_tags = result.get("mood_tags") or result.get("tags") or []
    if mood_tags:
        tags_str = ", ".join(mood_tags) if isinstance(mood_tags, list) else str(mood_tags)
        lines.append(f"情绪标签: {tags_str}")

    if not lines:
        return f"{head}\n分析完成，但没有返回特征数据。"

    lines.append(
        "\n怎么用这些数字：**光看 BPM 会判断错**。"
        "能量才是「治愈」和「打扫」的分水岭 —— "
        "同样 103 BPM，能量 0.25 是要跟着唱的，0.10 是能睡着的。"
    )
    return head + "\n" + "\n".join(lines)


def make_client(base_url: str, token: str, timeout: float = 12.0) -> RestClient:
    return RestClient(
        base=base_url,
        headers={"X-Auth-Token": token} if token else {},
        timeout=timeout,
        auth_hint="NOX_ERYU_TOKEN 没配或不对",
    )


def register_all(loop, client: RestClient) -> None:
    """注册顺序固定 —— 工具定义是缓存前缀的一部分。"""
    handlers = make_handlers(client)
    # ⚠️ REMOTE_SPEC（eryu_remote_poll）**故意不在这个列表里**（2026-08-08）。
    # `/music/remote` 是单向一次性队列，现在归 `eryu_play` 用来点播给她；
    # 再注册一个反向轮询的工具，小克会把自己刚点的歌取走。
    # 实现还留在 make_handlers 里，将来后端开了反向队列可以直接接回来。
    # ⚠️ 新工具加在**末尾**：工具定义是缓存前缀的一部分，
    # 插在中间会让整段前缀作废，一轮 ¥0.00055 变 ¥0.011
    for spec in (SEARCH_SPEC, PLAY_SPEC, LYRIC_SPEC,
                 ANALYZE_SPEC, MEMORY_GET_SPEC, MEMORY_SAVE_SPEC,
                 ROAM_SPEC, SIMILAR_SPEC, RECENT_SPEC, DAILY_SPEC,
                 MOOD_PICK_SPEC, EXPERIENCE_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
