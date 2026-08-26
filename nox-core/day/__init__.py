"""「Nox 的一天」—— 当日活动的只读投影（Read Model / Projection）。

**不是独立业务模块，不新增事实存储。** 设计见
`Nox 的一天 架构设计文档.md` v1.1。
"""

from day.aggregator import SOURCES, build_day

__all__ = ["build_day", "SOURCES"]
