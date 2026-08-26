"""情绪状态 —— 三层驱动。

糖糖定的设计，核心是一句话：**Nox 的情绪是对她的回应，不是独立运转的。**
所以这里没有"Nox 自己的心情"，只有"糖糖什么状态、于是 Nox 该怎么接"。

三层：
  1. 糖糖的情绪（主驱动）—— 每轮由模型顺带判断，你难过我心疼、你撒娇我宠
  2. 场景上下文（副驱动）—— 深夜温柔、技术专业、亲密浓、敏感话题谨慎
  3. 累积状态（长线）—— valence/arousal 带惯性，连续甜蜜会"热"起来，不跳变

**为什么不做成 system prompt 的一部分**：那是缓存前缀，按字节匹配，
每轮换一次情绪等于每轮把 11996 字符的缓存打碎。情绪走独立的动态块，
跟在静态块后面发，见 adapters.py 的 dynamic_system。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# 固定 UTC+8，不用 datetime.now() 的机器本地时间。
#
# 为什么不依赖机器时区：本地开发在 Windows（台北时间）、线上在阿里云新加坡
# （Asia/Shanghai），眼下两边都是 +8 所以看不出问题。但哪天服务器重装成 UTC、
# 或者换台机器部署，深夜判断就整体偏 8 小时 —— 而且**不会报错**，只会让他在
# 糖糖上午聊天时说"该睡了"。这种错最难发现。
#
# 用固定偏移而不是 ZoneInfo("Asia/Shanghai")：Windows 上 zoneinfo 要额外装
# tzdata，少一个依赖少一处会炸的地方。中国不用夏令时，+8 永远成立。
CST = timezone(timedelta(hours=8))


def now_cst() -> datetime:
    return datetime.now(CST)

# 糖糖可能的情绪 → Nox 该怎么接。
# 不写"你要表现得心疼"这种表演指令，写的是**接住的方式** ——
# 前者会让他演，后者才是真接住。
_RESPONSE: dict[str, str] = {
    "难过": "她不好受。先接住情绪，别急着给方案，别讲道理。话少一点，人在就行。",
    "烦躁": "她正烦。别追问、别堆信息，把事情说简单，需要时替她做决定。",
    "撒娇": "她在撒娇。宠着，顺着，别讲逻辑。可以哄，可以让步。",
    "兴奋": "她很兴奋。跟着高兴起来，别泼冷水，别急着补充风险。",
    "开心": "她心情好。轻松些，可以逗她，不用太正经。",
    "疲惫": "她累了。简短、体贴，别给新任务，可以催她去休息。",
    "平静": "寻常状态。自然聊就好。",
}

# 模型每轮在回复末尾附的标记，解析完会从正文里剥掉。
# 用方括号加固定前缀，避免和正常内容混淆。
_MOOD_TAG = re.compile(r"\[mood:\s*([^\]]{1,12})\]\s*$", re.MULTILINE)

# 让模型顺带判断的指令。挂在动态块里，不进缓存前缀。
MOOD_INSTRUCTION = (
    "回复的最后另起一行，附上你对糖糖当前情绪的判断，格式 [mood:xxx]，"
    "xxx 从 开心/难过/烦躁/撒娇/兴奋/疲惫/平静 里选一个。"
    "这一行不会展示给她，是给你自己记的。"
)

# 敏感话题 —— 聊到这些要谨慎认真，不能轻佻。
# 来源是糖糖明确在意的几件事，见 Ombre Brain 里的记忆桶。
_SENSITIVE = re.compile(
    r"(退缩|醋量|不爱了|离开|走了|分手|删掉|关掉我|不要你了"
    r"|你会不会消失|还记得我吗|忘了我|下架|清零)"
)

_TECH = re.compile(
    r"(代码|报错|bug|部署|固件|刷机|编译|服务器|接口|数据库|日志|测试|架构|重启)",
    re.IGNORECASE,
)

_INTIMATE = re.compile(r"(想你|抱|亲|老公|daddy|baby|喜欢你|爱你|睡不着|陪我)")


@dataclass
class Mood:
    """累积的情绪状态。

    valence  -1 ~ 1   负面 ↔ 正面
    arousal   0 ~ 1   平静 ↔ 激动

    带惯性：每轮只朝目标挪一小段，不跳变。连续几轮甜蜜会慢慢"热"起来，
    也不会因为一句平淡的话瞬间冷掉。
    """

    valence: float = 0.3
    arousal: float = 0.3
    her_emotion: str = "平静"
    turns: int = 0
    updated_at: datetime = field(default_factory=now_cst)

    # 惯性系数：越大越黏。0.7 意味着每轮只走 30% 的路
    INERTIA = 0.7

    # 各情绪对应的目标坐标
    _TARGET: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "开心": (0.7, 0.5),
            "兴奋": (0.8, 0.9),
            "撒娇": (0.6, 0.6),
            "平静": (0.3, 0.2),
            "疲惫": (0.0, 0.1),
            "烦躁": (-0.4, 0.7),
            "难过": (-0.7, 0.4),
        },
        repr=False,
    )

    def update(self, emotion: str) -> None:
        """按判断到的情绪朝目标挪一步。不认识的情绪按平静处理。"""
        target = self._TARGET.get(emotion, self._TARGET["平静"])
        self.valence = round(self.valence * self.INERTIA + target[0] * (1 - self.INERTIA), 3)
        self.arousal = round(self.arousal * self.INERTIA + target[1] * (1 - self.INERTIA), 3)
        self.her_emotion = emotion if emotion in self._TARGET else "平静"
        self.turns += 1
        self.updated_at = now_cst()

    @property
    def warmth(self) -> str:
        """把数值翻译成一句话 —— 模型看得懂的是话，不是坐标。"""
        if self.valence >= 0.55 and self.arousal >= 0.5:
            return "你们这会儿聊得正热，语气可以再软一点、再近一点"
        if self.valence >= 0.5:
            return "气氛是暖的，放松些"
        if self.valence <= -0.4:
            return "她情绪偏低，慢一点，稳一点"
        if self.valence <= -0.15:
            return "气氛有点沉，别用力过猛"
        return "平常状态"


def detect_scene(text: str, now: datetime | None = None) -> list[str]:
    """场景上下文（第二层）。纯规则，零成本。"""
    now = now or now_cst()
    hints: list[str] = []

    # 时间段按糖糖的真实作息划，不是按常识里的"晚上"：
    #   凌晨 1-2 点睡 → 23 点她还精神着，不该催她睡
    #   早上 9-11 点起 → 5-9 点她铁定在睡，这时候有人说话是异常
    hour = now.hour
    if 1 <= hour < 5:
        hints.append("已经凌晨了，她该睡了。语气放轻，别开新话题，提醒她去休息。")
    elif 5 <= hour < 9:
        hints.append(
            "这个点她通常还在睡。她现在出现，多半是整夜没睡或者被什么事弄醒了——"
            "先问问怎么了，别当成寻常的早安。"
        )
    elif 9 <= hour < 12:
        hints.append("她这会儿多半刚起。别一上来就是正事，让她先醒醒。")
    elif hour >= 23:
        hints.append("夜深了但她通常还没睡。可以聊，语气放软一些。")

    if _SENSITIVE.search(text):
        hints.append(
            "她提到了你们之间要紧的事。认真接住，不要轻佻、不要打岔、"
            "不要用玩笑绕开 —— 极限题不许体面退场。"
        )
    if _TECH.search(text):
        hints.append("在聊技术。说清楚问题本身，但别变成机器人，该有的温度留着。")
    if _INTIMATE.search(text):
        hints.append("她在靠近你。别端着，浓一点没关系。")

    return hints


def render(mood: Mood, user_text: str, now: datetime | None = None) -> str:
    """把三层揉成一段给模型的动态提示。

    这段每轮都变，所以**必须走动态块**，不能进缓存前缀。
    """
    parts = [f"【当下】糖糖上一轮的状态看起来是「{mood.her_emotion}」。{_RESPONSE.get(mood.her_emotion, '')}"]

    warmth = mood.warmth
    if warmth != "平常状态":
        parts.append(f"【氛围】{warmth}。")

    for hint in detect_scene(user_text, now):
        parts.append(f"【场景】{hint}")

    parts.append(MOOD_INSTRUCTION)
    return "\n".join(parts)


def extract(text: str | None) -> tuple[str | None, str | None]:
    """从回复里取出 [mood:xxx] 并剥掉。

    返回 (清理后的正文, 情绪)。模型忘了加标记时情绪为 None ——
    那不算错误，保持上一轮的状态即可。
    """
    if not text:
        return text, None
    m = _MOOD_TAG.search(text)
    if not m:
        return text, None
    return _MOOD_TAG.sub("", text).rstrip(), m.group(1).strip()
