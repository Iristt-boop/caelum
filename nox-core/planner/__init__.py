"""Daily Planner —— 把「今天」聚合成一份简报。

`daily.py` 只负责取数和聚合，不调 LLM。
「一句话概览」由谁来说分两种场景：

  她主动问     模型已经在对话里，`daily_summary` 工具把数据递过去，他自己说
  定时推送     没有对话，由 api 层起一轮生成，再走 bridge 推到她锁屏
"""

from planner.daily import DAILY_BUDGET, DEFAULT_SECTIONS, DailyBrief, build_brief

__all__ = ["DailyBrief", "build_brief", "DEFAULT_SECTIONS", "DAILY_BUDGET"]
