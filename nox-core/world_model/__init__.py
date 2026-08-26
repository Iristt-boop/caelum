"""Nox World Model —— 「这个世界现在是什么样」。

和 Ombre Brain 并列，**互不替代**：

    Ombre Brain   我们经历过什么、这件事对我们的关系意味着什么
    World Model   世界现在是什么样、这个结论是从哪条事实来的

## 为什么需要它（不是为了架构好看）

`attention/sources/sleep.py` 现在是这样的：

    health Provider → sleep.py → ExperienceEvent → Attention
                          ↑
                  判断完就把数据扔了，没人保存

后果很具体：**他答不出「我为什么觉得你睡眠有问题」**，也做不了趋势判断。
`evaluator.py` 只看单次 —— 「昨晚睡得少」和「这周一直在往下掉」
在他眼里是同一件事。

## 三条硬规矩（照架构文档 v1.3 第 4 节）

1. **原始 Observation 永不删除、永不改写。** 派生结论可以重算，事实不能丢。
2. **事实和推断必须分层。** `5h20m` 是观察，`poor` 是判断，
   两者分别存、分别查、分别追溯。不许把前者改写成后者。
3. **来源是强制字段。** 每条 State 都要能顺着 `based_on` 回溯到原始 Observation，
   否则他就答不出「你为什么这样判断」。

## 第一阶段只做三件事

    observe(...)      收下一条事实
    get_state(...)    现在是什么样（带 fresh / stale / unknown）
    query(...)        历史记录，返回带来源的 Evidence

**不做**：知识图谱、自动遗忘、LLM 改事实、常驻同步、Event Bus、
自动归纳全部数据（架构文档第 10 节的「第一阶段不做」清单）。

底层先复用 SQLite。等第二、第三个 Provider 接进来，再看要不要抽象。
"""

from world_model.model import WorldModel
from world_model.types import Evidence, Observation, State

__all__ = ["WorldModel", "Observation", "State", "Evidence"]
