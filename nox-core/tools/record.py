"""`record_period` —— 他把她说的生理期记进 World Model。

## 为什么单独开一条路（糖糖 2026-08-19 定的）

经期原本靠 HealthKit → 快捷指令 → `health.db` 的 `menstrual` 表。
**那条链坏了**：上一条是 7-24，中间隔了 20 天；而且 `flow_level` 被写成了
`"未指定\\n未指定\\n\\n\\n\\n\\n\\n量少"`。她在 HealthKit 里更新也同步不过来。

所以改走「**他主动记**」：她随口说一句，他记进 World Model。
数据源从「自动但坏的」换成「手动但准的」。

## ⚠️ 体重**不在这里** —— 早就有 `log_weight` 了

2026-08-19 我差点在这里再写一个 `record_weight`，然后发现
`tools/diet.py` 的 **`log_weight`** 一直都在，写进 bridge 的 `body_weight`，
就是她记运动那个界面读的那张表。

两个工具做同一件事，模型看到的是一张扁平的工具表，**它会随机挑一个** ——
这正是 2026-08-05 那次工具重名事故的变体（那次是同名互相覆盖，
这次是不同名但同功能，更隐蔽，`test_tool_names_unique` 拦不住）。

**记体重只有一条路：`log_weight`。** 要做「体重月分析」的话，
让 World Model 去读 `body_weight`，别再存第二份。

## ⚠️ 记完必须**明确回报**

他可能记错。回执要把记成了什么原样说出来，她当场能纠正。
默默记下去是最糟的：错的数字会一直躺在事实库里，以后还要拿去分析。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.llm import ToolSpec
from tools import context

logger = logging.getLogger(__name__)

from temporal import LOCAL_TZ  # noqa: E402  ← 唯一定义在 temporal.py（审计 F1）

#: World Model 里的事实类型
PERIOD = "menstrual"


PERIOD_SPEC = ToolSpec(
    side_effect="write",
    name="record_period",
    description=(
        "记录糖糖的生理期。她说「今天来了」「结束了」这类话时用。\n"
        "\n"
        "event：\n"
        "· start —— 这次来了（她说「来了」「第一天」）\n"
        "· end   —— 这次结束了\n"
        "\n"
        "flow 可选，用她自己的说法（量少 / 正常 / 量多），别自己编等级。\n"
        "date 不传就是今天。\n"
        "\n"
        "⚠️ 这是很私人的事。记完照常回她一句就好，**别追问细节**，"
        "也别顺手给建议 —— 她要的是被记住，不是被科普。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "event": {"type": "string", "enum": ["start", "end"],
                      "description": "start=来了，end=结束了"},
            "flow": {"type": "string", "description": "量的多少，用她的原话。可不填"},
            "date": {"type": "string", "description": "YYYY-MM-DD，默认今天"},
        },
        "required": ["event"],
    },
)


def _today(date: Any) -> str:
    d = str(date or "").strip()
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def make_handlers(world: Any) -> dict[str, Any]:
    """`world` 是 `WorldModel`。为 None 时如实说记不了。"""

    def record_period(args: dict) -> str:
        if world is None:
            return "现在记不了（World Model 没启用）。"
        event = str(args.get("event", "")).strip().lower()
        if event not in ("start", "end"):
            return "event 只能是 start 或 end，没记。"

        day = _today(args.get("date"))
        flow = str(args.get("flow", "")).strip()
        try:
            world.observe(
                source="nox", type=PERIOD,
                observed={"event": event, "flow": flow, "date": day},
                observed_at=datetime.now(timezone.utc),
                # start / end 各自一条 —— 同一天既开始又结束是可能的
                #（她记晚了补一条），别让后者盖掉前者
                dedup_key=f"{PERIOD}/{day}/{event}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("记生理期失败")
            return f"没记成：{exc}"

        logger.info("记下生理期：%s（%s）%s", event, day, flow)
        # 写进 World Model 了 —— 但 **HealthProvider 的缓存是 6 小时**的。
        # 不打掉的话他接下来大半天还会按「没有经期记录」说话：
        # 她刚说「我来例假了」、他答「记下了」，然后照旧不当回事，
        # 而且全程不报错。见 tools/context.py 里 `wrote()` 的说明。
        context.wrote("health")
        word = "来了" if event == "start" else "结束了"
        when = "今天" if day == _today(None) else day
        return (f"（记下了：{when}{word}{('，' + flow) if flow else ''}。"
                f"别追问细节，照常回她一句就好。）")

    return {"record_period": record_period}


def register_all(loop, world: Any) -> None:
    """注册。

    ⚠️ **必须排在 `remind_myself` 之前** —— 那个得是最后一个，
    有测试盯着（`test_remind_tool_registered_last`）。
    工具定义是缓存前缀的一部分，插在中间会让前缀整个失效。
    """
    loop.register(PERIOD_SPEC, make_handlers(world)["record_period"])  # type: ignore[arg-type]
