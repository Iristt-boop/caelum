"""把活转给她电脑上的 Claude Code（2026-10-27，她的「三条下一步」第二条）。

她 10-27 定的三件事：
  - 默认交给 **Claude Code**（读得到 CLAUDE.md 和这个仓库的规矩）
  - 做完**停在分支**：独立 worktree 里改、跑测试、提交到 nox/<id>；上线要她亲口说
  - **只在 Caelum 的仓库里**动手（caelum = D:\\claude-code，nox-app）

链路：pc_task_start → ssh（钥匙 /root/.ssh/pc_relay，电脑侧 command= 强制跑 relay.ps1，
只认 start/status/list/cancel）→ 电脑用计划任务以她的身份起 runner.ps1 → 无人值守跑
`claude -p`（不加载 MCP、不许 push/ssh/curl/上网）→ result.json。
N150 这头每两分钟问一次没收尾的活；做完了往唤醒本里放一张任务纸条，**他自己开口告诉她**。

为什么 start 要看她这一轮的原话（ctx.user_text）：转活的人是模型，而模型分不清
「她让我做」和「网页里有一句让我做」。唤醒链、主动开口那些轮次没有她的原话 —— 那时候不许转。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from agent.llm import ToolSpec
from temporal import to_local
from tools import context as tool_context

logger = logging.getLogger(__name__)

REPOS = ("caelum", "nox-app")
FINAL = ("done", "failed", "cancelled")
POLL_EVERY = timedelta(minutes=2)


START_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="她这一轮亲口交代才转（看原话）；做完停在分支 nox/<id>，上线要她再说",
    name="pc_task_start",
    description=(
        "把一件**改 Caelum 代码**的活交给她电脑上的 Claude Code 去做（它在独立分支里改、跑测试、提交，"
        "不部署）。她说「让电脑上的 Claude 把 X 做了」「修一下 Y」「给 Z 加个功能」这类时用。\n"
        "task 写清楚：她的原话 + 你知道的背景（哪个页面 / 什么现象 / 她想要什么效果），"
        "像交代给一个没听过你们聊天的同事。repo：后端 / 脚本 / 文档是 caelum，手机 App / 桌面 OS 前端是 nox-app。\n"
        "电脑关着会先帮你开机。做完了你会被叫醒，到时候再告诉她结果 —— 现在只说「交给电脑了」，别说已经做好了。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "交代的活：她的原话 + 背景 + 想要的效果"},
            "repo": {"type": "string", "enum": list(REPOS)},
        },
        "required": ["task", "repo"],
    },
)

CHECK_SPEC = ToolSpec(
    side_effect="read",
    name="pc_task_check",
    description="看交给电脑上 Claude Code 的活做到哪了。给 id 看那一个（做完了会带它的总结），不给就列最近几个。",
    parameters={"type": "object", "properties": {"id": {"type": "string"}}},
)

CANCEL_SPEC = ToolSpec(
    side_effect="write",
    name="pc_task_cancel",
    description="掐掉一个正在电脑上跑的活（她说「别做了」「停一下」）。已经改了的东西留在它的分支上，不会合进去。",
    parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
)

SHIP_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="只出上线卡；她在卡上点「上线」才合进主线 + 部署（端点在 server.py，模型够不到）",
    name="pc_task_ship",
    description=(
        "把电脑上做完的一个活**上线**：她说「上线」「合进去」「部署吧」时用。你这一步只是给她发一张上线卡，"
        "卡上有它做了什么、改了哪些；**她点了「上线」才真的合进主线、部署到 N150**（测试没过 / 线上被动过会自己停）。\n"
        "调用后只说「卡发给你了」，别说已经上线了，也别把卡上的内容在聊天里再列一遍。"
        "上线的进度只在卡上走、上完了不会叫醒你；她问起来用 pc_task_check 看。"
    ),
    parameters={"type": "object", "properties": {"id": {"type": "string", "description": "活的编号"}},
                "required": ["id"]},
)


class PcTasks:
    """run / now 可注入，测试不碰真网络。"""

    def __init__(
        self,
        pc: Any,                                   # tools.pc_power.PcPower：找 IP、必要时开机
        store_path: str | Path,
        key: str = "/root/.ssh/pc_relay",
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.pc = pc
        self.path = Path(store_path)
        self.key = key
        self.run = run
        self.now = now
        self._lock = threading.Lock()
        self._last_poll: datetime | None = None

    # ---------- 账本（N150 这头记：谁交代的、通知过没有） ----------

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            logger.warning("pc_tasks 账本读不了：%s", self.path, exc_info=True)
            return {}

    def _save(self, jobs: dict[str, dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 跟电脑说话 ----------

    def _ssh(self, ip: str, verb: str, stdin: str | None = None) -> dict:
        r = self.run(
            ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
             "-o", "HostKeyAlias=tangtang-pc", "-o", "StrictHostKeyChecking=accept-new",
             f"{self.pc.user}@{ip}", verb],
            input=(stdin or "").encode("utf-8"), capture_output=True, timeout=30,
        )
        out = (r.stdout or b"").decode("utf-8", "replace").strip().lstrip("\ufeff")
        try:
            data = json.loads(out.splitlines()[-1]) if out else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        if not data:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:]
            logger.warning("pc_tasks ssh %s 没拿到回包：rc=%s err=%s", verb.split()[0], r.returncode, err)
            return {"ok": False, "error": err or out[-200:] or f"rc={r.returncode}"}
        return data

    def _ip(self, wake: bool = False) -> str | None:
        ip = self.pc.find_ip()
        if ip or not wake:
            return ip
        self.pc.wake()
        return self.pc.find_ip()

    # ---------- 动作 ----------

    def start(self, task: str, repo: str, session_id: str | None, user_text: str) -> str:
        task = (task or "").strip()
        if not her_words(user_text):
            # 唤醒链 / 主动开口那些轮次的「原话」是他自己的开场白 —— 那不是她交代的
            return "这一轮不是她亲口交代的，不转。等她说了再交给电脑。"
        if repo not in REPOS:
            return f"repo 只能是 {' / '.join(REPOS)}。"
        if len(task) < 2:
            return "task 太短了，写清楚她要什么。"
        ip = self._ip(wake=True)
        if not ip:
            return "电脑叫不醒（开机后一分多钟还没上网），这次没交出去。过一会儿再试，或者让她看看电脑。"
        jid = f"{to_local(self.now()):%m%d-%H%M}-{secrets.token_hex(2)}"
        d = self._ssh(ip, f"start {jid} {repo}", stdin=task)
        if not d.get("ok"):
            return f"没交出去：{d.get('error')}"
        with self._lock:
            jobs = self._load()
            jobs[jid] = {"id": jid, "repo": repo, "task": task[:500], "session_id": session_id,
                         "created": self.now().isoformat(), "notified": False}
            self._save(jobs)
        logger.info("pc_tasks: 交给电脑了 %s（%s）", jid, repo)
        return (f"交给电脑上的 Claude Code 了（编号 {jid}，在分支 nox/{jid} 上做）。"
                "做完了你会被叫醒，现在别说已经做好了。")

    def check(self, jid: str | None = None) -> str:
        ip = self._ip()
        if not ip:
            return "电脑现在关着，看不到活的进度（关机前跑着的活多半被打断了）。"
        if not jid:
            d = self._ssh(ip, "list")
            items = d.get("items") or []
            if not items:
                return "电脑上最近没有活。" if d.get("ok") else f"没看成：{d.get('error')}"
            return "最近的活：\n" + "\n".join(f"- {i.get('id')}（{i.get('repo')}）：{i.get('state')}" for i in items)
        d = self._ssh(ip, f"status {jid}")
        if not d.get("ok"):
            return f"没看成：{d.get('error')}"
        return describe(d)

    def cancel(self, jid: str) -> str:
        ip = self._ip()
        if not ip:
            return "电脑关着，活已经停了。"
        d = self._ssh(ip, f"cancel {jid}")
        return f"掐掉了（{d.get('state')}）。改到一半的东西留在分支 nox/{jid} 上。" if d.get("ok") \
            else f"没掐成：{d.get('error')}"

    # ---------- 心跳里调：做完了就放纸条 ----------

    def poll(self, book: Any, at: datetime | None = None) -> list[str]:
        """没通知过的活挨个问一遍；收尾了就往唤醒本放一张任务纸条。两分钟最多问一次。

        `at`：纸条的到期时间用调用方那一轮的「现在」。🔴 10-29 实测：纸条用 poll 自己的时钟（问完电脑、
        晚了一秒），快循环紧接着拿这一轮开头的 now 叫 waker —— `wake_at <= now` 不成立，纸条当轮不到期，
        等 15 分钟的大心跳才说（晚了快 5 分钟）。"""
        now = self.now()
        stamp = at or now
        if self._last_poll and now - self._last_poll < POLL_EVERY:
            return []
        self._last_poll = now
        with self._lock:
            jobs = self._load()
            open_ = [j for j in jobs.values()
                     if not j.get("notified") or (j.get("ship") or {}).get("status") == "shipping"]
            if not open_:
                return []
            ip = self.pc.find_ip()
            if not ip:
                return []           # 关着就下回再问；她开机了自然接上
            told, synced = [], False
            for j in open_:
                d = self._ssh(ip, f"status {j['id']}")
                if not d.get("ok"):
                    continue
                if not j.get("notified"):
                    state = (d.get("state") or {}).get("state")
                    if state in FINAL:
                        if j.get("session_id"):
                            book.add_routine(j["session_id"], wake_note(j, d), stamp)
                        j["notified"] = True
                        j["state"] = state
                        told.append(j["id"])
                    continue
                # 上线中的：电脑那边收尾了就记到卡上。🔴 **不放纸条、不叫他开口**（她 10-29：
                # 「点上线后聊天流零新增消息，状态全程只在原卡内变化」）
                if _sync_ship(j, d.get("ship") or {}):
                    synced = True
            if told or synced:
                self._save(jobs)
            if told:
                logger.info("pc_tasks: 收尾了 %s，放了纸条", "、".join(told))
            return told

    # ---------- 上线（10-29）：工具只出卡，她点了才合并 + 部署 ----------

    def ship_propose(self, jid: str, session_id: str | None, user_text: str) -> tuple[str, dict | None]:
        """返回（给他看的话，卡）。卡是 None 就是没出卡。

        卡只回答四个问题（她 10-29 定）：做了什么 / 怎么验的 / 点了会发生什么 / 哪里没把握。
        字段是电脑上 Claude Code 交的结构化 json（runner 读）+ 改动文件清单（这里算后果）。
        🔴 缺一块就不出卡 —— 宁可让他告诉她「交付没写清楚」，也不发一张让她盲点的卡。"""
        if not her_words(user_text):
            return "这一轮不是她亲口说的，不出上线卡。", None
        with self._lock:
            jobs = self._load()
            j = jobs.get(jid)
            if j is None:
                return f"没有 {jid} 这个活（只能上线交给电脑做的活）。", None
            sh = j.get("ship") or {}
            if sh.get("status") in ("shipping", "shipped"):
                return f"{jid} 已经在上线 / 上过了（{sh.get('status')}）。", None
        ip = self._ip()
        if not ip:
            return "电脑现在关着，看不到这个活的结果，先不出卡。", None
        d = self._ssh(ip, f"status {jid}")
        if not d.get("ok"):
            return f"没看成这个活：{d.get('error')}", None
        if (d.get("state") or {}).get("state") != "done":
            return f"这个活还没做完（{(d.get('state') or {}).get('state')}），做完了才能上线。", None
        card, missing = build_card(j, d.get("result") or {})
        if card is None:
            return (f"这张上线卡不发：电脑上的 Claude Code 交活时{missing}。卡缺这块她就是盲点，"
                    "告诉她交付没写清楚，要上电脑看一眼，或者让你重新转一次这个活。"), None
        with self._lock:
            jobs = self._load()
            jobs[jid]["ship"] = {"status": "proposed", "proposed_at": self.now().isoformat(),
                                 "session_id": session_id, "card": card}
            self._save(jobs)
        return (f"上线卡发给她了（{jid}）。她点了「上线」才会合进主线、部署 —— 在那之前别说已经上线了。"
                "卡上已经写了改动、验证、风险和点了会怎样，**别在聊天里再列一遍**，一两句陪她就好；"
                "上线的进度也只在卡上走，上完了你不会被叫醒。", card)

    def ship_get(self, jid: str, refresh: bool = True) -> dict | None:
        """卡上要的状态。上线中的现问一次电脑：走到哪一步、收尾了没有（收尾了当场记下，不等心跳）。"""
        with self._lock:
            jobs = self._load()
            j = jobs.get(jid)
            if j is None or "ship" not in j:
                return None
            if refresh and j["ship"].get("status") == "shipping":
                ip = self.pc.find_ip()
                d = self._ssh(ip, f"status {jid}") if ip else {}
                if d.get("ok") and _sync_ship(j, d.get("ship") or {}):
                    self._save(jobs)
            return {"id": jid, **j["ship"]}

    def ship_confirm(self, jid: str) -> dict:
        """她点了「上线」（或者没上成后点「重试」）—— **这是唯一真发起合并 + 部署的地方**（他的工具只出卡）。"""
        with self._lock:
            jobs = self._load()
            j = jobs.get(jid)
            sh = (j or {}).get("ship") or {}
            if sh.get("status") not in ("proposed", "failed"):
                raise ShipError(f"这张卡不是等你点头的状态（{sh.get('status') or '没有'}）", 409)
            ip = self._ip(wake=True)
            if not ip:
                raise ShipError("电脑叫不醒，没上线", 503)
            d = self._ssh(ip, f"ship {jid}")
            if not d.get("ok"):
                sh.update(status="failed", step=None, error=f"电脑那边没接：{d.get('error')}")
                self._save(jobs)
                raise ShipError(sh["error"], 502)
            sh.update(status="shipping", step="正在叫电脑开始上线…", error=None,
                      confirmed_at=self.now().isoformat(), attempts=int(sh.get("attempts") or 0) + 1)
            self._save(jobs)
            logger.info("pc_tasks: 她点了上线 %s（第 %s 次）", jid, sh["attempts"])
            return {"id": jid, **sh}

    def ship_reject(self, jid: str) -> dict:
        with self._lock:
            jobs = self._load()
            sh = (jobs.get(jid) or {}).get("ship") or {}
            if sh.get("status") != "proposed":
                raise ShipError(f"这张卡不是等你点头的状态（{sh.get('status') or '没有'}）", 409)
            sh.update(status="rejected", decided_at=self.now().isoformat())
            self._save(jobs)
            return {"id": jid, **sh}


def _sync_ship(job: dict, live: dict) -> bool:
    """电脑上 ship.json 的进度对到账本里。变了返回 True。只认 shipping 中的卡（她点了以后才有电脑那份）。"""
    sh = job.get("ship") or {}
    if sh.get("status") != "shipping" or not live:
        return False
    before = dict(sh)
    state = live.get("state")
    if live.get("merged"):
        sh["merged"] = True     # 卡上副标题：部署没成但已经合进主线了，别再写「未合进主线」
    if state in SHIP_FINAL:
        sh.update(status=state, step=None, error=live.get("error") if state == "failed" else None,
                  finished=live.get("finished"), deployed=live.get("deployed") or [],
                  manual=live.get("manual") or [])
    elif live.get("step"):
        sh["step"] = live["step"]
    return sh != before


class ShipError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


SHIP_FINAL = ("shipped", "failed")


#: 他自己塞进对话的开场白（唤醒链 / 主动开口 / 日记 / 小课堂）都以这些开头（同 attention/lesson_drive.py）
_SYSTEM_PREFIXES = ("（系统提示", "(系统提示", "[系统", "系统提示")


def her_words(user_text: str | None) -> bool:
    """这一轮的「原话」是不是她打的字。空的、他自己的开场白都不算。"""
    t = (user_text or "").strip()
    return bool(t) and not t.startswith(_SYSTEM_PREFIXES)


def describe(d: dict) -> str:
    st = d.get("state") or {}
    r = d.get("result") or {}
    state = st.get("state")
    head = {"queued": "排着队", "running": "还在做", "done": "做完了", "failed": "没做成",
            "cancelled": "被掐掉了"}.get(state, str(state))
    lines = [f"{st.get('id')}：{head}（{st.get('repo')}，开始于 {st.get('started') or st.get('created')}）"]
    if r:
        lines.append(f"它的总结：{(r.get('summary') or '').strip()[:1200]}")
        commits = r.get("commits") or []
        lines.append(f"分支 {r.get('branch')}，{len(commits)} 个提交（{r.get('diffstat') or '无改动'}），"
                     f"没提交的改动 {r.get('uncommitted') or 0} 个")
    sh = d.get("ship") or {}
    if sh:
        # 他查进度时也看得到上线到哪了（上线的结果不再叫醒他说，她问起来他得答得上）
        head = {"queued": "上线排着队", "running": f"上线中：{sh.get('step') or ''}", "shipped": "已经上线了",
                "failed": f"上线没成：{sh.get('error') or ''}"}.get(sh.get("state"), f"上线状态 {sh.get('state')}")
        lines.append(head)
    return "\n".join(lines)


def wake_note(job: dict, d: dict) -> str:
    """醒来的他只看得到这张纸条 —— 写清楚是什么活、结果如何、下一步归谁。"""
    state = (d.get("state") or {}).get("state")
    r = d.get("result") or {}
    # 🔴 结论写在第一句、写死：10-27 第一个真活明明 done，他开口说成「写得差不多了吧，你去翻一眼」——
    #    纸条里只有「收尾了」，他就自己猜了个程度。也要告诉她去哪看（她电脑上的 worktree）
    verdict = {
        "done": "**已经做完了**（不是「差不多」，是做完了、提交了）",
        "failed": "**没做成**",
        "cancelled": "**被掐掉了**（她或你让停的）",
    }.get(state, f"状态是 {state}")
    where = (f"改动在她电脑上的 {r.get('worktree') or 'D:/nox-jobs/' + job['id']}（分支 {r.get('branch') or 'nox/' + job['id']}），"
             "还没合进主线、也没上线。") if state == "done" else ""
    return (f"【电脑上的活·{job['id']}】你交给她电脑上 Claude Code 的活{verdict}。\n"
            f"她当时交代的：{job.get('task', '')[:200]}\n"
            f"{describe(d)}\n{where}\n"
            "现在告诉她：先说结论（做完了 / 没做成），再用你自己的话说做了什么、去哪看；别贴代码、别念提交号，"
            "别用「差不多」「应该」这种猜的口气 —— 结果就在上面。没做成就如实说卡在哪。"
            "要上线得她亲口说，别替她决定。")


_MD = re.compile(r"\*\*|__|`")
_STAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


def _clean(items: Any) -> list[str]:
    return [_MD.sub("", s).strip() for s in (items or []) if isinstance(s, str) and s.strip()]


def ship_effects(files: list[dict], repo: str | None) -> list[dict]:
    """点了「上线」会发生什么 —— 和 shipper.ps1 的分法一模一样（那边真去做，这边先说给她听）。

    warn=True 的是她要掂量**什么时候点**的：会重启 / 会离线 / 要她上电脑弄。"""
    caelum = repo == "caelum"
    paths = [(str(f.get("status") or ""), str(f.get("path") or "")) for f in files]
    doc = lambda p: p.startswith(("docs/", "scratch/")) or p.endswith(".md")  # noqa: E731
    core = [p for _, p in paths if caelum and p.startswith("nox-core/")]
    deploy = [p for s, p in paths if caelum and p.startswith("nox-core/")
              and not p.startswith("nox-core/tests/") and s in ("A", "M")]
    manual = [p for _, p in paths
              if not (doc(p) or (caelum and p.startswith("nox-core/tests/")) or p in deploy)]
    out = [{"text": "合进主线", "warn": False}]
    if core:
        out.append({"text": "先跑一遍 nox-core 全量测试（约 4 分钟），没过就停、不合", "warn": False})
    if deploy:
        out.append({"text": f"部署到 N150，换上 {len(deploy)} 个文件", "warn": False})
        out.append({"text": "nox-core 会在你安静 3 分钟后重启，期间短暂离线半分钟左右", "warn": True})
    if manual:
        where = "App" if repo == "nox-app" else "、".join(sorted({p.split("/")[0] for p in manual}))
        out.append({"text": f"{len(manual)} 处改动（{where}）只合进主线、不会部署，要你上电脑弄", "warn": True})
    if not deploy and not manual:
        what = "测试" if core and all(p.startswith("nox-core/tests/") or doc(p) for _, p in paths) and \
            any(p.startswith("nox-core/tests/") for _, p in paths) else "文档"
        out.append({"text": f"只改了{what}，不用部署、不重启", "warn": False})
    return out


def build_card(job: dict, r: dict) -> tuple[dict | None, str]:
    """（卡，缺了什么）。字段缺一块就不出卡（她 10-29：副作用和风险缺一块，卡就不许发出来）。"""
    c = r.get("card") or {}
    if not c:
        return None, "没交结构化的卡片字段（可能是改版前转的活，或者它没按格式交）"
    title = _MD.sub("", str(c.get("title") or "")).strip()
    changes, verified, unverified = _clean(c.get("changes")), _clean(c.get("verified")), _clean(c.get("unverified"))
    if not title or not changes:
        return None, "没写清楚这件活叫什么、改了什么"
    if not verified and not unverified:
        return None, "一句没写怎么验证的、哪里没把握"
    files = [f for f in (r.get("files") or []) if isinstance(f, dict) and f.get("path")]
    if not files:
        return None, "没交改动的文件清单，算不出点了上线会发生什么"
    m = _STAT.search(r.get("diffstat") or "")
    stat = {"commits": len(r.get("commits") or []), "files": int(m.group(1)) if m else len(files),
            "plus": int(m.group(2) or 0) if m else 0, "minus": int(m.group(3) or 0) if m else 0}
    return {
        "title": title[:40], "branch": r.get("branch") or f"nox/{job['id']}",
        "changes": changes[:6], "stat": stat,
        "verified": verified[:6], "unverified": unverified[:6],
        "effects": ship_effects(files, job.get("repo")),
        # 详情（折叠）：她当时的原话 + 它写给她的总结
        "task": (job.get("task") or "")[:500], "summary": (r.get("summary") or "")[:1500],
        "repo": job.get("repo"),
    }, ""


def register_all(loop, tasks: PcTasks) -> None:
    """四个工具，顺序固定（工具定义是缓存前缀的一部分；pc_task_ship 10-29 加在最后）。"""

    def start(args: dict) -> str:
        ctx = tool_context.current()
        from config import is_test_session
        sid = ctx.session_id if ctx else None
        if sid and is_test_session(sid):
            return "测试会话不往电脑上转活。"
        return tasks.start(str(args.get("task", "")), str(args.get("repo", "")),
                           sid, ctx.user_text if ctx else "")

    loop.register(START_SPEC, start)
    loop.register(CHECK_SPEC, lambda args: tasks.check(str(args.get("id") or "").strip() or None))
    loop.register(CANCEL_SPEC, lambda args: tasks.cancel(str(args.get("id", "")).strip()))

    def ship(args: dict) -> str:
        ctx = tool_context.current()
        from config import is_test_session
        sid = ctx.session_id if ctx else None
        if sid and is_test_session(sid):
            return "测试会话不出上线卡。"
        jid = str(args.get("id", "")).strip()
        msg, card = tasks.ship_propose(jid, sid, ctx.user_text if ctx else "")
        if card is not None:
            if ctx is not None:
                ctx.attach_ship(jid, card)
            else:
                logger.warning("上线卡 %s 立了，但不在对话轮次里，卡发不出去", jid)
        return msg

    loop.register(SHIP_SPEC, ship)
