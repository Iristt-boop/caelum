#!/usr/bin/env python3
"""给 91 个直接构造的 ToolSpec 补上 side_effect / confirm_via（审计 3.1）。

为什么用脚本而不是手改 91 处：**映射表本身就是给人看的审计对象**。
手改的话，标错一个"花钱"成"只读"没有任何地方能发现；写成表就能被 review、
被 diff、被重跑。

⚠️ 原则：**拿不准就往重了标。**
标轻的代价是模型能直接碰，标重的代价只是多一次确认。

在 nox-core/ 下跑：
    python ../scratch/label-side-effects.py
"""

from __future__ import annotations

import pathlib
import re
import sys

GW = "本机网关每次弹窗问她"
GRANT = "本机网关按 Work Grant 授权（computer_start_work 时她点过一次头）"

# name -> (side_effect, confirm_via)
LABELS: dict[str, tuple[str, str | None]] = {
    # ---- 记忆 ----
    "recall_memory": ("read", None),
    "remember": ("write", None),
    "archive_memory": ("write", None),
    "memory_status": ("read", None),
    "review_memory": ("read", None),
    # delete=True 直接 os.remove、没有回收站；content= 覆盖正文且无修订历史（审计 4.3）
    "edit_memory": ("irreversible", None),
    # 合并后旧正文不可恢复（审计 4.3）
    "merge_memory": ("irreversible", None),

    # ---- 她的电脑（闸门在她那台机器上，不在这儿）----
    "computer_read_file": ("read", None),
    "computer_find_files": ("read", None),
    "computer_search_files": ("read", None),
    "computer_write_file": ("write", GRANT),
    "computer_edit_file": ("write", GRANT),
    "computer_run_command": ("irreversible", GW),
    "computer_git_status": ("read", None),
    "computer_git_diff": ("read", None),
    "computer_git_log": ("read", None),
    "computer_browse": ("read", None),
    "computer_read_image": ("read", None),
    "computer_terminal_open": ("write", GRANT),
    "computer_terminal_send": ("irreversible", GW),
    "computer_terminal_read": ("read", None),
    "computer_terminal_list": ("read", None),
    # 关 shell 会把里面在跑的东西一起结束，但范围只在他自己开的那个
    "computer_terminal_close": ("write", None),
    "computer_start_work": ("write", GW),
    "computer_end_work": ("write", None),

    # ---- 日常 ----
    "send_gallery_image": ("write", None),
    "favorite_image": ("write", None),
    "add_todo": ("write", None),
    "complete_todo": ("write", None),
    "get_todos": ("read", None),
    "write_diary": ("write", None),

    # ---- 饮食 ----
    "search_food": ("read", None),
    "add_meal": ("write", None),
    "check_budget": ("read", None),
    "today_diet": ("read", None),
    "delete_meal_item": ("write", None),
    "add_exercise": ("write", None),
    "log_weight": ("write", None),
    "add_food": ("write", None),

    # ---- 共听 ----
    "eryu_search": ("read", None),
    "eryu_play": ("write", None),
    "eryu_get_lyric": ("read", None),
    "eryu_analyze": ("read", None),
    "eryu_get_memory": ("read", None),
    "eryu_save_memory": ("write", None),
    "eryu_roam": ("read", None),
    "eryu_daily": ("read", None),
    "eryu_similar": ("read", None),
    "eryu_remote_poll": ("read", None),
    "eryu_experience": ("read", None),
    "eryu_pick_by_mood": ("read", None),
    "eryu_recent": ("read", None),

    # ---- 家居（开灯关灯是可逆的；真正危险的是 ha-mcp 的 hass_set_state，见 3.5）----
    "ha_list_devices": ("read", None),
    "ha_get_state": ("read", None),
    "ha_switch": ("write", None),
    "ha_set_climate": ("write", None),
    "ha_set_light": ("write", None),

    # ---- 亲密 ----
    "send_meme": ("write", None),
    "send_voice_message": ("write", None),
    # 🔴 驱动她身上的物理设备，没有任何结构性确认（审计列为 Critical）
    "toy_set": ("irreversible", None),
    # 停是永远安全的 —— 描述里写着"她说停就调，不要先问再停"
    "toy_stop": ("write", None),
    "toy_status": ("read", None),
    "save_github_note": ("write", None),
    "append_github_note": ("write", None),
    "read_github_note": ("read", None),

    # ---- 杂 ----
    "get_current_time": ("none", None),

    # ---- 网易云（动的是她的账号）----
    "netease_playlists": ("read", None),
    "netease_playlist_songs": ("read", None),
    "netease_create_playlist": ("write", None),
    "netease_add_to_playlist": ("write", None),
    "netease_remove_from_playlist": ("write", None),
    "netease_like_song": ("write", None),
    "netease_recommend": ("read", None),
    "netease_history": ("read", None),

    # ---- 其余只读/写 ----
    "notion_search": ("read", None),
    "notion_read_page": ("read", None),
    "daily_summary": ("read", None),
    "reading_list_notes": ("read", None),
    "reading_reply_note": ("write", None),
    "reading_current": ("read", None),
    "reading_continue": ("read", None),
    "record_period": ("write", None),
    "remind_myself": ("write", None),
    "room_get_state": ("read", None),
    "room_move": ("write", None),
    "room_use_furniture": ("write", None),
    "room_stop": ("write", None),
    "web_search": ("read", None),
    "get_today_apps": ("read", None),
    "watching_history": ("read", None),
    "topics_browse": ("read", None),
}

# ⚠️ 这里必须写死 `\n` 再捕获缩进。
# 第一版写的是 `ToolSpec\(\s*\n?(\s*)name=` —— `\s*` 贪婪地把换行和缩进
# 一起吃掉了，于是捕获到的缩进是空串，生成出来的是顶格的
#     ToolSpec(
#     side_effect="...",
#     name="toy_set",
#         description=(
# Python 在括号里不计缩进，**所以它照样编译通过、照样能跑** ——
# 一个只有肉眼能发现的错。2026-09-12 靠 `cat -A` 看出来的。
SPEC_RE = re.compile(
    r"ToolSpec\(\n(\s+)name=[\"']([^\"']+)[\"']",
)


def main() -> int:
    root = pathlib.Path(".")
    if not (root / "agent" / "loop.py").exists():
        print("请在 nox-core/ 下跑这个脚本")
        return 1

    touched = 0
    missing: list[str] = []
    for p in sorted(root.rglob("*.py")):
        if "tests" in p.parts or ".venv" in p.parts:
            continue
        src = p.read_text(encoding="utf-8")
        if "ToolSpec(" not in src:
            continue
        out = src
        for m in SPEC_RE.finditer(src):
            indent, name = m.group(1), m.group(2)
            if name not in LABELS:
                missing.append(f"{p}::{name}")
                continue
            eff, via = LABELS[name]
            # 已经标过就跳过（幂等）
            block_start = m.start()
            block_end = src.find("\n    )", block_start)
            if "side_effect=" in src[block_start:block_end if block_end > 0 else block_start + 600]:
                continue
            add = f'{indent}side_effect="{eff}",'
            if via:
                add += f'\n{indent}confirm_via="{via}",'
            old = m.group(0)
            out = out.replace(old, f"ToolSpec(\n{add}\n{indent}name=\"{name}\"", 1)
            touched += 1
        if out != src:
            p.write_text(out, encoding="utf-8")

    print(f"标了 {touched} 处")
    if missing:
        print(f"🔴 映射表里没有这 {len(missing)} 个，没敢猜：")
        for x in missing[:20]:
            print("   ", x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
