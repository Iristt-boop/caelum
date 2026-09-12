#!/usr/bin/env python3
"""把「pinned 纳入 token 预算」打进 Ombre-Brain/server.py（审计 4.4 下半）。

为什么是一个脚本而不是我直接改：Ombre-Brain 是独立仓库，不在这个会话的
隔离工作区里，编辑工具拒绝碰主目录的文件。所以改动写在这儿，由她执行。

跑法（在 D:\\claude-code 下）：
    python .claude/worktrees/awesome-sanderson-1e4889/scratch/apply-ob-pinned-budget.py

改完用 `cd Ombre-Brain && git diff` 自己看一眼再提交。
重复跑会直接退出，不会改第二遍。
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path(r"D:\claude-code\Ombre-Brain\server.py")

ANCHOR_CONST = """# --- Initialize core components / 初始化核心组件 ---
embedding_engine = EmbeddingEngine(config)"""

NEW_CONST = '''# --- Surfacing budget / 浮现预算 ---
# 钉选桶（核心准则）最多能吃掉 breath 预算的多大比例。
#
# 🔴 为什么需要这个上限（2026-09-12 加，审计 4.4）：
# 原来钉选桶那个循环**没有任何预算判断** —— 有多少钉选就脱水多少条，
# 然后 `token_budget = max_tokens` 再逐条减去，可以直接减成负数；
# 于是动态浮现那个循环第一次 `if token_budget <= 0` 就退出了。
#
# 后果是渐进的、静默的：钉选越攒越多 → 动态记忆被挤得越来越少 →
# 最后他只剩「核心准则」，对糖糖当下这句话完全没有相关记忆。
# 不报错，表现只是「他好像越来越记不住最近的事」。
#
# 0.7 的意思是：**动态浮现永远至少留下 30%**。
# 相关性不该被历史挤掉 —— 核心准则说的是「他一直是谁」，
# 动态浮现说的是「她现在在说什么」，后者少了，他就只会背诵。
#
# ⚠️ count_tokens_approx 对非中文严重低估（审计 4.2），所以这是个粗预算，
# 不是精确闸门。它挡的是「钉选把预算吃光」这种量级的事。
PINNED_BUDGET_RATIO = 0.7

# --- Initialize core components / 初始化核心组件 ---
embedding_engine = EmbeddingEngine(config)'''

OLD_LOOP = '''        pinned_results = []
        for b in pinned_buckets:
            try:
                clean_meta = {k: v for k, v in b["metadata"].items() if k != "tags"}
                summary = await dehydrator.dehydrate(strip_wikilinks(b["content"]), clean_meta)
                pinned_results.append(f"📌 [核心准则] [bucket_id:{b['id']}] {summary}")
            except Exception as e:
                logger.warning(f"Failed to dehydrate pinned bucket / 钉选桶脱水失败: {e}")
                continue'''

NEW_LOOP = '''        # 🔴 钉选桶也要吃预算（2026-09-12 修，审计 4.4）——
        # 见文件上方 PINNED_BUDGET_RATIO 那段说明。
        pinned_cap = int(max_tokens * PINNED_BUDGET_RATIO)
        pinned_used = 0
        pinned_skipped = 0
        pinned_results = []
        for b in pinned_buckets:
            try:
                clean_meta = {k: v for k, v in b["metadata"].items() if k != "tags"}
                summary = await dehydrator.dehydrate(strip_wikilinks(b["content"]), clean_meta)
                line = f"📌 [核心准则] [bucket_id:{b['id']}] {summary}"
                cost = count_tokens_approx(line)
                if pinned_used + cost > pinned_cap:
                    pinned_skipped += 1
                    continue
                pinned_results.append(line)
                pinned_used += cost
            except Exception as e:
                logger.warning(f"Failed to dehydrate pinned bucket / 钉选桶脱水失败: {e}")
                continue

        if pinned_skipped:
            # ⚠️ 截断核心准则是件大事，**绝对不能静默** ——
            # 它意味着有些「他以为自己一直记得」的东西这一轮没进去。
            logger.warning(
                "Pinned over budget / 钉选桶超预算：%d 条装下、%d 条被挡在外面"
                "（上限 %d tokens，占 max_tokens 的 %.0f%%）。"
                "该 pulse 一遍，把不再算准则的钉选摘掉了",
                len(pinned_results), pinned_skipped, pinned_cap,
                PINNED_BUDGET_RATIO * 100,
            )'''

OLD_BUDGET = '''        token_budget = max_tokens
        for r in pinned_results:
            token_budget -= count_tokens_approx(r)'''

NEW_BUDGET = '''        # 钉选已经在上面按 pinned_cap 收过一次了，这里直接减掉它实际用掉的。
        # **不再逐条重算** —— 原来那样写会把已经受限的东西再减一遍，
        # 而且当年正是这行把 token_budget 减成负数的。
        token_budget = max_tokens - pinned_used'''


def main() -> int:
    if not TARGET.exists():
        print(f"找不到 {TARGET}")
        return 1

    src = TARGET.read_text(encoding="utf-8")

    if "PINNED_BUDGET_RATIO" in src:
        print("已经打过了（找到 PINNED_BUDGET_RATIO），不重复改。")
        return 0

    for name, old in (("常量锚点", ANCHOR_CONST), ("钉选循环", OLD_LOOP), ("预算行", OLD_BUDGET)):
        if src.count(old) != 1:
            print(f"🔴 [{name}] 在文件里出现 {src.count(old)} 次，期望正好 1 次 —— 中止，不猜。")
            return 2

    out = src.replace(ANCHOR_CONST, NEW_CONST)
    out = out.replace(OLD_LOOP, NEW_LOOP)
    out = out.replace(OLD_BUDGET, NEW_BUDGET)

    TARGET.write_text(out, encoding="utf-8")
    print("✅ 三处都改好了：")
    print("   · 新增常量 PINNED_BUDGET_RATIO = 0.7")
    print("   · 钉选循环加上限 + 被挡掉时打 WARNING")
    print("   · token_budget 改成 max_tokens - pinned_used（不会再变负）")
    print()
    print("下一步：cd D:\\claude-code\\Ombre-Brain && git diff   自己看一眼")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
