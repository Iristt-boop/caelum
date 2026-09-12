#!/usr/bin/env python3
"""第二处同样的 bug：/breath-hook 的钉选桶也没吃预算（审计 4.4 续）。

第一个脚本只修了 breath() 工具那条路。实测发现 `/breath-hook`
（SessionStart 专用的记忆浮现口）有一模一样的写法：
    token_budget = 10000
    for b in pinned: ... token_budget -= count_tokens_approx(summary)
钉选攒多了就把 10000 吃穿，下面动态浮现第一次判断就 break ——
于是会话启动时**只有核心准则，没有近况**。

实证：2026-09-12 用 breath("") 打线上，返回 40 条核心准则、0 条浮现记忆。

跑法（在哪个目录都行，目标是写死的绝对路径）：
    python "D:\\claude-code\\.claude\\worktrees\\awesome-sanderson-1e4889\\scratch\\apply-ob-pinned-budget-2.py"
"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(r"D:\claude-code\Ombre-Brain\server.py")

OLD = '''        parts = []
        token_budget = 10000
        for b in pinned:
            summary = await dehydrator.dehydrate(strip_wikilinks(b["content"]), {k: v for k, v in b["metadata"].items() if k != "tags"})
            parts.append(f"📌 [核心准则] {summary}")
            token_budget -= count_tokens_approx(summary)'''

NEW = '''        parts = []
        # 🔴 和 breath() 那处是同一个 bug（2026-09-12 修，审计 4.4）。
        # 原来这里也是「有多少钉选就脱水多少」，token_budget 从 10000 一路
        # 减成负数，下面动态浮现第一次 `if token_budget <= 0` 就退出 ——
        # 于是**会话启动时只有核心准则、没有近况**。
        # 实证：修之前 breath("") 打线上，40 条核心准则 / 0 条浮现记忆。
        HOOK_TOKEN_BUDGET = 10000
        pinned_cap = int(HOOK_TOKEN_BUDGET * PINNED_BUDGET_RATIO)
        pinned_used = 0
        pinned_skipped = 0
        for b in pinned:
            summary = await dehydrator.dehydrate(strip_wikilinks(b["content"]), {k: v for k, v in b["metadata"].items() if k != "tags"})
            cost = count_tokens_approx(summary)
            if pinned_used + cost > pinned_cap:
                pinned_skipped += 1
                continue
            parts.append(f"📌 [核心准则] {summary}")
            pinned_used += cost

        if pinned_skipped:
            # 截断核心准则不许静默 —— 同 breath() 那处
            logger.warning(
                "breath-hook pinned over budget / 启动浮现的钉选超预算："
                "%d 条装下、%d 条被挡在外面（上限 %d tokens）",
                len(parts), pinned_skipped, pinned_cap,
            )

        # 动态浮现拿剩下的 —— 至少 30%，不会再是负数
        token_budget = HOOK_TOKEN_BUDGET - pinned_used'''


def main() -> int:
    if not TARGET.exists():
        print(f"找不到 {TARGET}")
        return 1

    src = TARGET.read_text(encoding="utf-8")

    if "HOOK_TOKEN_BUDGET" in src:
        print("已经打过了（找到 HOOK_TOKEN_BUDGET），不重复改。")
        return 0

    if "PINNED_BUDGET_RATIO" not in src:
        print("🔴 没找到 PINNED_BUDGET_RATIO —— 请先跑第一个脚本。中止。")
        return 3

    n = src.count(OLD)
    if n != 1:
        print(f"🔴 锚点在文件里出现 {n} 次，期望正好 1 次 —— 中止，不猜。")
        return 2

    TARGET.write_text(src.replace(OLD, NEW), encoding="utf-8")
    print("✅ /breath-hook 也修好了：")
    print("   · 钉选吃 PINNED_BUDGET_RATIO 的上限（动态至少留 30%）")
    print("   · 被挡掉的钉选打 WARNING")
    print("   · token_budget = HOOK_TOKEN_BUDGET - pinned_used，不会再变负")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
