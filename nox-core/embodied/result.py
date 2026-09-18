"""闸门结果 —— DENY 必须带「原因 + 替代方向」，话术归 Nox。

Validator 只给机器可读的 reason / alternative 和事实；**不写给人听的话**。
回到 LLM 重组：「我本来想帮你暖一下，但现在室温 32 度……我把空调调舒服点？」
代码不定话术、给 LLM 留出路 —— 同 `_THINK` 的 `[SKIP]`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: reason 的全部取值。稳定不要改 —— 日志和测试都按它对账
ALLOW = "allow"
FORBIDDEN_DOMAIN = "forbidden_domain"        # automation./sensor. 等不该碰的域（排期 3.5）
NOT_IN_MODEL = "not_in_model"                # 没有语义档案的设备 → 降权限
UNKNOWN_PREREQUISITE = "unknown_prerequisite"  # requires 项 unknown 且无话语证据（蒸蛋器）
ENVIRONMENT_CONFLICT = "environment_conflict"  # 32°C 开电热毯
QUIET_HOURS = "quiet_hours"                  # 深夜开灯/电视 —— 只警告不拦


@dataclass
class GateResult:
    allow: bool
    reason: str = ALLOW
    #: 给 Nox 看的人话（DENY 时必填）。他读完后自己组织给她的话，
    #: 所以这里**写事实和方向，不写台词**：「当前 32°C，电热毯只会更热，
    #: 空调可以制冷」——不说「你该说……」
    human: str = ""
    #: unknown 时问她的话。Nox 原样转述即可（「蒸蛋器里加水放蛋了吗？」）
    ask: str = ""
    #: 机器可读的替代方向（cooling_possible / heating_possible / ...）
    alternative: str = ""
    #: ALLOW 但有提醒（quiet_hours）：附在工具输出尾部，他有 Gateway 自我纠正
    warning: str = ""
    #: 当时用的世界快照（unknown 的项标 None）—— 排障和测试都靠它
    facts: dict[str, Any] = field(default_factory=dict)
