"""拿 shadow 里她的真句子，验时间抽取的提示词（2026-09-18）。

    .venv\\Scripts\\python.exe tests\\smoke_temporal_prompt.py

## 为什么要有这个

`test_temporal_extract.py` 喂的是**假模型输出**（`_x(raw)`），它验的是
解析那一段 —— 提示词改成什么样它都绿。而 2026-09-18 修的两条
（「昨晚」跨午夜、「一会」是模糊）**改的就是提示词**。

所以这里送真句子给真模型，判据是**它认成了什么 kind**。

句子全部来自线上 shadow 日志（`journalctl -u nox-core | grep 时间理解`），
不是编的 —— 编的句子验不出「她实际会怎么说」。

⚠️ 要花钱（utility 模型，11 次，厘级）。不进 pytest，手动跑。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.ERROR)

from agent.adapters import make_adapter  # noqa: E402
from config import config  # noqa: E402
from temporal.extract import TemporalExtractor  # noqa: E402

#: (她的原话, 期望认出的 kind 列表（按出现顺序）, 这条在验什么)
#: 空列表 = 不该产出事件
CASES: list[tuple[str, list[str], str]] = [
    # ---- 2026-09-28 第二版：多事件 + duration 方向 ----
    ("早上起得晚嘛。收到领导，我今天晚上就去放好鸡蛋，明天你给我蒸",
     ["day_offset", "day_offset"], "🔴 一句两个时间：今晚放鸡蛋 / 明天蒸"),
    ("Moments明天上线，今天给你加了长任务循环", ["day_offset", "day_offset"], "🔴 一句两个时间"),
    ("Glm做工具显示直接做坏了。导致这3个小时我都连不上你", ["duration"], "🔴 过去的一段，不是三小时后"),
    # ---- 2026-09-18 修的两条 ----
    ("昨晚？你再看看？昨晚我就醒了18分钟", ["last_night"], "🔴 昨晚：原来被认成 day_offset -1"),
    ("不知道 睁不开眼 昨晚老醒", ["last_night"], "🔴 昨晚"),
    ("那我一会再煎个鸡蛋 。", ["vague"], "🔴 一会：原来被折算成 30 分钟"),
    ("先给你一个早安亲亲", [], "🔴 招呼语不算时间表达"),
    # ---- 原来就对的，别改坏 ----
    ("今天脑子不太清醒  背了一半", ["day_offset"], "回归"),
    ("下午要出去", ["day_offset"], "回归（带 slot）"),
    ("今天体重51.8。胖了", ["day_offset"], "回归"),
    ("你今晚不能给我开电热毯了。", ["day_offset"], "回归（今晚≠昨晚）"),
    ("今天没有英语小课堂？", ["day_offset"], "回归"),
]


def main() -> int:
    # 只要 utility 那一个 adapter —— 起整个 Nox 在本机缺 bridge 起不来，
    # 而这里验的就是 utility 这条线（`api/server.py:1330` 用的也是它）
    if not config.utility.usable:
        print("🔴 没有配 utility 模型，验不了 —— **这不是通过**")
        return 1
    adapter = make_adapter(config.utility)
    print(f"用的模型：{config.utility.model}\n")

    x = TemporalExtractor(lambda: adapter)
    bad = 0
    produced = 0
    for text, want, why in CASES:
        got = x.extract(text)
        got_kinds = [e.intent.kind for e in got]
        produced += len(got)
        ok = got_kinds == want
        bad += 0 if ok else 1
        mark = "✅" if ok else "🔴"
        print(f"{mark} {str(want or '(不产出)'):<28} ← 实际 {str(got_kinds):<28} "
              f"｜{text[:24]}  # {why}")
        for e in got:
            print(f"      「{e.expression}」→ {e.event}（{e.act}）{e.intent.to_dict()}")

    print()
    # 🔴 空集不是通过：一条都没跑成也会「0 个不对」
    if not CASES:
        print("🔴 一条用例都没有，不下结论")
        return 1

    # 🔴 **全 None = 链路挂了，不是结果。**
    #
    # 第一次跑这个脚本时本机没有 key，11 条全 None，而期望 None 的那条
    # 显示成 ✅ —— 一个整条链路 401 的运行，报出来是「1/11 对」。
    # 那个 ✅ 是**空断言**：它没有区分「模型认对了」和「模型根本没被调到」。
    if produced == 0:
        print("🔴 一条都没产出 —— 这是链路挂了（key？模型名？），不是模型认不出来。")
        print("   把 logging 调到 DEBUG 看真因。**不下结论。**")
        return 1

    print(f"{len(CASES) - bad}/{len(CASES)} 对")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
