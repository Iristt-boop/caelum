#!/usr/bin/env python3
"""日志异常摘要 —— 把「没人看的日志」变成「每天一行」。

## 起因

糖糖 2026-09-08：「有日志有警告，没人看是个问题吧？」

那天量出来的代价：
  - utility 全线 401 **三十多个小时**（压缩没在压、理解层没在推断），
    因为主聊天照常，表面看不出来
  - bridge 的「未知型号按 Sonnet 估价」喊了 **8728 次**，账单虚高 27 倍
  - galatea 接线的 TypeError 一直在刷

三件都躺在日志里，一条都没被看见。

## 设计的三条

**① 只报「新出现」和「突增」，不报存量。**
这是这类东西唯一的生死线 —— 每天原样报一遍存量，三天后她就不看了，
那就退化成了另一种墙纸（同 `local_link` 抢链路告警那条：警报天天响就没人看）。

**② 必须有静音名单，而且要好加。**
总有一些是正常噪音（启动提示、她主动打断的请求）。
没有静音，第 ① 条迟早被绕过去 —— 因为「新出现」的噪音也是噪音。

**③ 不用 LLM。**
今天挂的正是 utility。**一个和被监控对象共享故障模式的监控不是监控。**
而且瓶颈从来不是「看不懂」，是「没人看」—— 先解决推到面前。

**④ 每条都带「最后一次是什么时候」。**
第一版没有，我立刻就被自己坑了：看见 register_all 崩了 60 次冲过去查，
查完发现是三小时前部署窗口里的，早好了。**「还在烧」和「烧过了」是两件事**，
不写出来的话这张卡每天都在让人追尸体。

## 用法

    log-digest.py                # 出摘要（给晨检卡用）
    log-digest.py --full         # 连存量一起列（人工排查时用）
    log-digest.py --mute "xxx"   # 把含这个子串的指纹静音掉
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

STATE = Path("/root/.log-digest-state.json")
MUTE = Path("/root/.log-digest-mute.txt")

#: 结构化产出。锁屏卡的 body **硬卡在 260 字**（morning-check.sh 那行 `body[:260]`），
#: 塞不下细节 —— 所以卡上只放一句结论，正文落在这儿，
#: 由 nox-core `/api/nox/logs/digest` 读出去给 Caelum OS 的 Advanced 页。
#: 糖糖 09-06 要的就是这个：「在 OS 加个我能看到的状态和每天跑的晨检卡还有日志」。
OUT = Path("/root/nox-core/data/log-digest.json")

#: 扫哪些服务。**每家日志格式不同**，所以规则跟着服务走，
#: 不能一把 grep 打天下（nox-core 是 Python logging、bridge 是 console.error）。
UNITS = ("nox-core", "bridge", "ombre-brain", "co-reading",
         "co-watching", "eryu", "netease-mcp")

#: 什么算「问题行」。宁可多收一点 —— 漏掉的看不见，多收的能静音。
_BAD = re.compile(
    r"(WARNING|ERROR|CRITICAL|Traceback|Exception|Error:|"
    r"失败|错误|异常|挂了|不可用|超时|拒绝)",
    re.IGNORECASE,
)

#: 一定不是问题的（这些词在正常日志里也天天出现）
_NOT_BAD = re.compile(r"(INFO\b.*成功|没有失败|error=None|errors: 0)")

#: 🔴 traceback 的**中间层是噪音，末行才是信号**。
#: 第一版没滤，48 小时里 76 条 `Traceback (most recent call last):` 和一堆
#: `File "…", line N, in wrapped_app` 占掉了榜单前排，而它们完全不区分故障 ——
#: 同一个异常换个调用栈就分裂成好几条，不同异常的栈顶又长得一模一样。
#: 真正认得出「是什么坏了」的只有最后那行 `XxxError: …`，它照常被收进来。
_FRAME = re.compile(
    r"^\s*(Traceback \(most recent call last\)|File \"|\.{3}|"
    r"await |raise |return |exec\(|yield |with |async |self\.|"
    r"[a-z_]+ = |[A-Za-z_.]+\(.*\)$)"
)

#: journalctl `-o short-iso` 的行首：时间 + 主机 + 单元[pid]:
_PREFIX = re.compile(r"^(\S+)\s+\S+\s+\S+?\[\d+\]:\s?")

#: 归一：把变量部分抹掉，让同一类问题收敛成一个指纹。
#: 这套规则就是 2026-09-08 排查时手敲的那条 sed 的正式版 ——
#: 它当场把 683 条压成了 8 类，分得很准。
_NORM = (
    (re.compile(r"0x[0-9a-fA-F]+"), "X"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "X"),          # uuid / session / hash
    (re.compile(r"\b\d[\d,.]*\b"), "N"),             # 一切数字
    (re.compile(r"['\"][^'\"]{40,}['\"]"), "'…'"),   # 长字符串（多半是内容不是类型）
    (re.compile(r"\s+"), " "),
)

#: 指纹留多长。太长会让同类因为尾巴不同而分裂成几条
FP_LEN = 90

#: 少于这个行数就当它是「哑巴」。48 小时窗口下，正常服务至少几百行
QUIET_LINES = 20

#: 单个服务最多看多少行。**这是防 OOM 的硬闸**（2026-09-08 那次把整机干趴了）。
#: 这台 4GB 没 swap，宁可少看一点也不能再把机器拖死 ——
#: 扫描器自己成为故障源是最糟的结果。
#: 30 万行按每行 ~150 字节算，峰值也就几十 MB。
MAX_LINES = 300_000


def _when(ts: str) -> str:
    """把 ISO 时间戳说成人话。**「还在烧」和「烧过了」是两件事。**"""
    if not ts or ts == "?":
        return "?"
    try:
        from datetime import datetime
        #: 🔴 journalctl 给的是 `+0800`（不带冒号），**Python 3.10 的
        #: fromisoformat 不认**（3.11 才放宽）。线上就是 3.10 ——
        #: 不补这个冒号，每条都会静静掉进 except 里退回原始时间戳，
        #: 「3小时前」这个最有用的信息就没了，而且一声不吭。
        iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts)
        dt = datetime.fromisoformat(iso)
        mins = (datetime.now(dt.tzinfo) - dt).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return ts[5:16]
    if mins < 90:
        return f"{mins:.0f}分钟前"
    if mins < 60 * 36:
        return f"{mins / 60:.0f}小时前"
    return f"{mins / 1440:.0f}天前"


def fingerprint(line: str) -> str:
    s = line.strip()
    for pat, rep in _NORM:
        s = pat.sub(rep, s)
    return s[:FP_LEN].strip()


def collect(unit: str, since: str) -> tuple[list[tuple[str, str]], int]:
    """取一个服务的问题行，返回 [(时间, 正文)] 和该服务的总行数。

    总行数是给「哑巴检测」用的 —— `co-reading` 和 `netease-mcp` 48 小时
    只写一行，那不是它们没出错，是它们**根本不说话**。一个不说话的服务
    在这张卡上永远是干净的，这比它真的坏掉更危险。

    ## 🔴 必须流式读，不许 `capture_output=True`

    2026-09-08 血案：第一版用 `subprocess.run(capture_output=True)`，
    它**把整份 journalctl 输出一次性塞进内存**，`.splitlines()` 再翻一倍。
    nox-core 48 小时的日志有几十万行 —— 内核 OOM 杀掉了一个
    `anon-rss:370986kB` 的 python3，那就是这个脚本。

    这台是 4GB **而且没配 swap**（`Total swap = 0kB`），
    连 systemd-journald 都被拖到 watchdog 崩溃，整机 SSH/HTTP 全不通。

    **一个为了「早点发现问题」而写的东西，自己成了当天最大的故障。**
    所以这里逐行读、读完就扔，另外加一道行数硬闸 —— 两道都要，
    因为流式只保证峰值低，不保证跑得完。
    """
    proc = None
    rows: list[tuple[str, str]] = []
    total = 0
    try:
        proc = subprocess.Popen(
            ["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "short-iso"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace", bufsize=1,
        )
        for raw in proc.stdout:  # type: ignore[union-attr]
            total += 1
            if total > MAX_LINES:
                #: 说出来。悄悄截断会让「昨天很干净」变成一句谎话
                rows.append(("?", f"[log-digest] {unit} 日志超过 {MAX_LINES} 行，只看了前面这些"))
                break
            m = _PREFIX.match(raw)
            ts, body = (m.group(1), raw[m.end():].rstrip("\n")) if m else ("?", raw.rstrip("\n"))
            if _FRAME.match(body) or not _BAD.search(body) or _NOT_BAD.search(body):
                continue
            rows.append((ts, body))
    except Exception as exc:  # noqa: BLE001
        # 🔴 读不到某个服务的日志本身就是个问题，要说出来，不能静默跳过
        rows.append(("?", f"[log-digest] 读不到 {unit} 的日志: {exc}"))
    finally:
        if proc is not None:
            #: 提前 break 的话 journalctl 还在往管道里写 —— 不 kill 会留下
            #: 一个写满就阻塞的僵尸进程，下次跑再来一个
            try:
                if proc.stdout:
                    proc.stdout.close()
                proc.kill()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
    return rows, total


def muted() -> list[str]:
    if not MUTE.exists():
        return []
    return [ln.strip() for ln in MUTE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        # 状态坏了最坏的后果是今天全报成「新出现」—— 比不报强
        return {}


def _iso(ts: str) -> str:
    """`+0800` → `+08:00`。

    🔴 journalctl 给的偏移量不带冒号，**JS 的 `new Date()` 不保证认**
    （ES 规范要求带冒号，浏览器宽容度各不相同）。写进 JSON 前补掉，
    否则 Advanced 页上那行会变成 `Invalid Date` —— 而且不报错，
    只是安静地显示错的时间。
    """
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts or "")


def _write_out(today, fresh, spiked, quiet, since) -> None:
    """落结构化产出给 Advanced 页读。

    ⚠️ **写失败不能让这次巡检算失败** —— 巡检本身已经跑完、结论已经打在
    journal 里了。为了一个展示用的文件把退出码弄成非零，cron 会天天发告警邮件，
    那正好是这个脚本要消灭的东西。
    """
    from datetime import datetime
    payload = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": since,
        "total_kinds": len(today),
        #: `last` 一律发**绝对时间**，相对量（「3 小时前」）交给前端现算 ——
        #: 这份文件早上 08:00 写，她可能晚上才点开，
        #: 在这儿把「3 小时前」冻进 JSON 就是往页面上写假话
        "fresh": [{"n": n, "key": k, "last": _iso(e["last"]), "sample": e["sample"]}
                  for n, k, e in fresh],
        "spiked": [{"n": n, "was": w, "key": k, "last": _iso(e["last"]), "sample": e["sample"]}
                   for n, w, k, e in spiked],
        "quiet": [{"unit": u, "lines": n} for u, n in quiet],
        #: 全量也带上 —— Advanced 页要能「展开看全部」，
        #: 不然想查点什么还是得 ssh，那这页就白做了
        "all": sorted(
            ({"n": e["n"], "key": k, "last": _iso(e["last"])} for k, e in today.items()),
            key=lambda x: -x["n"],
        ),
    }
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[log-digest] 摘要写不出去（Advanced 页会看到旧的）：{exc}", file=sys.stderr)


def _self_limit(mb: int = 256) -> None:
    """给自己套一个内存上限。

    🔴 这不是优化，是**保命**。2026-09-08 这个脚本吃到 370MB，
    在一台 4GB、`Total swap = 0kB` 的机器上触发内核 OOM ——
    被杀的不止它自己，systemd-journald 也被拖到 watchdog 崩溃，
    整机 SSH / HTTP 全不通，只能去云控制台硬重启。

    上面已经改成流式读了，正常跑几十 MB 到不了这儿。
    这道闸守的是**下一个 bug**：撞上限的话这个进程自己
    `MemoryError` 死掉，晨检卡少一行 —— 而机器还活着。
    两者不是一个量级的事故。
    """
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        cap = mb * 1024 * 1024
        if hard != resource.RLIM_INFINITY:
            cap = min(cap, hard)
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    except Exception:  # noqa: BLE001
        #: Windows 上没有 RLIMIT_AS —— 本机跑 --help 不该因此报错
        pass


def main() -> int:
    _self_limit()
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="24 hours ago")
    ap.add_argument("--full", action="store_true", help="连存量一起列")
    ap.add_argument("--mute", help="把含这个子串的指纹静音掉")
    ap.add_argument("--reset", action="store_true", help="清空基线（下次全算新的）")
    args = ap.parse_args()

    if args.mute:
        with MUTE.open("a", encoding="utf-8") as f:
            f.write(args.mute.strip() + "\n")
        print(f"已静音：{args.mute}")
        return 0
    if args.reset:
        STATE.unlink(missing_ok=True)
        print("基线已清空")
        return 0

    mute = muted()
    today: dict[str, dict] = {}
    quiet = []
    for unit in UNITS:
        rows, total = collect(unit, args.since)
        if total <= QUIET_LINES:
            quiet.append((unit, total))
        for ts, line in rows:
            fp = fingerprint(line)
            if not fp or any(m in fp for m in mute):
                continue
            key = f"{unit}｜{fp}"
            e = today.setdefault(key, {"n": 0, "last": "", "sample": ""})
            e["n"] += 1
            #: 时间戳是 ISO，字符串比大小就是比时间，不用 parse。
            #: **样本跟着时间一起换成最新的** —— 留第一条的话，卡上会出现
            #: 「最后 65 分钟前」配一条五小时前的原文，看的人会以为自己看错了
            if ts > e["last"]:
                e["last"], e["sample"] = ts, line.strip()[:200]

    old = load_state()
    fresh, spiked = [], []
    for key, e in today.items():
        was = (old.get(key) or {}).get("n", 0)
        if was == 0:
            fresh.append((e["n"], key, e))
        #: 突增判据：翻 3 倍**且**绝对量够。只看倍数的话，
        #: 1 条变 4 条也会报，那种噪音会把真信号淹掉
        elif e["n"] >= max(20, was * 3):
            spiked.append((e["n"], was, key, e))

    fresh.sort(key=lambda x: -x[0])
    spiked.sort(key=lambda x: -x[0])

    if args.full:
        print(f"=== 全部（{len(today)} 类）===")
        for key, e in sorted(today.items(), key=lambda kv: -kv[1]["n"]):
            print(f"{e['n']:6d}  {_when(e['last']):>7}  {key}")
        print()

    if not fresh and not spiked:
        print("日志✓ 没有新问题")
    else:
        parts = []
        if fresh:
            parts.append(f"新增 {len(fresh)} 类")
        if spiked:
            parts.append(f"突增 {len(spiked)} 类")
        print("日志⚠️ " + "、".join(parts))
        for n, key, e in fresh[:5]:
            print(f"  [新] {n:>5} 次 · 最后 {_when(e['last'])}  {key}")
            print(f"        {e['sample'][:120]}")
        for n, was, key, e in spiked[:3]:
            print(f"  [增] {was}→{n} 次 · 最后 {_when(e['last'])}  {key}")
            print(f"        {e['sample'][:120]}")

    #: 哑巴服务单独说 —— 它在上面永远是干净的，不点名就等于默认它没事
    if quiet:
        print("日志🔇 " + "、".join(f"{u}({n}行)" for u, n in quiet) + " 几乎不写日志，坏了也看不见")

    _write_out(today, fresh, spiked, quiet, args.since)

    #: 今天的计数存成明天的基线。**报完再存** ——
    #: 先存的话这一轮的「新出现」会被自己盖掉，永远报不出来
    STATE.write_text(json.dumps(today, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
