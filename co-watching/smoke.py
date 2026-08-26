#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""co-watching 端到端冒烟。**要网络、要服务在跑。**

    python3 smoke.py                 # 用已知能用的那条 B站链接
    python3 smoke.py <B站链接>
    python3 smoke.py --analyze       # 连视觉预分析一起验（慢，会写 data/analysis）

## 和 test_observer.py 的分工

    test_observer.py   离线单测，不打网络不打 ffmpeg —— 每次改代码都跑
    smoke.py（本文件） 打真网络真视频 —— 部署后手工跑一次

⚠️ 这两个必须分得清。2026-08-22 之前这个目录里躺着 **12 个** `test_bili*.py`
`test_frame*.py` 之类的一次性探针，`ls` 一眼看过去分不清哪个是真测试、
哪个是当年调试随手写的。本文件是它们的合并版，其余删了。

## 判定原则

**「没有」和「坏了」要分开。** B站大部分视频没有 CC 字幕 —— 那是事实不是故障，
所以字幕拿不到记 SKIP 不记 FAIL。真正的 FAIL 只有「该有的东西没有」。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3200"
# 8-21 那晚验过能用的一条（有字幕）。换片子时把这个也换掉
DEFAULT_URL = "https://www.bilibili.com/video/av770415011"
BANGUMI_URL = "https://www.bilibili.com/bangumi/play/ep692437"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[status]
    print(f"  {mark} {name}{('：' + detail) if detail else ''}")


def get(path: str, timeout: int = 60):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.load(r)


def post(path: str, body: dict | None = None, timeout: int = 180):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_analyze = "--analyze" in sys.argv
    url = args[0] if args else DEFAULT_URL

    print(f"=== co-watching 冒烟 · {url}")

    # ---- import
    try:
        d = post("/api/import", {"url": url})
        sid = d["session_id"]
        record("import", "PASS", f'{d["title"]} | {d["duration"]}s')
    except Exception as exc:  # noqa: BLE001
        record("import", "FAIL", f"{type(exc).__name__}: {exc}")
        return summary()

    # ---- session
    try:
        s = get(f"/api/session/{sid}", 15)
        record("session", "PASS" if s.get("session_id") == sid else "FAIL", str(s.get("title")))
    except Exception as exc:  # noqa: BLE001
        record("session", "FAIL", str(exc))

    # ---- stream（Range 必须回 206，不然前端 seek 不了）
    try:
        req = urllib.request.Request(f"{BASE}/api/stream/{sid}")
        req.add_header("Range", "bytes=0-262143")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(262144)
            cr = r.headers.get("Content-Range")
            ok = r.status == 206 and len(data) > 0
            record("stream (Range)", "PASS" if ok else "FAIL",
                   f"HTTP {r.status} | {len(data)} 字节 | {cr}")
    except Exception as exc:  # noqa: BLE001
        record("stream (Range)", "FAIL", str(exc))

    # ---- 声音那条轨
    #
    # ⚠️ **这一条是 2026-08-22 补的，因为它漏掉过一个大 bug。**
    # 原来只验「stream 有字节流出来」—— 字节一直在流，但那是**纯视频轨**，
    # B站是 DASH 分轨的，声音在另一条上。拉流从上线起就是无声的，
    # 冒烟一路绿灯。**「有数据」不等于「是对的数据」。**
    try:
        if not d.get("separate_audio"):
            record("音频轨", "SKIP", "这个源不是分轨的（自带声音）")
        else:
            req = urllib.request.Request(f"{BASE}/api/stream/{sid}?track=audio")
            req.add_header("Range", "bytes=0-65535")
            with urllib.request.urlopen(req, timeout=60) as r:
                got = len(r.read(65536))
                ok = r.status == 206 and got > 0
                record("音频轨", "PASS" if ok else "FAIL",
                       f"HTTP {r.status} | {got} 字节")
    except Exception as exc:  # noqa: BLE001
        record("音频轨", "FAIL", str(exc))

    # ---- 字幕（**没有不算坏**）
    try:
        sub = get(f"/api/subtitles/{sid}", 20)
        record("字幕", "PASS", f'{sub["count"]} 条 ({sub["lang"]})')
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            record("字幕", "SKIP", "这个视频没有 CC 字幕（B站大部分都没有，不是故障）")
        else:
            record("字幕", "FAIL", f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        record("字幕", "FAIL", str(exc))

    # ---- 抽帧 + vision
    try:
        f = get(f"/api/frame/{sid}?t=30", 120)
        if f.get("description"):
            record("抽帧 + vision", "PASS", f["description"][:60].replace("\n", " "))
        elif f.get("frame_url"):
            record("抽帧 + vision", "FAIL", "帧抽出来了但 vision 没描述（看 key / 模型名）")
        else:
            record("抽帧 + vision", "FAIL", "帧都没抽出来")
    except Exception as exc:  # noqa: BLE001
        record("抽帧 + vision", "FAIL", str(exc))

    # ---- 番剧 iframe（第二模式）
    try:
        ep = post("/api/episode", {"url": BANGUMI_URL}, 40)
        ok = bool(ep.get("embed_url"))
        record("番剧 iframe", "PASS" if ok else "FAIL",
               f'{ep.get("title")} {ep.get("episode")}')
    except Exception as exc:  # noqa: BLE001
        record("番剧 iframe", "FAIL", str(exc))

    # ---- 视觉预分析（慢，默认不跑）
    if do_analyze:
        try:
            post(f"/api/analyze/{sid}", timeout=40)
            for _ in range(60):
                time.sleep(5)
                st = get(f"/api/analyze/{sid}", 20)
                if not st.get("running"):
                    break
            ev = get(f"/api/observations/{sid}", 30)["items"]
            mins = max(st.get("completed_through_ms", 0) / 60000, 1e-6)
            record("视觉预分析", "PASS" if st.get("status") == "ready" else "FAIL",
                   f'{st.get("status")} | {len(ev)} 个事件 | {len(ev)/mins:.1f} 次/分')
        except Exception as exc:  # noqa: BLE001
            record("视觉预分析", "FAIL", str(exc))
    else:
        record("视觉预分析", "SKIP", "加 --analyze 才跑（慢，而且会写 data/analysis）")

    return summary()


def summary() -> int:
    bad = [r for r in results if r[1] == "FAIL"]
    print(f"\n=== {len(results) - len(bad)} 过 / {len(bad)} 挂")
    for name, _, detail in bad:
        print(f"    ✗ {name}：{detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
