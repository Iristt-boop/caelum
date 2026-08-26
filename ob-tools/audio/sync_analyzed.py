#!/usr/bin/env python3
"""把「这首歌分析过了」同步进歌曲记忆。

## 为什么需要这一步

音频分析在糖糖电脑上跑，结果直接落成 `{id}_preanalysis.json`。
但 eryu 的歌曲记忆（`music_memory.json`）里有个 `analyzed` 字段，
**没人去改它** —— 于是实际分析了 49 首，`stats` 报 `analyzedSongs: 0`
（2026-08-11 查出）。

④ Music Intelligence 干完活了，③ Music Experience 不知道。

## 顺便补全 memory 条目

`music_memory.json` 只有「播过的歌」才有条目（listen 时建的），
而分析过的歌有 49 首。没条目的歌补一条，
这样 `stats.totalSongs` 才是真实的曲库规模，
而不是只数「播过的」。
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

DATA = Path("/root/eryu/server/data")
CACHE = DATA / "music_cache"
MEM = DATA / "music_memory.json"


def main() -> int:
    files = sorted(glob.glob(str(CACHE / "*_preanalysis.json")))
    if not files:
        print("没有分析结果")
        return 0

    try:
        mem = json.loads(MEM.read_text(encoding="utf-8"))
    except Exception:
        mem = {}

    marked = created = 0
    for f in files:
        try:
            a = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = str(a.get("songId") or Path(f).name.split("_preanalysis")[0])
        if not sid:
            continue

        entry = mem.get(sid)
        if entry is None:
            # 分析过但没播过 —— 补一条，让曲库规模是真实的
            entry = {
                "songId": sid,
                "name": a.get("name") or "",
                "artist": a.get("artist") or "",
                "listenCount": 0, "togetherCount": 0,
                "firstListened": None, "lastListened": None,
                "analyzed": False, "notes": "", "feeling": "",
                "favoriteLines": [], "tags": [],
            }
            mem[sid] = entry
            created += 1

        if not entry.get("analyzed"):
            entry["analyzed"] = True
            marked += 1
        # 顺手把名字补上（早期分析结果里是空的）
        if not entry.get("name") and a.get("name"):
            entry["name"] = a["name"]
            entry["artist"] = a.get("artist") or ""

    MEM.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")

    print("分析结果 %d 个" % len(files))
    print("  标记 analyzed：%d 首" % marked)
    print("  新建记忆条目：%d 首" % created)
    print("  记忆里现在共 %d 首" % len(mem))
    return 0


if __name__ == "__main__":
    sys.exit(main())
