#!/usr/bin/env python3
"""把某个网易云歌单的歌下载到 VPS 缓存，供本地分析。

## 为什么要先下载

音频分析要真实的 mp3。eryu 的 `/music/url` 会把歌下载并缓存到
`data/music_cache/{id}.mp3` —— 我们借它的手把歌备齐，
再从本地把 mp3 拉下来跑 librosa。

## 为什么不直接全跑

糖糖的歌单加起来 1300+ 首，全下要 6.5 GB、分析一个半小时。
先跑「喜欢的音乐」那 337 首 —— 最能代表她口味的一批。

## 限速

每首之间歇一下。网易云对高频下载会限流，而且这台 VPS 上还跑着
bridge / nox-core / ombre-brain 一堆东西，别把带宽占死。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/nox-core")

CACHE = Path("/root/eryu/server/data/music_cache")


def load_env(path: str) -> dict:
    env = {}
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", default="705727340", help="歌单 id，默认「喜欢的音乐」")
    ap.add_argument("--limit", type=int, default=60, help="最多下几首")
    ap.add_argument("--sleep", type=float, default=1.5, help="每首之间歇几秒")
    args = ap.parse_args()

    env = load_env("/root/nox-core/.env")

    from tools.eryu import make_client  # noqa: E402
    from tools.mcp_client import McpClient  # noqa: E402

    # 1. 从网易云拿歌单里的歌
    ne = McpClient(env.get("NOX_NETEASE_URL", ""), name="netease", timeout=40)
    r = ne.call("get_playlist_songs", {"playlist_id": int(args.playlist)})
    if not r.ok:
        print("拿歌单失败：", r.error)
        return 1

    import re
    songs = []
    for line in (r.text or "").split("\n"):
        m = re.match(r"^\s*\d+\.\s*(.+?)\s*-\s*(.*?)\s*\(ID:(\d+)\)", line)
        if m:
            songs.append({"id": m.group(3), "name": m.group(1).strip(),
                          "artist": m.group(2).strip()})

    print("歌单里拿到 %d 首" % len(songs))

    # 已经在缓存里的跳过
    have = {p.stem for p in CACHE.glob("*.mp3")}
    todo = [s for s in songs if s["id"] not in have][:args.limit]
    print("已缓存 %d 首，这次下载 %d 首\n" % (
        len(songs) - len([s for s in songs if s["id"] not in have]), len(todo)))

    if not todo:
        print("没有要下的。")
        return 0

    # 2. 借 eryu 的 /music/url 把歌下下来
    eryu = make_client(env.get("NOX_ERYU_URL", ""), env.get("NOX_ERYU_TOKEN", ""))
    ok = fail = 0
    meta = {}
    for i, s in enumerate(todo, 1):
        try:
            # 超时给宽一点 —— 要现从网易云下 5 MB
            resp = eryu.get("/music/url", {"id": s["id"]})
            good = bool(resp.ok and (resp.data or {}).get("ok"))
        except Exception:
            good = False
        f = CACHE / ("%s.mp3" % s["id"])
        if good and f.exists() and f.stat().st_size > 0:
            ok += 1
            meta[s["id"]] = {"name": s["name"], "artist": s["artist"]}
            print("  ✅ %3d/%d  %s - %s" % (i, len(todo), s["name"][:26], s["artist"][:16]))
        else:
            fail += 1
            print("  ❌ %3d/%d  %s（VIP / 已下架 / 超时）" % (i, len(todo), s["name"][:26]))
        time.sleep(args.sleep)

    # 歌名歌手单独存一份，本地分析时好带上
    out = CACHE / "_names.json"
    old = {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            pass
    old.update(meta)
    out.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n成功 %d · 失败 %d · 缓存目录现在 %d 首 mp3" % (
        ok, fail, len(list(CACHE.glob("*.mp3")))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
