"""MoodProvider —— 框架的第一个真实 Provider。

## 为什么第一个是它

架构文档 0.3：三层情绪每轮渲染一段文本经 `dynamic_system` 注入，
**这本来就是一个 Context Provider，只是还没这么叫**。
拿它当框架的第一个用户，比等 Phase 2 才第一次被使用踏实得多，
也避免出现两套并行的「每轮注入」机制。

## 它是 volatile 的

`volatile = True`，绕开缓存。不是为了实时性，是正确性：
情绪状态和场景判断都跟**当轮说了什么**绑着，缓存住就会把上一轮的判断
用到这一轮 —— 她这句在聊技术，他却还按上一句「她在靠近你」的调子回话。

## 存储仍然在 personality/mood.py

Provider **不持有情绪状态**，只读 `Nox.mood` 那个对象。
写入（`mood.update()`）仍然发生在 `nox.py` 收到模型回复之后。
这和 Memory Provider 的分工是同一个道理（架构文档第十节）：
Context 层只负责「按当前情况取出来并格式化」，不负责存储和更新。

## render() 必须逐字等同于原来的 mood.render()

那是他说话的语气，不是可以顺手优化的文案。
`tests/test_mood_provider.py` 拿 1000 条基线逐字比对
（7 情绪 × 5 时段 × 5 文本类型 × 5 个 warmth 档位）。
"""

from __future__ import annotations

from typing import Any

from context.base import BaseContextProvider, Turn
from personality import mood as mood_mod


class MoodProvider(BaseContextProvider):
    """三层情绪驱动：她的情绪 / 氛围 / 场景。"""

    name = "mood"
    section = "user"          # World State 里挂在 user 下
    volatile = True           # 每轮必新，不缓存

    def __init__(self, mood_state: mood_mod.Mood, **kw: Any) -> None:
        super().__init__(**kw)
        # 引用而不是复制 —— nox.py 那边 mood.update() 之后这里要能看见新值
        self.mood = mood_state

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        m = self.mood
        return {
            "her_emotion": m.her_emotion,
            "response": mood_mod._RESPONSE.get(m.her_emotion, ""),
            "warmth": m.warmth,
            "valence": m.valence,
            "arousal": m.arousal,
            "turns": m.turns,
            # 场景判断必须用**当轮**的文本，这就是它不能缓存的原因
            "scene_hints": mood_mod.detect_scene(turn.text, turn.now),
        }

    def render(self, state: dict[str, Any]) -> str:
        """拼成给他看的一段话。

        ⚠️ 输出必须和 `personality.mood.render()` **逐字相同**。
        这是他跟糖糖说话的语气，改一个标点都要先问她。
        改这个函数前先跑 `tests/test_mood_provider.py`。
        """
        if state.get("available") is False:
            return ""

        parts = [
            f"【当下】糖糖上一轮的状态看起来是「{state['her_emotion']}」。{state['response']}"
        ]

        warmth = state.get("warmth", "")
        if warmth != "平常状态":
            parts.append(f"【氛围】{warmth}。")

        for hint in state.get("scene_hints", []):
            parts.append(f"【场景】{hint}")

        parts.append(mood_mod.MOOD_INSTRUCTION)
        return "\n".join(parts)
