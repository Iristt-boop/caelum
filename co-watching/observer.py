# -*- coding: utf-8 -*-
"""便宜的视觉层 —— 「什么时候值得说话」的信号源。

## 它补的是哪个洞（架构 14.4）

第六节的触发器写着「重要场景切换 / 情绪转折 / 视觉母题」当信号，
11.3 又定了「视觉一律抽帧 → vision 模型」。**这两条接不上** ——
vision 模型太贵，不可能每秒跑一次去问「现在算不算场景切换」。

所以分两层：**便宜的这层判断「何时」，贵的那层负责「说什么」。**
这个文件是便宜的那层。

## 为什么不用 OpenCV

对照的那个仓库（open-watch-cinema）用 Python + OpenCV 算直方图/边缘/光流。
我们这台是 2 核 3.7G 的小机器，而且 **cv2 没装**。

实测发现不需要：**ffmpeg 的 `scdet` 滤镜本身就是帧间差分**
（`mafd` = mean absolute frame difference），C 代码跑，不用 per-frame 的
Python 循环。2026-08-22 在 VPS 上实测：

    60 秒素材、2 fps、缩到 160 宽 → 10.3 秒
    **5.8 倍实时**，2 小时的片子约 21 分钟

零新依赖，比装 OpenCV 快也稳。

## ⚠️ 阈值是自适应的，不是拍脑袋定的

同一天实测那条片子（剪得很快的短视频）：

    min=0.00  p50=4.04  p90=10.97  p99=20.06  max=25.48

**固定阈值必然错**：慢节奏的文艺片永远到不了 20，快剪的片子又会满屏都是。
所以按每块自己的分位数定，再加一个绝对下限兜住"整块都很静"的情况。

每块的分位数会**原样写进 chunk 文件**，以后想重新调阈值不用重新分析。

## ⚠️ 原始遥测不出这一层

`scores` 留在 chunk 文件里，读接口只回**事件**。理由同 14.4：
模型不需要看 600 个浮点数，它需要「这里切了一刀」。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger("co-watching.observer")

#: 算法版本。改了取样率/阈值算法就 +1 —— planHash 会跟着变，旧结果自动作废。
#:   1 → 2（2026-08-23）：加了 MOTION_FLOOR，静止画面不再刷假的运动峰；
#:                       切镜头的回声也不再被当成运动
ALGO_VERSION = 2

#: 一块分析多长（毫秒）。同 open-watch-cinema 的 MAX_ANALYSIS_DURATION_MS
CHUNK_MS = 300_000
#: 每块往前多吃一点，让帧差有前文。**但归属不重叠**（见 plan_chunks）
BOUNDARY_MS = 2_000
#: 取样率。0.5 秒一帧 —— 再密就分不清"切镜头"和"动作快"了（2fps 的固有限制）
SAMPLE_FPS = 2
#: 分析用的宽度。只算帧差，不需要细节
SAMPLE_WIDTH = 160

#: 场景切换的绝对下限。低于它的再"相对突出"也不算切镜头 ——
#: 不然一整块静止画面里的一点噪声会被当成切换
SCENE_FLOOR = 12.0
#: 场景切换的相对门槛：本块的这个分位数
SCENE_PCT = 0.98
#: 切镜头必须是**尖峰**，不能是平台 —— 要比前几帧的均值高出这么多倍。
#:
#: ⚠️ 这条是测试逼出来的（2026-08-22）：只用分位数的话，
#: 一段持续 6 秒的中等晃动（帧差稳定在 18）会因为"高于本块 p98"
#: 被报成**连续 12 次切镜头**。真实的剪辑点是"突然不一样"，
#: 而持续晃动是"一直在变" —— 前者归 scene_change，后者归 motion_peak
SPIKE_RATIO = 1.5
#: 两个运动峰之间至少隔这么久。平台期每一帧都满足"局部最大"（相等也算），
#: 不加这条一段匀速运动还是会刷出一串
MOTION_MIN_GAP_MS = 5_000
#: 两次切镜头之间至少隔这么久。
#:
#: ⚠️ 也是测试逼出来的：从静止切进一段动作戏时，**同一个切入点会被连报 4 次** ——
#: 起始那帧确实是剪辑点（该报），但随后几帧的"前几帧均值"还在被旧的静止值
#: 拖着，于是一路都满足尖峰条件。2 秒内的连续尖峰当成同一次。
#: （2 fps 本来也分辨不了更密的剪辑，见模块开头）
SCENE_MIN_GAP_MS = 2_000
#: 运动峰值：滑动均值的这个分位数
MOTION_PCT = 0.90
#: 运动的绝对下限。**和 SCENE_FLOOR 同理，但这条是后补的。**
#:
#: ⚠️ 2026-08-23 用合成数据探本地那条路时发现：一段**完全静止**的画面里
#: 刷出了 13 个「运动峰」—— 因为分位数是相对的，平坦信号里 p90 就等于底噪。
#: 更糟的是静止时 `motion_gate == motion_top`，跨度为 0 → 分数给 0.5 →
#: 超过 director 的 TENSION_GATE(0.35) → **静止长镜头被判成「正紧张着」**。
#: 那恰恰是最该说话的时刻，却被抑制器堵死。
#:
#: 实测参考：那条真片子 p50=3.81、motion_gate=8.53 —— 6.0 能留住真运动、
#: 掐掉静止底噪
MOTION_FLOOR = 6.0
#: 滑动窗口有几帧（2fps × 3 秒）。**持续的中等变化 = 运动**，
#: 而单帧尖峰 = 切镜头，两者要分开
MOTION_WIN = 6


def media_key(url: str) -> str:
    """一个视频的稳定身份。**按视频算，不按会话算** ——
    同一部片子第二次导入应该直接复用上次的分析结果。"""
    return hashlib.sha256(url.strip().encode()).hexdigest()[:16]


def plan_hash(key: str, duration_ms: int) -> str:
    """任一影响结果的参数变了 → 不同的 plan → 旧结果自动作废。

    比只哈希字幕/时长严：取样率、阈值算法、分块参数全在里面
    （open-watch-cinema 的 planHash 就是这么做的，架构 14.5）。
    """
    payload = json.dumps({
        "key": key, "duration_ms": duration_ms, "algo": ALGO_VERSION,
        "fps": SAMPLE_FPS, "width": SAMPLE_WIDTH,
        "chunk_ms": CHUNK_MS, "boundary_ms": BOUNDARY_MS,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def plan_chunks(duration_ms: int) -> list[dict]:
    """切块。

    ⚠️ **分析范围重叠，归属范围不重叠。** 每块往前多吃 2 秒是为了让帧差有前文
    （不然每块开头第一帧的差分是跟黑屏比，必然误判成切换）；
    但 `own_from/own_to` 只认逻辑区间，同一秒不会被两块各记一遍。
    """
    if duration_ms <= 0:
        raise ValueError("duration_ms 必须为正")
    step = CHUNK_MS - BOUNDARY_MS
    chunks = []
    start, i = 0, 0
    while start < duration_ms:
        end = min(duration_ms, start + step)
        analysis_from = start if i == 0 else start - BOUNDARY_MS
        chunks.append({
            "id": f"chunk-{i:04d}",
            "index": i,
            "status": "pending",
            "own_from": start,       # 归属：只有这段里的事件算这块的
            "own_to": end,
            "from": analysis_from,   # 分析：往前多吃一点
            "to": end,
            "attempts": 0,
            "events": None,
            "error": None,
        })
        start += step
        i += 1
    return chunks


def completed_through(chunks: list[dict]) -> int:
    """已经准备到第几毫秒。

    ⚠️ **只数从头连续完成的块。** 后面的块先跑完不算数 ——
    不然会出现「显示 80%，但第 3 分钟那块是空的」。
    （抄的 open-watch-cinema 的 completedThroughMs，架构 14.5）
    """
    through = 0
    for c in chunks:
        if c["own_from"] != through or c["status"] != "complete":
            break
        through = c["own_to"]
    return through


# ------------------------------------------------------------------ 解析

_SCORE = re.compile(r"lavfi\.scd\.score=([\d.]+)")
_TIME = re.compile(r"pts_time:([\d.]+)")


def parse_metadata(text: str) -> list[tuple[float, float]]:
    """ffmpeg 的 metadata=print 输出 → [(秒, score)]。

    输出长这样（实测）：

        frame:1    pts:1       pts_time:0.5
        lavfi.scd.mafd=15.431
        lavfi.scd.score=15.431
        lavfi.scd.time=0.5

    ⚠️ 第 0 帧的 score 永远是 0（没有前一帧可比），要丢掉 ——
    留着会把每块的分位数往下拉。
    """
    out: list[tuple[float, float]] = []
    cur_t: float | None = None
    for line in text.splitlines():
        m = _TIME.search(line)
        if m:
            cur_t = float(m.group(1))
            continue
        m = _SCORE.search(line)
        if m and cur_t is not None:
            out.append((cur_t, float(m.group(1))))
            cur_t = None
    return out[1:] if out else out


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


def derive_events(samples: list[tuple[float, float]], offset_ms: int,
                  own_from: int, own_to: int) -> dict:
    """帧差序列 → 事件 + 这一块的分布。

    两种事件，判据不同：

        scene_change  单帧尖峰      —— 切镜头
        motion_peak   滑动均值的局部高点 —— 持续的运动

    分开是有必要的：一个 0.5 秒的尖峰和一段 3 秒的持续晃动，
    对「现在适不适合说话」的含义完全不同。
    """
    scores = [s for _, s in samples]
    if not scores:
        return {"events": [], "stats": {}, "samples": 0}

    scene_gate = max(SCENE_FLOOR, _pct(scores, SCENE_PCT))
    top = max(scores)

    def _norm(value: float, gate: float, ceiling: float) -> float:
        """把「刚过门槛」到「本块最强」映射到 0~1。

        ⚠️ **不能拿门槛当分母。** 第一版就是 `min(1, score/gate)` ——
        而事件只在 `score >= gate` 时才产生，所以比值永远 ≥1，
        clamp 完**每个事件的分数都是 1.0**，这个字段一点信息都没有。
        真机上跑了一部片子才看出来（2026-08-22，21 个事件全是 1.0）。

        P3 的触发器要靠它排序选帧 —— 全是 1.0 的话排序等于随机。
        """
        span = ceiling - gate
        if span <= 1e-6:
            # 整块只有一个强度：给 0.5 而不是 1.0，别假装它很突出
            return 0.5
        return round(max(0.0, min(1.0, (value - gate) / span)), 3)

    # 滑动均值
    rolling: list[float] = []
    for i in range(len(scores)):
        lo = max(0, i - MOTION_WIN + 1)
        window = scores[lo:i + 1]
        rolling.append(sum(window) / len(window))
    motion_gate = max(MOTION_FLOOR, _pct(rolling, MOTION_PCT))
    motion_top = max(rolling) if rolling else 0.0

    events = []
    last_motion_at = -10 ** 9
    last_scene_at = -10 ** 9
    #: 上一个切镜头在第几个样本。**切一刀之后滑动均值会跟着抬起来**，
    #: 那是这一刀的回声，不是持续运动 —— 见下面 SCENE_ECHO 那段
    last_scene_i = -10 ** 9
    for i, (t, score) in enumerate(samples):
        at = int(t * 1000) + offset_ms
        # 归属：分析范围往前多吃了 2 秒，那一截不算这块的
        if not (own_from <= at < own_to):
            continue

        # 前几帧的均值（**不含自己**）—— 判断这一帧是不是"突然不一样"
        lo = max(0, i - MOTION_WIN)
        prev_scores = scores[lo:i]
        baseline = sum(prev_scores) / len(prev_scores) if prev_scores else score

        if (score >= scene_gate and score >= SPIKE_RATIO * max(baseline, 1e-6)
                and at - last_scene_at >= SCENE_MIN_GAP_MS):
            last_scene_at = at
            last_scene_i = i
            events.append({
                "at": at, "kind": "scene_change",
                # 归一化到 0~1 给上层用（原始值留在 stats 里）。
                # 分母是「门槛到本块最强」的跨度，不是门槛本身 —— 见 _norm
                "sceneScore": _norm(score, scene_gate, top),
                "raw": round(score, 2),
            })
            continue

        # 🔴 **切镜头的回声不算运动。**
        #
        # 2026-08-23 探本地那条路时发现：一次干净的切镜头（帧差 60）
        # 会让之后几帧的滑动均值抬到 11.7，超过门槛 → 报一个运动峰 →
        # director 看到「刚才有运动峰」判定「正紧张着」→
        # **每一次换场都把自己抑制掉，他永远开不了口。**
        #
        # 滑动窗口有多长，回声就有多长，所以按样本下标跳过一个窗口。
        if i - last_scene_i < MOTION_WIN:
            continue

        # 局部最大 + 过门槛 + 离上一个够远，才算一个运动峰。
        # 三个条件缺一不可：平台期每帧都是"局部最大"（相等也算 >=）
        if rolling[i] >= motion_gate > 0 and at - last_motion_at >= MOTION_MIN_GAP_MS:
            prev_r = rolling[i - 1] if i else 0.0
            next_r = rolling[i + 1] if i + 1 < len(rolling) else 0.0
            if rolling[i] >= prev_r and rolling[i] >= next_r:
                events.append({
                    "at": at, "kind": "motion_peak",
                    "motionScore": _norm(rolling[i], motion_gate, motion_top),
                    "raw": round(rolling[i], 2),
                })
                last_motion_at = at

    return {
        "events": events,
        # ⚠️ 分布原样留着：以后想重新调阈值，不用重新解码一遍片子
        "stats": {
            "p50": round(_pct(scores, 0.50), 2),
            "p90": round(_pct(scores, 0.90), 2),
            "p98": round(_pct(scores, 0.98), 2),
            "max": round(max(scores), 2),
            "scene_gate": round(scene_gate, 2),
            "motion_gate": round(motion_gate, 2),
        },
        "samples": len(scores),
    }


# ------------------------------------------------------------------ 跑

def ffmpeg_cmd(url: str, from_ms: int, to_ms: int, referer: str) -> list[str]:
    headers = f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n"
    return [
        "ffmpeg", "-hide_banner", "-nostdin",
        "-ss", f"{from_ms / 1000:.3f}", "-t", f"{(to_ms - from_ms) / 1000:.3f}",
        "-headers", headers, "-i", url,
        "-an",
        "-vf", (f"fps={SAMPLE_FPS},scale={SAMPLE_WIDTH}:-2,"
                f"scdet=threshold=0,metadata=print:file=-"),
        "-f", "null", "-",
    ]


class Analyzer:
    """一部片子的分析任务。可续跑、有锁、状态写在 manifest 里。"""

    def __init__(self, data_dir: str, key: str, duration_ms: int) -> None:
        self.dir = os.path.join(data_dir, "analysis", key)
        self.key = key
        self.duration_ms = duration_ms

    # ---- manifest

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.dir, "manifest.json")

    def load(self) -> dict | None:
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save(self, m: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        os.replace(tmp, self.manifest_path)  # 原子替换，别写到一半被读到

    def fresh(self) -> dict:
        return {
            "key": self.key,
            "plan": plan_hash(self.key, self.duration_ms),
            "algo": ALGO_VERSION,
            "duration_ms": self.duration_ms,
            "status": "pending",
            "chunks": plan_chunks(self.duration_ms),
            "started_at": None,
            "updated_at": None,
        }

    def ensure(self) -> dict:
        """拿到当前 manifest。参数变了（planHash 对不上）就重来。"""
        m = self.load()
        want = plan_hash(self.key, self.duration_ms)
        if m is None or m.get("plan") != want:
            if m is not None:
                logger.info("plan 变了（%s → %s），重新分析 %s",
                            m.get("plan"), want, self.key)
            m = self.fresh()
            self.save(m)
        return m

    # ---- 读

    def events(self, from_ms: int, to_ms: int) -> list[dict]:
        """区间内的事件。**只回事件，不回原始分数**（架构 14.4）。"""
        m = self.load()
        if not m:
            return []
        out = []
        for c in m["chunks"]:
            if c["status"] != "complete" or c["own_to"] <= from_ms or c["own_from"] >= to_ms:
                continue
            for e in c.get("events") or []:
                if from_ms <= e["at"] < to_ms:
                    out.append(e)
        out.sort(key=lambda e: e["at"])
        return out

    def progress(self, m: dict | None = None) -> dict:
        m = m or self.load()
        if not m:
            return {"status": "none", "percent": 0, "completed_through_ms": 0}
        chunks = m["chunks"]
        done = sum(1 for c in chunks if c["status"] == "complete")
        failed = sum(1 for c in chunks if c["status"] == "failed")
        return {
            "status": m.get("status"),
            "total": len(chunks),
            "complete": done,
            "failed": failed,
            "percent": round(done / len(chunks) * 100, 1) if chunks else 0,
            # 只数连续的那一段 —— 见 completed_through 的注释
            "completed_through_ms": completed_through(chunks),
            "stats": next((c.get("stats") for c in chunks if c.get("stats")), None),
        }


#: key → 正在跑的线程。**同一部片子不许并发跑两遍**
_running: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def is_running(key: str) -> bool:
    with _lock:
        t = _running.get(key)
        return bool(t and t.is_alive())


def run_analysis(analyzer: Analyzer, url_of, referer: str, max_chunks: int = 0) -> None:
    """把没跑完的块跑完。**每块现取直链** —— 直链几分钟就过期。

    `url_of` 是个函数不是字符串，就是为了这个。
    """
    m = analyzer.ensure()
    m["status"] = "running"
    m["started_at"] = m.get("started_at") or time.time()
    analyzer.save(m)

    done_this_run = 0
    for c in m["chunks"]:
        if c["status"] == "complete":
            continue
        if max_chunks and done_this_run >= max_chunks:
            break
        c["status"] = "running"
        c["attempts"] += 1
        analyzer.save(m)
        try:
            url = url_of()
            cmd = ffmpeg_cmd(url, c["from"], c["to"], referer)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            samples = parse_metadata(r.stdout or "")
            if not samples:
                raise RuntimeError(
                    f"没解析出任何帧（ffmpeg rc={r.returncode}）："
                    f"{(r.stderr or '')[-200:]}"
                )
            got = derive_events(samples, c["from"], c["own_from"], c["own_to"])
            c["events"] = got["events"]
            c["stats"] = got["stats"]
            c["samples"] = got["samples"]
            c["status"] = "complete"
            c["error"] = None
            logger.info("分析完成 %s %s：%d 帧 → %d 个事件",
                        analyzer.key, c["id"], got["samples"], len(got["events"]))
        except Exception as exc:  # noqa: BLE001
            # 一块炸了不许带塌整部片子 —— 它可以下次重来（attempts 记着）
            c["status"] = "failed"
            c["error"] = str(exc)[:300]
            logger.warning("分析失败 %s %s：%s", analyzer.key, c["id"], exc)
        m["updated_at"] = time.time()
        analyzer.save(m)
        done_this_run += 1

    pending = [c for c in m["chunks"] if c["status"] != "complete"]
    m["status"] = "ready" if not pending else (
        "failed" if all(c["status"] == "failed" for c in pending) else "partial"
    )
    analyzer.save(m)


def start_background(analyzer: Analyzer, url_of, referer: str) -> bool:
    """后台开跑。已经在跑就返回 False（不重复起）。"""
    with _lock:
        t = _running.get(analyzer.key)
        if t and t.is_alive():
            return False
        th = threading.Thread(
            target=run_analysis, args=(analyzer, url_of, referer),
            name=f"analyze-{analyzer.key}", daemon=True,
        )
        _running[analyzer.key] = th
        th.start()
        return True
