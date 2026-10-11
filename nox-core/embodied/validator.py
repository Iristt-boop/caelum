"""行动闸门 —— 当前世界状态下，这个行动成立吗。

确定性代码，不走 LLM。「32°C 开电热毯」不该靠模型自觉，该被这里拦。

规则链顺序固定（见 README 第 3 节）：
    1. forbidden_domain      2. not_in_model
    3. unknown_prerequisite  4. environment_conflict
    5. quiet_hours（只警告）  6. allow + facts

🔴 诚实的不确定贯穿每一处：requires 证据只认「她说过的」和
「世界里的确定值」，两者都没有就是 unknown。

env_temp（2026-10-11 起）：World Model 里一直没有 weather / home 状态 ——
09-18 上线以来 env_temp **永远是 None**，电热毯那条 26°C 规则一次都没生效过，
而且闸门不留日志，没人发现。现在：
  · 温度先看 World Model（以后 embodied 0.2 接温湿度传感器写进去），
    没有就读**户外天气预报**（WeatherProvider，糖糖 10-11：传感器还没买，先用预报顶）
  · 还是拿不到 → 有 deny_when 的设备**先问**，不放行（糖糖 10-11 定）；
    她这句话本身就是在要（「开电热毯」「好冷」「开吧」）→ 放行，她说的就是证据
  · 每次拦截 / 警告 / 先问都写日志
"""

from __future__ import annotations

import logging
import re
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
    UNKNOWN_ENVIRONMENT,
    UNKNOWN_PREREQUISITE,
    GateResult,
)
from temporal import now as now_cst

logger = logging.getLogger(__name__)

#: 天气预报的旧值能用多久。WeatherProvider 失败时退回上一次的值并标 stale ——
#: 三小时内的气温还算数，再旧就当不知道
WEATHER_STALE_MAX_S = 3 * 3600

#: 温度不知道时，她这句话算不算「她在要」。她说的就是证据（同 requires 的话语证据）
ENV_UNKNOWN_EVIDENCE = r"电热毯|冷|暖和|开吧|开一下|要开|帮我开|打开"


def _forecast_temp(weather_ref: Any) -> float | None:
    """户外天气预报的气温。拿不到 / 太旧 → None。"""
    if weather_ref is None:
        return None
    try:
        provider = weather_ref() if callable(weather_ref) else weather_ref
        if provider is None:
            return None
        st = provider.get_state() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("行动闸门：读天气预报失败，温度按不知道算：%s", exc)
        return None
    if st.get("available") is False:
        return None
    if st.get("stale") and float(st.get("stale_age_s") or 0) > WEATHER_STALE_MAX_S:
        return None
    t = st.get("temp")
    return float(t) if isinstance(t, (int, float)) else None


def world_snapshot(world_ref: Any = None, weather_ref: Any = None) -> dict[str, Any]:
    """从 World Model 拼闸门要用的轻量快照。

    🔴 每一项拿不到就是 None（unknown）—— **不猜、不用默认值顶**
    （presence 用默认"在家"会让他半夜放心开灯；温度用默认 25 会
    放过夏天开电热毯）。unknown 的语义由规则自己决定怎么保守。
    """
    snap: dict[str, Any] = {"env_temp": None, "env_source": None, "presence": None}
    _fill_from_world(snap, world_ref)
    if snap["env_temp"] is not None:
        snap["env_source"] = "world"
    else:
        snap["env_temp"] = _forecast_temp(weather_ref)
        if snap["env_temp"] is not None:
            snap["env_source"] = "forecast"
    return snap


def _fill_from_world(snap: dict[str, Any], world_ref: Any) -> None:
    w = None
    try:
        w = world_ref() if callable(world_ref) else world_ref
    except Exception:  # noqa: BLE001
        w = None
    if w is None:
        return
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
    """行动闸门 + 留痕。规则本身在 `_decide`。"""
    g = _decide(entity_id, action, world, user_text)
    if not g.allow or g.warning:
        # 🔴 09-18 到 10-11 闸门一个字日志都没有，温度规则空转三周没人发现
        logger.info("行动闸门：%s %s → %s（%s）facts=%s",
                    entity_id, action, "放行+提醒" if g.allow else "拦下",
                    g.reason, {k: v for k, v in g.facts.items() if k != "entity"})
    return g


def _decide(entity_id: str, action: str, world: dict[str, Any],
            user_text: str = "") -> GateResult:
    """行动闸门。`action`：on / off / 或 climate 的 mode|温度描述。

    DENY 时返回的 human 写**事实和方向**，不写台词 —— 话是 Nox 的。
    `action="off"` 视为收手，绝大多数检查不适用（关东西不闯祸）。
    """
    entity_id = (entity_id or "").strip()
    world = world or {}
    env_temp = world.get("env_temp")
    facts = {"entity": entity_id, "action": action,
             "env_temp": env_temp, "env_source": world.get("env_source"),
             "presence": world.get("presence")}

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
    if threshold is not None and env_temp is None and not re.search(ENV_UNKNOWN_EVIDENCE, user_text or ""):
        # 不知道现在多少度，她这句也没在要 —— 先问，不默认执行（Unknown 原则，糖糖 10-11）
        name = model.get("name", entity_id)
        return GateResult(
            allow=False, reason=UNKNOWN_ENVIRONMENT, facts=facts,
            human=f"现在拿不到温度（天气预报没取到），{name}开不开我判断不了，先没动。",
            ask=f"现在冷吗？要开{name}吗？",
        )
    if threshold is not None and env_temp is not None and env_temp > threshold:
        return GateResult(
            allow=False, reason=ENVIRONMENT_CONFLICT, facts=facts,
            human=(f"现在{'户外（天气预报）' if world.get('env_source') == 'forecast' else '环境'}"
                   f"温度 {env_temp:g}°C，{model.get('name', entity_id)}"
                   f"开着只会更热——这个动作和当下条件是冲突的。"),
            alternative=model.get("alternative", ""),
        )

    # ---- 5. quiet_hours：只警告，不拦 ----
    warning = ""
    if model.get("quiet_hours"):
        hour = now_cst().hour   # 北京时间，不看机器时区（边界法则 R9）
        if QUIET_HOUR_RANGE[0] <= hour < QUIET_HOUR_RANGE[1]:
            warning = (f"（现在是凌晨 {hour:02d} 点，如果她刚醒需要灯/电视，"
                       "亮度和小声是对的。）")

    return GateResult(allow=True, facts=facts,
                      warning=warning, reason=ALLOW)
