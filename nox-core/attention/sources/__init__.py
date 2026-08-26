"""Experience Sources —— 「发生了什么」。

每个 Source 负责一件事：把某个数据源的原始信号，压成**有意义的变化**，
产出统一的 `ExperienceEvent`。

## Source 不许碰 Registry

判断「这值不值得关心、算 concern 还是 focus」是 Attention Evaluator 的事。
Source 只说「发生了什么」，不说「这有多重要」。

这条边界要守住 —— 一旦 Source 自己开始写 Registry，同一件事在两个 Source
眼里给出矛盾判断时，就没有地方能仲裁了。
（架构设计第十五节列的风险之一。）

## 每个 Source 都要有自己的 Temporal Filter

高频信号直接进 Registry 会把它刷爆。但压缩策略是**按数据源定制**的，
不是统一降采样：睡眠看状态变化，位置看地理围栏，心率看滑动平均。

当前只有 `sleep`。其余等观察期结束后按价值排（v1.3 第 14.4 节）。
"""

from attention.sources.sleep import SleepSource, classify

__all__ = ["SleepSource", "classify"]
