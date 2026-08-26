"""各 Context Provider 实现。

顺序按架构文档第十五节的 Phase：
  Phase 1  mood      ← 已有、天天在跑，收编进框架（当前在这）
  Phase 2  time / memory / home
  Phase 3  health（要先把 health-mcp 部署到 VPS，那是部署活不是 Provider 活）
  Phase 4  weather（复用 xiaozhi 那把和风 key）
  Phase 5  activity  ← music 在这（2026-08-11 接的，「她现在在听什么」）
  Phase 6  location（**先定数据来源再动手**，现在全服务器没有任何位置数据源）
"""

from context.providers.health import HealthProvider
from context.providers.home import HomeProvider
from context.providers.location import LocationProvider
from context.providers.memory import MemoryProvider
from context.providers.mood import MoodProvider
from context.providers.music import MusicProvider
from context.providers.time import TimeProvider
from context.providers.todo import TodoProvider
from context.providers.weather import WeatherProvider

__all__ = ["HealthProvider", "HomeProvider", "LocationProvider", "MemoryProvider",
           "MoodProvider", "MusicProvider", "TimeProvider", "TodoProvider",
           "WeatherProvider"]
