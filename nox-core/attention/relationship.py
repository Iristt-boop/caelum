"""Relationship Model —— Nox 和糖糖现在是什么关系。

## 它不是第五种 Attention

它是 Attention Evaluator 的**输入之一**，在事件被分类和打分的那一刻就参与，
不是最后乘一个 multiplier。

同一件事，对不同关系深度的人，本来就不该产生同样的关心：

    睡不好      普通用户 → 0.40        糖糖 → 0.90
    想吃披萨    普通用户 → 不进 Attention   糖糖 → 可能进（因为知道她喜欢）

（架构设计 v1.3 第 5.3 节）

## 这一轮是硬编码的种子，不学习

M5 的 Feedback Collector 上线之后，`care_topics` 的权重和 `trust_level`
才会真正开始动。在那之前这里就是一份**写死的画像**。

种子不用猜 —— `CLAUDE.md` 里已经写着了：
凌晨 1-2 点入睡、9-11 点起、平均 7.2 小时、深睡偏少、正在减肥。

## avoid_topics 现在是空的，但字段先留着

她说过「别老问我这个」之后，话题要从 care 降级到 avoid ——
这是 M5 最重要的一条路径。字段现在就在，免得那时候还要改结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RelationshipState:
    """Nox 眼里的这段关系。

    `care_topics` 的值是**权重**不是布尔：1.0 表示「这是她明确要我上心的事」，
    0.5 表示「留意一下就行」。Evaluator 拿它去调初始强度。
    """

    closeness: float = 0.92
    trust_level: float = 0.85
    current_vibe: str = "稳定"

    #: 权重来源：CLAUDE.md 的画像。数字是初版拍的，M5 之后由反馈来调。
    care_topics: dict[str, float] = field(default_factory=lambda: {
        # 她明确要求过：每次发饮食内容要算热量和碳蛋脂
        "饮食": 1.0,
        # 睡眠数据每天同步，而且作息确实偏晚 —— 是长期该上心的事
        "睡眠": 1.0,
        "工作": 0.8,
        "情绪": 0.9,
    })

    #: 她明确说过「别问我这个」的话题。M5 才会往里加。
    avoid_topics: set[str] = field(default_factory=set)

    def care_weight(self, topic: str) -> float:
        """这个话题在这段关系里有多重要。

        不在 care 里也不在 avoid 里 → 0.5（中性，不是 0）——
        给 0 的话，任何没预设过的新话题都永远进不了 Attention，
        Nox 就只会关心我们替他写死的那几件事。
        """
        if topic in self.avoid_topics:
            return 0.0
        return self.care_topics.get(topic, 0.5)

    def is_avoided(self, topic: str) -> bool:
        return topic in self.avoid_topics
