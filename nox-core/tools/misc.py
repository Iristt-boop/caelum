"""零碎但必要的工具。

放这儿的东西有个共同点：模型自己算不出来，只能问系统。
"""

from __future__ import annotations

from agent.llm import ToolSpec
from personality.mood import now_cst

_WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


TIME_SPEC = ToolSpec(
    side_effect="none",
    name="get_current_time",
    description=(
        "获取当前的北京时间。"
        "需要知道现在几点、今天几号、星期几时调这个 —— "
        "**不要凭猜**，你没有实时时钟。"
        "存记忆、写日记、算日期差的时候尤其要先调它。"
    ),
    parameters={"type": "object", "properties": {}},
)


def current_time(_args: dict) -> str:
    """返回北京时间。

    固定 UTC+8，不看服务器时区 —— 本地开发在 Windows、线上在阿里云，
    哪天有台机器时区没设对，模型会理直气壮地报错误时间，而且不会报错。
    """
    now = now_cst()
    return (
        f"{now:%Y-%m-%d %H:%M:%S}（{_WEEKDAY[now.weekday()]}，北京时间 UTC+8）"
    )


def register_all(loop) -> None:
    loop.register(TIME_SPEC, current_time)
