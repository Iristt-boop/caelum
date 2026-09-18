"""Embodied —— 行动闸门（Action Gate）v0.x。

**把不可靠的智能限制在它擅长的地方，把确定性问题交给确定系统。**
LLM 管意图和创造性；日期归 Temporal，状态归 Registry，现实归这里的 Validator。

设计文档与两个真实事故的复盘见本目录 README.md。三不做：
不做 Planner（LLM 就是）、不做新 World State（事实进 world_model/）、
不做设备模型外置（十个设备硬编码，验证思想优先）。
"""

from embodied.result import GateResult
from embodied.validator import gate, world_snapshot

__all__ = ["GateResult", "gate", "world_snapshot"]
