#!/usr/bin/env python3
"""批量分析目录下的 mp3。并行跑，跳过已分析的。

单首约 24 秒，纯 CPU。糖糖的机器 16 逻辑核，默认开 6 个进程 ——
不占满是故意的，留几个核给她自己用，别把机器跑卡。

歌名/歌手从 VPS 的 music_data.json 带过来（可选），
拿不到就留空，不影响特征分析。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _one(args_tuple):
    """子进程里跑。librosa 在子进程里各自 import，不共享状态。"""
    from analyze_local import analyze

    path, song_id, name, artist = args_tuple
    t0 = time.time()
    try:
        result = analyze(Path(path), song_id, name, artist)
        return (song_id, result, time.time() - t0, None)
    except Exception as exc:
        return (song_id, None, time.time() - t0, "%s: %s" % (type(exc).__name__, exc))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="mp3 所在目录")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--meta", help="music_data.json，用来补歌名歌手")
    ap.add_argument("--force", action="store_true", help="已分析的也重跑")
    args = ap.parse_args()

    d = Path(args.dir)

    # 歌名歌手：从 recent 和 playlists 里凑一份 id → (name, artist)
    names: dict[str, tuple[str, str]] = {}
    if args.meta and Path(args.meta).exists():
        data = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        pools = list(data.get("recent") or [])
        for pl in data.get("playlists") or []:
            pools.extend(pl.get("songs") or [])
        for s in pools:
            sid = str(s.get("songId") or "")
            if sid:
                names[sid] = (s.get("name") or "", s.get("artist") or "")

    jobs = []
    skipped = 0
    for mp3 in sorted(d.glob("*.mp3")):
        sid = mp3.stem
        if not args.force and (d / ("%s_preanalysis.json" % sid)).exists():
            skipped += 1
            continue
        name, artist = names.get(sid, ("", ""))
        jobs.append((str(mp3), sid, name, artist))

    if not jobs:
        print("没有要分析的（已跳过 %d 首已分析的）" % skipped)
        return 0

    print("待分析 %d 首，跳过 %d 首，并行 %d 个进程" % (len(jobs), skipped, args.workers))
    print("单首约 24 秒，预计 %.1f 分钟\n" % (len(jobs) * 24 / args.workers / 60))

    t_all = time.time()
    ok = fail = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_one, j): j[1] for j in jobs}
        for fut in as_completed(futures):
            sid, result, dt, err = fut.result()
            if err:
                fail += 1
                print("  ❌ %-12s %.1fs  %s" % (sid, dt, err))
                continue
            ok += 1
            out = d / ("%s_preanalysis.json" % sid)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            label = result.get("name") or sid
            print("  ✅ %-12s %.1fs  %s" % (sid, dt, label))
            print("       BPM %-4s %-3s | 能量 %.3f | 起伏 %.3f | 明亮 %.0f | 谐波 %.2f" % (
                result["bpm"], result["key"], result["energy"],
                result["dynamics"], result["brightness"], result["harmonicRatio"]))

    print("\n总耗时 %.1f 分钟 · 成功 %d · 失败 %d" % ((time.time() - t_all) / 60, ok, fail))

    if ok:
        print("\n⚠️ 别忘了把结果传回 VPS，然后跑一次 sync_analyzed.py ——")
        print("   否则 ③ Music Experience 不知道 ④ 分析过了，")
        print("   stats 里 analyzedSongs 会一直是 0（2026-08-11 踩过）。")
        print("   scp *_preanalysis.json root@…:/root/eryu/server/data/music_cache/")
        print("   ssh root@… python3 /root/sync_analyzed.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
