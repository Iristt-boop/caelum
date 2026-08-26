# -*- coding: utf-8 -*-
"""共影本地控制器 —— 「这一秒要不要说话」。

## 为什么在这儿判，不去问 Core（架构第六节）

> 共影是连续、强时序、强沉浸的实时场景。触发器与抑制器属于共影本地控制器：
> 它们直接决定此刻是否适合插话，不能依赖全局 Attention/Intent 往返裁决，
> 否则会损害延迟和观影节奏。

所以判断住在这里，Core 只负责**说什么**（拿到证据之后生成那句话）。

## 🔴 运动峰是抑制器，不是触发器

P2 产出两种信号，第一直觉是「都当触发器」。**错的。**

动作戏正是最不该说话的时候 —— 第六节的抑制器写着
`tension_level > 0.8 就闭嘴`，而持续的高帧差就是紧张度的廉价代理。

真正适合开口的是**「气口」**：

    一段安静之后的场景切换   —— 换场了，可以聊两句
    动作结束之后的回落       —— 那口气松下来的时刻
    没有人在说话             —— 压着台词说话是最讨厌的

所以 `motion_peak` 只出现在抑制那一侧。

## 默认是闭嘴

`decide()` 的结构是**一路 skip 到底**，最后才可能 speak。
第六节的原话是「90% 时间陪伴，10% 时间精准点睛」——
所以每一条规则都在找理由**不说**，而不是找理由说。

⚠️ **每个决定都要有理由**（同 Care 的 `CareOutcome`）：
静默丢弃是这套东西最难查的病 —— 「他今天怎么一句话都没说」
和「他判断了 800 次，780 次因为有台词、20 次因为刚说过」是两回事。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 一部片子最多说几次。第十一节：「自然触发 5-15 次点评」
MAX_PER_FILM = 15
#: 两次之间最少隔多久（秒）。**下限**，实际会按片长拉开（见 min_gap_s）
MIN_GAP_S = 150
#: 想让点评摊在整部片子上，而不是前半小时说完。
#: 间隔 = 片长 / (配额 × 这个系数)
SPREAD = 1.2

#: 台词前后这么多秒算「正在说话」。压着台词插嘴是最讨厌的
DIALOGUE_PAD_S = 2.0
#: 往前看这么久判断「是不是在动作戏里」
TENSION_WINDOW_S = 12.0
#: 这段时间里运动峰的归一化强度超过它 → 正紧张着，闭嘴
TENSION_GATE = 0.35
#: 触发点要在「刚过去的这几秒」里找 —— 太旧的切换说起来就不合时宜了
TRIGGER_LOOKBACK_S = 8.0
#: 但也别太新：切换发生的那一瞬间她还在接收画面，等一下下
TRIGGER_MIN_AGE_S = 1.5
#: 场景切换前面要有这么久的相对安静，才算「一个可以聊天的换场」
QUIET_BEFORE_S = 15.0
#: 同一个事件不重复说
DEDUP_MS = 3_000
#: 开场这么久之内不说话。
#:
#: ⚠️ 真数据逼出来的（2026-08-22）：拿一部片子从头 tick 到尾，
#: 他**在第 4 秒就开口了** —— 她刚按下播放键，片头都没过完。
#: 逻辑上没错（前面确实"安静"，因为前面根本没有东西），
#: 但那不是一个可以聊天的换场，那是还没开始。
#:
#: ⚠️ **按片长收缩**（见 `DirectorState.warmup_s`）：90 秒对两小时的电影是 1%，
#: 对一条 4 分钟的 B站视频却是 36% —— 定死的话短视频会被整个吃掉，一句不说。
WARMUP_S = 90.0
#: 热身最多占片长的这个比例
WARMUP_RATIO = 0.10


@dataclass
class DirectorState:
    """一场观影的现场状态。**进程内**，重启就没了 ——
    重来一次最多多说几句，不值得为它落库。"""

    duration_s: float = 0.0
    last_spoke_ms: int = -10 ** 9
    spoke_count: int = 0
    said_events: set = field(default_factory=set)
    #: 她最后一次主动做事（提问 / 暂停）。她在忙自己的事时别插话
    last_user_ms: int = -10 ** 9

    def warmup_s(self) -> float:
        """开场留多久。短片子按比例缩，别把整条视频都算成片头。"""
        if self.duration_s <= 0:
            return WARMUP_S
        return min(WARMUP_S, self.duration_s * WARMUP_RATIO)

    def min_gap_s(self) -> float:
        """按片长把点评摊开。短片子间隔就用下限，长片子拉大。"""
        if self.duration_s <= 0:
            return MIN_GAP_S
        return max(MIN_GAP_S, self.duration_s / (MAX_PER_FILM * SPREAD))


@dataclass
class Decision:
    speak: bool
    reason: str
    #: 说的是哪个时刻的事（毫秒）
    at_ms: int = 0
    #: 触发它的那个事件
    event: dict | None = None
    #: 这一刻前后的台词（给 Core 拼证据用）
    dialogue: str = ""


def _cues_around(subs: list[dict], t_s: float, pad: float) -> list[dict]:
    return [c for c in subs
            if c["start"] - pad <= t_s <= c["end"] + pad]


def _cues_before(subs: list[dict], t_s: float, window: float) -> str:
    """T 之前 window 秒的台词。

    ⚠️ **只取 T 之前**（架构 12.1 的红线）：他随口剧透一句，整部电影就毁了。
    """
    picked = [c["text"] for c in subs
              if c["end"] <= t_s and c["end"] >= t_s - window]
    return " ".join(picked).strip()


def decide(now_ms: int, events: list[dict], subs: list[dict],
           st: DirectorState) -> Decision:
    """要不要在这一刻开口。**默认不说。**

    `events` 是 P2 产出的视觉事件（scene_change / motion_peak），
    `subs` 是字幕条目（秒）。两个都可以是空的 —— 那就基本不会说话，
    这是对的：没有证据就没有值得说的话。
    """
    now_s = now_ms / 1000.0

    # ---- 开场先让她进片子。**第一条**，比配额还靠前 ——
    #      片头那一段"安静"是因为还没开始，不是气口
    warm = st.warmup_s()
    if now_s < warm:
        return Decision(False, f"才开场 {int(now_s)} 秒（片头留 {int(warm)} 秒），让她先进去")

    # ---- 配额：一部片子最多这么多次
    if st.spoke_count >= MAX_PER_FILM:
        return Decision(False, f"这部片已经说了 {st.spoke_count} 次，够了")

    # ---- 冷却：刚说过就别连着说
    gap = st.min_gap_s()
    since = (now_ms - st.last_spoke_ms) / 1000.0
    if since < gap:
        return Decision(False, f"{int(since)} 秒前刚说过（要隔 {int(gap)} 秒）")

    # ---- 她在忙自己的事（刚问过问题 / 刚操作过）
    since_user = (now_ms - st.last_user_ms) / 1000.0
    if since_user < 20:
        return Decision(False, f"她 {int(since_user)} 秒前才刚说过话，让她看")

    # ---- 有人在说台词：压着台词插嘴是最讨厌的
    if _cues_around(subs, now_s, DIALOGUE_PAD_S):
        return Decision(False, "这会儿有台词")

    # ---- 正紧张着：动作戏别打断（motion_peak 在这里是抑制器，不是触发器）
    hot = [e for e in events
           if e["kind"] == "motion_peak"
           and now_ms - TENSION_WINDOW_S * 1000 <= e["at"] <= now_ms
           and e.get("motionScore", 0) >= TENSION_GATE]
    if hot:
        return Decision(False, f"刚才 {len(hot)} 个运动峰，正紧张着")

    # ---- 找触发点：刚过去几秒里的场景切换
    lo = now_ms - TRIGGER_LOOKBACK_S * 1000
    hi = now_ms - TRIGGER_MIN_AGE_S * 1000
    cands = [e for e in events
             if e["kind"] == "scene_change" and lo <= e["at"] <= hi
             and e["at"] not in st.said_events]
    if not cands:
        return Decision(False, "刚才没有值得一提的换场")

    # 强度最高的那个（架构 14.3 的「按重要度选」）
    best = max(cands, key=lambda e: e.get("sceneScore", 0))

    # ---- 换场前面得有一段安静，才算「可以聊天的换场」
    #      动作戏里每隔几秒切一刀，那种不是气口
    qlo = best["at"] - QUIET_BEFORE_S * 1000
    busy = [e for e in events
            if qlo <= e["at"] < best["at"]
            and (e["kind"] == "motion_peak" or e["kind"] == "scene_change")]
    if busy:
        return Decision(False, f"这个换场前面 {int(QUIET_BEFORE_S)} 秒里有 {len(busy)} 个事件，不是气口")

    return Decision(
        True, "一段安静之后换场了",
        at_ms=best["at"], event=best,
        # 证据只取 T 之前的台词。**红线**：不许碰她还没看到的
        dialogue=_cues_before(subs, best["at"] / 1000.0, 45),
    )


def note_spoke(st: DirectorState, d: Decision, now_ms: int) -> None:
    st.last_spoke_ms = now_ms
    st.spoke_count += 1
    if d.event:
        st.said_events.add(d.event["at"])


def note_user(st: DirectorState, now_ms: int) -> None:
    """她说话/操作了。**让她看片** —— 20 秒内不主动插话。"""
    st.last_user_ms = now_ms
