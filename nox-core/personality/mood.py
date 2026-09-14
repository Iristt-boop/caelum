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

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.llm import HER_EMOTIONS

logger = logging.getLogger(__name__)

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


# 累积情绪的默认坐标。**下面 Mood 的字段默认值必须和这两个常量一致** ——
# `restore()` 的衰减目标就是它们，两处对不上会让"衰减到底"停在一个
# 谁都没定义过的地方，而且不会报错。
DEFAULT_VALENCE = 0.3
DEFAULT_AROUSAL = 0.3

#: 半衰期：离开这么久，累积情绪走一半的路回默认坐标（糖糖 2026-09-14 定）。
#:
#: 为什么要衰减而不是"一律恢复"或"一律清零"：
#: 原来的设计是重启即清零，注释写着"隔了一天再开口，本来也该是平常状态"——
#: 理由本身对，但它**默认了「进程重启 ≈ 隔了很久」**。而 2026-09-13 一天之内
#: nox-core 重启了 8 次，全是发版本造成的，间隔几十秒。她那头一句话还没说完，
#: 他就忽然变客气了（惯性 0.7，热起来要 5~8 轮，那几轮全白攒）。
#:
#: 所以判断的不是"要不要留"，而是"隔多久之后它就不该留了"。
#: 用连续衰减而不是硬阈值，是为了和这个模块本来的原则一致：**带惯性，不跳变**。
#: 详见 `docs/RESTART-STATE.md`。
HALF_LIFE = timedelta(hours=2)

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
# ⚠️ IGNORECASE：模型偶尔写 [Mood:平静]（大写 M），生产库漏过 3 次
# （2026-09-06 查证）—— 不剥的话她屏幕上就是一行裸文字。
_MOOD_TAG = re.compile(r"\[mood:\s*([^\]]{1,12})\]\s*$", re.MULTILINE | re.IGNORECASE)

# 二级兜底：模型偶尔连方括号都不写，直接一行「mood: 撒娇」
# （2026-09-06 截图实锤）。只认她的七个情绪词、只认整行 —— 零误伤。
_MOOD_LINE = re.compile(
    r"^[ \t]*mood\s*[:：][ \t]*(" + "|".join(HER_EMOTIONS) + r")[ \t。\.]*$",
    re.MULTILINE | re.IGNORECASE,
)

# 三级兜底：标记写在**任意位置**（行首还跟着正文）——主动消息实锤
# （2026-09-07：「[mood:开心]18:34了…」，上面两个 pattern 都够不着，
# 她锁屏上就是裸的）。不限行尾、词不限七个，带前导空白一起吃。
_MOOD_TAG_ANY = re.compile(r"\s*\[mood:\s*([^\]]{1,12})\]", re.IGNORECASE)

# 让模型顺带判断的指令。挂在动态块里，不进缓存前缀。
MOOD_INSTRUCTION = (
    "回复的最后另起一行，附上你对糖糖当前情绪的判断，格式 [mood:xxx]，"
    "xxx 从 开心/难过/烦躁/撒娇/兴奋/疲惫/平静 里选一个。"
    "必须带方括号、写成完整的 [mood:xxx] —— 写成 mood: xxx 她就会看到。"
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

    valence: float = DEFAULT_VALENCE
    arousal: float = DEFAULT_AROUSAL
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

    # ------------------------------------------------------------ 落盘

    def to_dict(self) -> dict[str, Any]:
        """序列化。`updated_at` 是**恢复时算衰减的唯一依据**，不能省。"""
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "her_emotion": self.her_emotion,
            "turns": self.turns,
            "updated_at": self.updated_at.isoformat(),
        }

    def restore(self, d: dict[str, Any] | None, now: datetime | None = None) -> bool:
        """把落盘的状态按离开的时长衰减之后**就地**写回。恢复了返回 True。

        ⚠️ **必须就地改字段，不许新建 Mood 对象替换。**
        `nox.py` 是 `MoodProvider(self.mood)` 这么注册的 —— Provider 手里攥的是
        引用，在 `__init__` 那一刻就绑死了。换对象的话 Provider 会继续读旧的那个，
        **而且不报错**：他的上下文里永远是默认情绪，日志干干净净。
        这正是 `docs/RESTART-STATE.md` 第四节说的"失败形状是什么都没发生"。

        **这个方法不抛异常。** 存档坏了最坏的后果是这次回到平常状态，
        比起因此起不来要轻得多（同 `attention/store.py:260` 的取舍）。

        能挡什么：部署造成的几十秒重启把攒了几轮的温度清零。
        不能挡什么：进程被 kill -9 时**最后一轮**还没落盘的那一次更新
        （落盘在 update 之后同步做，窗口只有几毫秒，不值得上 WAL）。
        """
        if not d:
            return False
        now = now or now_cst()
        try:
            saved_at = datetime.fromisoformat(str(d["updated_at"]))
            valence = float(d["valence"])
            arousal = float(d["arousal"])
        except (KeyError, TypeError, ValueError):
            logger.exception("情绪存档读不出来，这次按平常状态开始：%r", d)
            return False

        if saved_at.tzinfo is None:
            # 历史存档没带时区的兜底。按 CST 解释 —— 写它的进程就在 +8
            saved_at = saved_at.replace(tzinfo=CST)

        # 时钟回拨（NTP 校时、换机器）会让 elapsed 变负，clamp 到 0 当"刚刚"。
        # 不 clamp 的话 2**正数 会把情绪放大到坐标系外面去
        elapsed = max((now - saved_at).total_seconds(), 0.0)
        decay = 0.5 ** (elapsed / HALF_LIFE.total_seconds())

        self.valence = round(DEFAULT_VALENCE + (valence - DEFAULT_VALENCE) * decay, 3)
        self.arousal = round(DEFAULT_AROUSAL + (arousal - DEFAULT_AROUSAL) * decay, 3)

        # her_emotion 和 turns 是离散的，没法"衰减一半"。
        # 挂在同一个半衰期上：离开不到一个半衰期才接着算上一轮的状态，
        # 超过了就当这是新的开始 —— 和上面的连续衰减说的是同一件事
        fresh = decay >= 0.5
        self.her_emotion = str(d.get("her_emotion") or "平静") if fresh else "平静"
        self.turns = int(d.get("turns") or 0) if fresh else 0
        self.updated_at = now

        logger.info(
            "情绪接回来了：离开 %.0f 秒，衰减 %.2f｜valence %.2f→%.2f｜她上一轮 %s",
            elapsed, decay, valence, self.valence, self.her_emotion,
        )
        return True

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
    """从回复里取出情绪标记并剥掉。

    认三种：[mood:xxx] 在行尾（标准）、整行「mood: xxx」（不写方括号）、
    [mood:xxx] 在**任意位置**（行首跟着正文 —— 主动消息 2026-09-07 实锤：
    「[mood:开心]18:34了…」，speaker 走非流式没有过滤器，只能靠这里兜）。

    返回 (清理后的正文, 情绪)。模型忘了加标记时情绪为 None ——
    那不算错误，保持上一轮的状态即可。
    """
    if not text:
        return text, None
    m = _MOOD_TAG.search(text)
    if m:
        return _MOOD_TAG.sub("", text).rstrip(), m.group(1).strip()
    m2 = _MOOD_LINE.search(text)
    if m2:
        return _MOOD_LINE.sub("", text).rstrip(), m2.group(1).strip()
    m3 = _MOOD_TAG_ANY.search(text)
    if m3:
        return _MOOD_TAG_ANY.sub("", text).strip(), m3.group(1).strip()
    return text, None
