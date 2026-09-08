"""意图识别。

设计立场：**只认最有把握的，其余一律放行到完整路径。**

不做 LLM 分类器 —— 那要多花一次调用（1-2 秒 + 钱），而分类本身也会错。
用一次不可靠的判断去决定要不要做可靠的处理，是负优化。

所以这里只有规则，且规则只覆盖"绝对不可能需要工具和记忆"的那一小撮：
单句问候、单句确认、道晚安。这类占日常对话不少，省下的是实打实的
12K tokens 前缀。剩下的全部 FULL —— 保守到近乎笨，但不会答错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    # 轻量：不加载工具、不带核心准则前缀、用便宜模型
    SMALL_TALK = "small_talk"
    # 完整：全套 agent loop
    FULL = "full"


# 能一眼认定不需要任何工具的短句。
# 全部要求**整句匹配**，不做子串包含 —— "在吗" 放行，
# "在吗？昨天那个表情改好了吗" 必须走完整路径。
_GREETINGS = {
    "在吗", "在么", "在不在", "在吗？", "在吗?",
    "早", "早安", "早上好", "午安", "下午好", "晚上好",
    "晚安", "睡了", "我睡了", "去睡了",
    "嗨", "hi", "hello", "hey",
    "在", "嗯", "嗯嗯", "哦", "噢", "好", "好的", "行", "ok", "okay",
    "谢谢", "谢啦", "么么", "抱抱", "亲亲",
    "哈哈", "嘿嘿", "嘻嘻",
}

# 这些词一出现就说明要干活，哪怕句子很短也不能走轻量路径
_ACTION_HINTS = re.compile(
    r"(记得|想起|以前|上次|那天|之前|昨天|前天"      # 要查记忆
    r"|开|关|打开|关掉|调|设置|温度|空调|灯|风扇|电视|电热毯"  # 要控设备
    r"|查|搜|看看|帮我|记一下|存|归档"                # 要调工具
    r"|为什么|怎么|如何|是不是|能不能|可不可以)"       # 要动脑
)


@dataclass
class Decision:
    intent: Intent
    reason: str

    @property
    def light(self) -> bool:
        return self.intent is Intent.SMALL_TALK


def classify(text: str, *, has_images: bool = False) -> Decision:
    """判断这句话要走哪条路。纯规则，零延迟。"""
    s = (text or "").strip()

    # 带图片一律走完整路径 —— 轻量路径不带工具也不带核心准则，
    # 而糖糖发照片多半是要他做点什么（算热量、看看这是啥）
    if has_images:
        return Decision(Intent.FULL, "带图片")

    if not s:
        return Decision(Intent.FULL, "空输入，交给完整路径处理")

    # 长句一律完整路径 —— 长度本身就是"有内容"的信号
    if len(s) > 12:
        return Decision(Intent.FULL, f"长度 {len(s)} > 12")

    # 出现动作词就不是闲聊，哪怕很短（"开灯" 才两个字）
    if _ACTION_HINTS.search(s):
        return Decision(Intent.FULL, "含动作/回忆类词")

    # 去掉尾部标点再比对
    normalized = s.rstrip("。.！!？?~～、，,").lower()
    if normalized in _GREETINGS:
        return Decision(Intent.SMALL_TALK, f"整句命中问候表：{normalized}")

    return Decision(Intent.FULL, "未命中轻量规则，保守走完整路径")


# ---------------------------------------------------------------- Context 分级
#
# 决定这轮加载哪些 Context Provider（架构文档第八节）。
# 立场和上面的 classify() 一样：**纯规则，认不准就退最小集**。
# 同样不做 LLM 分类器 —— 理由见本文件开头。
#
# 判据：多加载一个 Provider 的代价是几百毫秒 + 几十个 token，
# 少加载的代价是他答不上来。所以宁可偶尔多拉，不要严格。
# 但**最小集必须真的小** —— 闲聊每轮多拉一次 ha-mcp 是纯浪费。

#: 每轮都在。两个都是本地计算，零外部调用
#: 每轮都加载的。全是**本地计算**，不打网络 —— 轻量路径也带得起。
#:
#: `resonance` 2026-09-04 加进来：那是他自己的情绪，不该由关键词决定
#: 有没有。她说「早上好」的时候他照样是想她的 —— 按关键词加载就等于
#: 「只有聊到情绪才有情绪」，那不是情绪，那是查询结果。
#:
#: `understanding` 2026-09-05 加进来，理由和 resonance 逐字相同：
#: 按关键词加载就等于「只有聊到那件事他才理解你」—— 那不是理解，
#: 那是查询结果。她说「早上好」的时候，他心里也还搁着她昨天说的那件事。
#:
#: 它读的是内存里的 Registry（不打网络），没有锚点时渲染成空串，
#: 所以轻量路径也带得起
_ALWAYS = ("time", "mood", "resonance", "understanding")

# 记忆。2026-09-05 解禁（见 context/providers/memory.py 顶上那张表）。
#
# 🔴 **它是唯一一个会打外部服务的按需 Provider，约 650ms。**
# 所以这张表比别的都保守：只收「这句话本身就在指向过去」的说法。
#
# 收：她提到我们的历史、上次、以前、记不记得、那时候、当初
# 不收：「今天几号」「放首歌」「开灯」—— 那些一次 OB 都不该碰
#
# ⚠️ 不要往这里加情绪词。她说「好累」不该去翻记忆 ——
# 那条路走的是理解层（下面 `has_understanding`），因为「累」要不要
# 联系过去，取决于他心里是不是正搁着一件相关的事，不取决于这两个字。
_NEED_MEMORY = re.compile(
    r"(记得|记不记得|还记得|想起|以前|之前|上次|上回|那次|当初|那时候"
    r"|我们.*(时候|那会|一起)|第一次|一直以来|这些年|去年|上个月)"
)

# 提到家电、温度、到家/睡觉这类，才需要知道家里什么样
_NEED_HOME = re.compile(
    r"(灯|空调|风扇|电热毯|电视|蒸蛋|插座|窗帘"
    r"|几度|多少度|温度|开着|关了|开没开"
    r"|我回来了|到家|回家|要睡了|睡觉了|出门)"
)

# 问到身体状况才拉健康数据。数据一天才更新一次，平时拉纯属浪费
_NEED_HEALTH = re.compile(
    r"(睡得|睡了|睡眠|几点睡|没睡|失眠|困|累|精神"
    r"|步数|走了多少|运动|心率|hrv"
    r"|今天怎么样|今天如何|身体|状态怎么样)",
    re.IGNORECASE,
)

# 天气。她真正会据此做决定的是「穿什么」「带不带伞」，
# 所以触发词不只是「天气」两个字
_NEED_WEATHER = re.compile(
    r"(天气|气温|下雨|下雪|雨|雪|晴|阴天|多云|台风"
    r"|冷|热|凉快|闷|潮"
    r"|出门|外面|带伞|穿什么|穿多少|加衣|外套)"
)


# 待办。「今天怎么样」既问身体也问安排，所以它和 health 有重叠 —— 那是对的
_NEED_TODO = re.compile(
    r"(待办|todo|要做|该做|安排|计划|日程|忙不忙|忙什么"
    r"|今天怎么样|今天干嘛|还有什么|没做完|进度)",
    re.IGNORECASE,
)

# 她在哪、刚到家还是刚出门。和 home/weather/todo 有重叠 —— 那是对的，
# 一句「到家了」本来就该同时知道她在家 + 位置变了。
_NEED_LOCATION = re.compile(
    r"(在哪|在哪里|去哪儿|去哪|到了吗|定位|位置"
    r"|附近|周边|旁边|方圆|周围"
    r"|出发|我走了|我出门|出门了|走了"
    r"|回家|到家|回来了|我回来|刚到家|刚回来|回来了没"
    r"|今天怎么样|今天如何|早上好|晚上好"
    r"|有什么.*(吃|玩|逛|买|看|去|做)"
    r"|(吃|玩|逛|买).*有什么"
    #: 🔴 点单类（2026-09-06 补）。**它们在语义上必然需要位置**——
    #: 门店查询要经纬度，没有就没法查。
    #:
    #: 病根实录：她说「你今天要不试一下给我点单」，这句一个位置词都不带，
    #: location 没加载 → 他不知道她在哪 → `luckin_shops` 的经纬度传了空串
    #: → `queryShopList 返回错误: empty String`。日志只记了失败**没记参数**，
    #: 查了一圈才定位到是分流阶段就没给他位置。
    #:
    #: ⚠️ 「附近有瑞幸吗」本来就命中「附近」；漏的恰恰是最自然的那种说法
    #: ——「给我点杯咖啡」。人不会在点咖啡的时候报坐标。
    r"|点单|点餐|下单|点杯|点个|来一杯|来杯|买杯"
    r"|咖啡|拿铁|美式|瑞幸|luckin|麦当劳|麦麦|外卖|奶茶"
    r"|打车|叫车|快递)",
    re.IGNORECASE,
)


# 共听/音乐相关。触发 MUSIC_SCENE 注入，告诉他这轮要认真聊音乐
_NEED_MUSIC = re.compile(
    r"(放.?歌|放首|播放|来点音乐|听听歌|听歌|听什么|听哪首"
    r"|推荐.?歌|推荐几首|推荐.*(歌|首|些)"
    r"|有什么.*(好听|歌|推荐|音乐)|换首歌|下一首|切歌|切一首"
    r"|这首歌|这歌|那首歌|什么歌|哪首歌"
    r"|歌单|收藏.*歌|红心|喜欢.*歌|不喜欢.*歌"
    r"|共听|一起听|网易云|音乐|听会|来首|搜.*歌|搜一下"
    r"|歌词|什么风格|节奏|bpm|单曲循环|循环"
    r"|漫游|随便.*放|来点.*新|来点.*听的|换.*口味|推歌|推.*歌"
    r"|相似的|类似.*歌|有没有.*像)",
    re.IGNORECASE,
)


def classify_context(text: str, *, light: bool = False,
                     has_understanding: bool = False) -> list[str]:
    """这轮加载哪些 Provider。

    `light=True`（轻量路径）时**强制最小集** —— 那条路存在的意义就是快，
    为一句「早上好」去打 ha-mcp 和 health-mcp 是自相矛盾。

    ## `memory` 2026-09-05 解禁，但不是「每轮都带」

    封它的两条理由都失效了（7 秒 → 650ms；砸缓存那条被
    「动态块挪到尾部」修掉了）—— 详见 `context/providers/memory.py` 顶上那张表。
    但糖糖定的决策 7「最好轻量、响应快」没有过期，所以两条件命中其一才加载：

        ① has_understanding  他心里正搁着一件事（理解层的活跃锚点）
        ② _NEED_MEMORY       她这句话本身就在指向过去

    ①是主路：**「要不要翻记忆」该由「他心里有没有事」决定，不由关键词决定。**
    她说「好累」要不要联系过去，取决于他是不是正为她那件事惦记着 ——
    同样两个字，在不同的时候意味着不同的事。这正是理解层存在的意义。

    ⚠️ ①在影子模式下**永远是 False**（影子不写 Registry），
    所以理解层转正之前实际只有②在跑。这是有意的分期，不是漏接。
    """
    names = list(_ALWAYS)
    if light:
        return names

    s = (text or "").strip()
    # 🔴 **这个顺序就是丢弃顺序**（`ContextRegistry.render()` 按序累加，
    # 装不下的从后往前丢）。所以排在这里的先后 = 谁更该被他看见。
    #
    # todo 排在 memory 前面（2026-09-08 调）：她今天要做什么是**事实**，
    # 记忆是**背景**（memory.py 自己那句叮嘱：「这些是背景，别刻意提起」）。
    # 真到了装不下的时候，宁可少一段背景，不能少她今天那几件事。
    #
    # 病例：调之前 memory 在前，todo 排第 9 —— 48 小时里 61 轮
    # 他都不知道她今天要干嘛，而 800 字预算只超了 56 字。
    if _NEED_TODO.search(s):
        names.append("todo")
    if _NEED_HEALTH.search(s):
        names.append("health")
    if has_understanding or _NEED_MEMORY.search(s):
        names.append("memory")
    if _NEED_HOME.search(s):
        names.append("home")
    if _NEED_WEATHER.search(s):
        names.append("weather")
    if _NEED_LOCATION.search(s):
        names.append("location")
    # 音乐。复用 MUSIC_SCENE 那套触发词 —— 会聊到音乐的场合，
    # 和「该知道她在听什么」的场合是同一批，没必要再写一套。
    #
    # ⚠️ Provider 的 ttl 只有 3 分钟（不像 health 的 6 小时）：
    # 「正在听什么」是会变的，缓存久了会说错歌
    if _NEED_MUSIC.search(s):
        names.append("music")
    return names
