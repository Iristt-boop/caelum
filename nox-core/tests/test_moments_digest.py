"""Moments shadow 的当日汇总脚本（T9 顺带）：报告末尾要能看到正文。

规格见 `scripts/moments-shadow-digest.py`（跑在 VPS 上，独立一个文件，
不能 import `moments` 包）。这一版要回答的第二个问题终于有数据了 ——
「发出来的东西像不像他自言自语」：shadow 现在会真生成正文，
`MomentRecord.log()` 把它打在行尾 `｜body=<正文>`。

## 这一组守的是什么

🔴 **解析不到就明说「这一段还没有数据」，不许打一个空标题。**
报告里出现一个标题下面是空的，读的人分不清「这几天没有本来会发的」
和「脚本没解析到」—— 那正是 CAELUM-MAP 第三·五节第一种假验证的形状。

## 每条测试能挡什么、不能挡什么

写在各自 docstring 里。⚠️ 不跑 journalctl、不连库：只测解析和排版，
那两件才是这一版新加的部分。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "moments-shadow-digest.py"
_spec = importlib.util.spec_from_file_location("moments_shadow_digest", _SCRIPT)
digest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(digest)


def _line(*, at: str = "2026-09-18T00:18:05+00:00", mode: str = "shadow",
          drives: str = "{'concern': 0.59}", reason: str = "shadow",
          body: str | None = "今天的风") -> str:
    """一条和 `MomentRecord.log()` 形状一致的日志行（`-o cat`，没有前缀）。"""
    tail = "｜body=-" if body is None else f"｜body={body}"
    return (
        f"Moments｜{at}｜mode={mode}｜drives={drives}｜value=0.00 inner=0.00"
        f" timing=1.00｜threshold=0.45 dice=0.00 p=0.00"
        f"｜posted=False reason={reason}｜（人话）｜body 段{tail}"
    )


def test_parse_extracts_the_body_from_the_log_line():
    """`parse()` 要把行尾 `｜body=<正文>` 取出来 —— 这是这一版新增的料。

    能挡：正则漏了 body（新增字段却没进解析）、把 `-` 当成正文
          （那是「没有正文」的占位，不是一条叫 `-` 的帖子）。
    不能挡：正文里的换行会不会把一行日志拆开 —— 提示词第 4 条管着它。
    """
    rec = digest.parse(_line(body="今天的风"))

    assert rec is not None
    assert rec["body"] == "今天的风"

    #: 老格式（这一版之前打的那些行）没有 body 段 —— 当作没有，不许炸
    old = _line(body=None).replace("｜body 段｜body=-", "")
    assert digest.parse(old)["body"] == ""

    #: `body=-` 是「没有正文」，不是正文
    assert digest.parse(_line(body=None))["body"] == ""


def test_recent_bodies_are_formatted_with_time_and_lead():
    """排版成 `[09-18 00:18] 担心她领头 —— 「……」`，最多 5 条、每条截 80 字。

    时间让人能顺着点回那一条；领头的心事让人一眼看出「他这几天都在
    想什么」。截 80 字是因为这份报告是给人扫的，不是全文归档。

    能挡：不带时间 / 不带领头、条数不封顶（几十条会把报告淹掉）、
          不截长度。
    不能挡：正文写得好不好 —— 那是线上人看的事。
    """
    recs = [
        {"at": "2026-09-18T00:18:05+00:00", "drives": {"concern": 0.59},
         "reason": "shadow", "body": "第一条"},
        {"at": "2026-09-18T01:18:05+00:00", "drives": {"curiosity": 0.46},
         "reason": "shadow", "body": "第二条"},
        #: 不是 shadow（没到阈值 / 骰子没中）的不算「本来会发出去」
        {"at": "2026-09-18T02:18:05+00:00", "drives": {"longing": 0.4},
         "reason": "dice", "body": "第三条"},
        {"at": "2026-09-18T03:18:05+00:00", "drives": {"longing": 0.8},
         "reason": "shadow", "body": "长" * 200},
    ]
    for i in range(4, 7):
        recs.append({"at": f"2026-09-18T0{i}:18:05+00:00",
                     "drives": {"longing": 0.8}, "reason": "shadow",
                     "body": f"第{i}条"})

    lines = digest.format_recent_bodies(recs)

    assert len(lines) == 5, "最多 5 条"
    assert any("09-18 01:18" in ln and "被一件事勾着领头" in ln for ln in lines)
    assert all("「" in ln and "」" in ln for ln in lines)
    long_line = next(ln for ln in lines if "长" in ln)
    assert "长" * 80 in long_line and "长" * 81 not in long_line
    assert not any("第三条" in ln for ln in lines), "不是 shadow 的不上这段"
    #: 领头映射：更早那条 concern 的（放宽 limit 才看得到，验证的是词表不是条数）
    all_lines = digest.format_recent_bodies(recs, limit=10)
    assert any("09-18 00:18" in ln and "担心她领头" in ln for ln in all_lines)


def test_no_recent_bodies_says_so_instead_of_an_empty_title():
    """没有可显示的正文 → 报告里写「（这一段还没有数据）」，**不是空标题**。

    空标题读起来像「这几天没有本来会发的」，其实可能是脚本没解析到 ——
    这两种在报告里必须分得开（`docs/LOGGING.md` 空集陷阱）。

    能挡：`format_recent_bodies` 返回 [] 时 main 那边什么都不打。
    不能挡：报告其余部分的空集处理（那是脚本原来的 `min_ticks` 闸门）。
    """
    assert digest.format_recent_bodies([]) == []
    assert digest.format_recent_bodies([
        {"at": "2026-09-18T00:18:05+00:00", "drives": {"concern": 0.59},
         "reason": "dice", "body": ""},
    ]) == []
    assert "还没有数据" in digest.NO_BODY_PLACEHOLDER
