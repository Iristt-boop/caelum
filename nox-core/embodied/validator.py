"""行动闸门 —— 当前世界状态下，这个行动成立吗。

确定性代码，不走 LLM。「32°C 开电热毯」不该靠模型自觉，该被这里拦。

规则链顺序固定（见 README 第 3 节）：
    1. forbidden_domain      2. not_in_model
    3. unknown_prerequisite  4. environment_conflict
    5. quiet_hours（只警告）  6. allow + facts

🔴 诚实的不确定贯穿每一处：env_temp 拿不到就 unknown，
规则 4 **不触发**（不假装知道）；requires 证据只认「她说过的」和
「世界里的确定值」，两者都没有就是 unknown。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from embodied.device_model import (
    DEVICE_MODEL,
    FORBIDDEN_PREFIXES,
    READY_EVIDENCE,
)
from embodied.device_model import QUIET_HOURS as QUIET_HOUR_RANGE
from embodied.result import (
    ALLOW,
    ENVIRONMENT_CONFLICT,
    FORBIDDEN_DOMAIN,
    NOT_IN_MODEL,
    QUIET_HOURS,
    UNKNOWN_PREREQUISITE,
    GateResult,
)


def world_snapshot(world_ref: Any = None) -> dict[str, Any]:
    """从 World Model 拼闸门要用的轻量快照。

    🔴 每一项拿不到就是 None（unknown）—— **不猜、不用默认值顶**
    （presence 用默认"在家"会让他半夜放心开灯；温度用默认 25 会
    放过夏天开电热毯）。unknown 的语义由规则自己决定怎么保守。
    """
    snap: dict[str, Any] = {"env_temp": None, "presence": None}
    w = None
    try:
        w = world_ref() if callable(world_ref) else world_ref
    except Exception:  # noqa: BLE001
        w = None
    if w is None:
        return snap
    try:
        st = w.get_state("weather")
        if st is not None and getattr(st, "status", "") == "fresh":
            value = st.value or {}
            for key in ("temperature", "temp", "temp_c", "current_temp"):
                v = value.get(key)
                if isinstance(v, (int, float)):
                    snap["env_temp"] = float(v)
                    break
    except Exception:  # noqa: BLE001
        pass
    try:
        st = w.get_state("home")
        if st is not None and getattr(st, "status", "") == "fresh":
            value = st.value or {}
            p = value.get("presence") or value.get("person")
            if p in ("home", "away", "not_home"):
                snap["presence"] = "away" if p == "not_home" else p
    except Exception:  # noqa: BLE001
        pass
    return snap


def _ready_evidence(user_text: str, item: str) -> bool:
    """她的原话里有没有这一项的就绪表述。**她说过的就是证据。**

    两层：先是窄正则（「加了水」「打蛋」这类动词在前）；不中再按子句兜底
    —— 「蛋和水都放好了」是名词在前、动词在后，窄正则匹配不到。
    子句判据 = 该句提到这项 + 句里有就绪动词/完成词。
    """
    if not user_text:
        return False
    pattern = READY_EVIDENCE.get(item)
    if pattern and re.search(pattern, user_text):
        return True
    for clause in re.split(r"[。！？\n，,]", user_text):
        if item in clause and re.search(
                r"放|加|倒|灌|打|备|就绪|准备|好[了啦]?$|好了", clause):
            return True
    return False


def gate(entity_id: str, action: str, world: dict[str, Any],
         user_text: str = "") -> GateResult:
    """行动闸门。`action`：on / off / 或 climate 的 mode|温度描述。

    DENY 时返回的 human 写**事实和方向**，不写台词 —— 话是 Nox 的。
    `action="off"` 视为收手，绝大多数检查不适用（关东西不闯祸）。
    """
    entity_id = (entity_id or "").strip()
    world = world or {}
    env_temp = world.get("env_temp")
    facts = {"entity": entity_id, "action": action,
             "env_temp": env_temp, "presence": world.get("presence")}

    # ---- 1. forbidden_domain（排期 3.5：automation/sensor 等不许碰）----
    if entity_id.startswith(FORBIDDEN_PREFIXES):
        return GateResult(
            allow=False, reason=FORBIDDEN_DOMAIN, facts=facts,
            human=(f"{entity_id} 不是可控设备（它是自动化/传感器/人员类实体），"
                   "我没有动它。"))

    # ---- 2. not_in_model：没有语义档案的设备，降权限 ----
    model = DEVICE_MODEL.get(entity_id)
    if model is None:
        if action == "off":
            # 关一个陌生设备是收手，放行
            return GateResult(allow=True, reason=NOT_IN_MODEL, facts=facts)
        return GateResult(
            allow=False, reason=NOT_IN_MODEL, facts=facts,
            human=(f"{entity_id or '这个实体'} 我还没有它的语义档案——"
                   "它是什么设备、动了会有什么后果，我都不知道，所以先没碰。"
                   "你可以告诉我它是什么，我补上档案。"),
            ask=f"{entity_id} 是什么设备？",
        )

    # ---- off 一律放行（收手不闯祸；quiet 也不警告 —— 关灯是好事）----
    if action == "off":
        return GateResult(allow=True, facts=facts)

    # ---- 3. unknown_prerequisite（蒸蛋器：requires 项 unknown → ask）----
    requires = model.get("requires") or []
    missing = [item for item in requires
               if not _ready_evidence(user_text, item)]
    # World 快照里如果哪天有了确定值（传感器到位），在这里豁免：
    # for item in requires: if world.get(f"ready_{item}") is True: missing.remove(item)
    if missing:
        need = "、".join(missing)
        return GateResult(
            allow=False, reason=UNKNOWN_PREREQUISITE, facts=facts,
            human=(f"{model.get('name', entity_id)} 还不能启动——"
                   f"我确认不了{need}是否已就绪（这东西没有传感器，我看不到）。"),
            ask=f"{model.get('name', entity_id)}里的{need}都准备好了吗？"
                f"好了我就开。",
        )

    # ---- 4. environment_conflict（电热毯：env_temp 已知且超限）----
    deny_when = model.get("deny_when") or {}
    threshold = deny_when.get("env_temp_above")
    if threshold is not None and env_temp is not None and env_temp > threshold:
        return GateResult(
            allow=False, reason=ENVIRONMENT_CONFLICT, facts=facts,
            human=(f"现在环境温度 {env_temp:g}°C，{model.get('name', entity_id)}"
                   f"开着只会更热——这个动作和当下条件是冲突的。"),
            alternative=model.get("alternative", ""),
        )

    # ---- 5. quiet_hours：只警告，不拦 ----
    warning = ""
    if model.get("quiet_hours"):
        hour = datetime.now().hour
        if QUIET_HOUR_RANGE[0] <= hour < QUIET_HOUR_RANGE[1]:
            warning = (f"（现在是凌晨 {hour:02d} 点，如果她刚醒需要灯/电视，"
                       "亮度和小声是对的。）")

    return GateResult(allow=True, facts=facts,
                      warning=warning, reason=ALLOW)
