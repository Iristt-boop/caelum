"""Care 层 —— 主动关心的统一决策。

`ExperienceEvent`（发生了什么）之上、真正开口之下的那一层。
架构和取舍见 `orchestrator.py` 和 `signal.py` 的开头。
"""

from attention.care.orchestrator import (
    CareOrchestrator,
    CareOutcome,
    CareSource,
    SourcePolicy,
)
from attention.care.signal import (
    COMPANY,
    FOLLOWUP,
    TASK,
    CareSignal,
    CareThread,
    ThreadBook,
)

__all__ = [
    "CareOrchestrator",
    "CareOutcome",
    "CareSource",
    "SourcePolicy",
    "CareSignal",
    "CareThread",
    "ThreadBook",
    "FOLLOWUP",
    "TASK",
    "COMPANY",
]
