"""后台活计的心跳台账（审计 1.4）。

## 它治的是哪种坏

nox-core 有三条后台循环，每一条死掉的症状都**不是报错**：

    attention 心跳   死了 → 他从此不再主动找她
    Care 快循环      死了 → 位置跃迁、随机惦记全没了
    话题池 Scout     死了 → 他再也不会带新东西来聊

`service.py` 的注释自己写着：「它死了不会有任何人发现，只是 Nox 从此
再也不主动说话了」。三条循环都用 `except Exception: 继续下一轮` 兜着，
所以**进程活着、`/health` 全绿、日志不红** —— 而那件事已经不发生了。

`except` 兜底是对的（一次失败不该让心跳死），但它同时也意味着
「连着失败一千次」和「一切正常」在外面看起来一模一样。
这个台账就是用来分开这两者的。

## 怎么用

    from obs import heartbeat

    heartbeat.declare("attention_tick", every_s=900)   # 启动时声明节奏
    ...
    heartbeat.beat("attention_tick")                   # 每次**成功**之后

`/health` 里读 `heartbeat.snapshot()`。

## 判据是「陈旧」，不是「有没有值」

只报 `last_ok` 的话，读的人还得自己知道每条循环该多久跑一次 ——
那种"要懂行才看得懂"的健康信息，等于没有（docs/LOGGING.md 规则 4：
看到的人要能回答「接下来该做什么」）。所以这里直接给结论：`stale: true`。

⚠️ **刚启动不算陈旧。** 进程重启后台账是空的，而一条 6 小时的循环
本来就要等 6 小时才第一次跑。不处理这一点的话，每次部署完
`/health` 都会红一片，红几次之后就没人看了 —— 又回到原点。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: 容忍几倍于节奏才算陈旧。3 倍 —— 漏一次是抖动，连漏三次是坏了。
STALE_FACTOR = 3.0
#: 再急的活计也给 60 秒宽限，避免秒级循环一抖就报警
STALE_FLOOR_S = 60.0


@dataclass
class _Job:
    every_s: float
    #: 墙钟，给人看的
    last_ok_wall: float | None = None
    #: 单调钟，算年龄用 —— 系统时钟被改也不受影响
    last_ok_mono: float | None = None
    count: int = 0


_LOCK = threading.Lock()
_JOBS: dict[str, _Job] = {}
#: 进程起来的时刻（单调钟）。用来回答"还是刚启动，不是停了"
_STARTED = time.monotonic()


def declare(job: str, *, every_s: float) -> None:
    """登记一条后台活计和它该有的节奏。**启动时调一次。**

    要先声明再 beat：只在 beat 时才登记的话，一条**从没成功过**的循环
    在台账里根本不存在 —— 而那恰恰是最该看见的一种坏。
    """
    with _LOCK:
        job_obj = _JOBS.get(job)
        if job_obj is None:
            _JOBS[job] = _Job(every_s=every_s)
        else:
            job_obj.every_s = every_s


def beat(job: str) -> None:
    """记一次**成功**。放在活计真的做完之后，不要放在 try 的开头。"""
    now_mono = time.monotonic()
    with _LOCK:
        j = _JOBS.get(job)
        if j is None:
            # 没声明过也收下，但要说一声 —— 多半是漏了 declare，
            # 那样 stale 判定会用一个瞎猜的节奏
            logger.warning("heartbeat: %s 没有 declare 过，按 15 分钟节奏算", job)
            j = _Job(every_s=900.0)
            _JOBS[job] = j
        j.last_ok_wall = time.time()
        j.last_ok_mono = now_mono
        j.count += 1


def reset() -> None:
    """清空。**只给测试用。**"""
    global _STARTED
    with _LOCK:
        _JOBS.clear()
        _STARTED = time.monotonic()


def snapshot() -> dict[str, dict]:
    """给 `/health` 看的台账。"""
    now_mono = time.monotonic()
    uptime = now_mono - _STARTED
    out: dict[str, dict] = {}
    with _LOCK:
        for name, j in _JOBS.items():
            tol = max(j.every_s * STALE_FACTOR, STALE_FLOOR_S)
            if j.last_ok_mono is None:
                # 从没成功过。**刚起来不算坏** —— 一条 6 小时的循环
                # 本来就要等 6 小时；这时候报红只会让人学会忽略它。
                stale = uptime > tol
                age = None
            else:
                age = now_mono - j.last_ok_mono
                stale = age > tol
            out[name] = {
                "every_s": round(j.every_s, 1),
                "last_ok": (
                    datetime.fromtimestamp(j.last_ok_wall, timezone.utc).isoformat()
                    if j.last_ok_wall else None
                ),
                "age_s": round(age, 1) if age is not None else None,
                "count": j.count,
                "stale": stale,
            }
    return out


def stale_jobs() -> list[str]:
    """哪些活计停了。给 `/health` 的总判定和看门狗用。"""
    return sorted(k for k, v in snapshot().items() if v["stale"])
