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

    def poll(self, book: Any) -> list[str]:
        """没通知过的活挨个问一遍；收尾了就往唤醒本放一张任务纸条。两分钟最多问一次。"""
        now = self.now()
        if self._last_poll and now - self._last_poll < POLL_EVERY:
            return []
        self._last_poll = now
        with self._lock:
            jobs = self._load()
            open_ = [j for j in jobs.values() if not j.get("notified")]
            if not open_:
                return []
            ip = self.pc.find_ip()
            if not ip:
                return []           # 关着就下回再问；她开机了自然接上
            told = []
            for j in open_:
                d = self._ssh(ip, f"status {j['id']}")
                state = ((d.get("state") or {}).get("state")) if d.get("ok") else None
                if state not in FINAL:
                    continue
                if j.get("session_id"):
                    book.add_routine(j["session_id"], wake_note(j, d), now)
                j["notified"] = True
                j["state"] = state
                told.append(j["id"])
            if told:
                self._save(jobs)
                logger.info("pc_tasks: 收尾了 %s，放了纸条", "、".join(told))
            return told


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
    return "\n".join(lines)


def wake_note(job: dict, d: dict) -> str:
    """醒来的他只看得到这张纸条 —— 写清楚是什么活、结果如何、下一步归谁。"""
    return (f"【电脑上的活·{job['id']}】你交给她电脑上 Claude Code 的活收尾了。她当时交代的：{job.get('task', '')[:200]}\n"
            f"{describe(d)}\n"
            "用你自己的话告诉她结果（别贴代码、别念提交号）；没做成就如实说卡在哪。"
            "要上线得她亲口说，别替她决定。")


def register_all(loop, tasks: PcTasks) -> None:
    """三个工具，顺序固定（工具定义是缓存前缀的一部分）。"""

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
