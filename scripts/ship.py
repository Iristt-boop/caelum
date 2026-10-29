#!/usr/bin/env python3
"""只从提交部署（她 10-29：「先治线上和 git 对不上那条」，对账见 CAELUM-线上与git对账-2026-10-29.md）。

那天对账：5 个文件没提交就在线上跑、两处安全加固只在线上、版本指针停在 10-15 ——
都是「cp 一个文件上去」留下的。这个脚本把「cp 一个文件上去」换成一条有规矩的路：

  1. 只发**已提交**的内容：工作区和 HEAD 不一致就拒绝；发的是 `git show HEAD:<文件>`
  2. 换之前确认线上 == 上一次发的版本（清单里记的 sha1）；没有清单记录的，线上必须是
     git 历史里某个版本或者还不存在 —— 对不上就停：说明有人绕过脚本改过，先弄清楚再说
  3. 备份 → 换 → 更新 `/root/deployed/<服务>.json`（每个文件的 sha1 + 来自哪个提交）
  4. `--restart`：nox-core / bridge / ombre-brain 等她安静 3 分钟再换再重启（她说话时不掐断），然后等健康检查

晨检每天用 `check-drift.py` 对一遍清单，有人绕过这条路，第二天早上就知道。

用法（在仓库根目录，Git Bash）：
  python scripts/ship.py nox-core nox-core/attention/service.py nox-core/attention/speaker.py --restart
  python scripts/ship.py scripts scripts/morning-check.sh
  python scripts/ship.py --baseline nox-core      # 给现在的线上建清单（只登记和 git 一致的文件）
  python scripts/ship.py --result <id>            # 看一次等安静的部署有没有做完
  加 --dry-run 只检查不动线上。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOST = "root@192.168.1.52"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = "/root/check-drift.py"
MANIFEST_DIR = "/root/deployed"

#: 服务 → 线上目录 / 本地仓库 / 仓库内前缀 / systemd unit / 健康检查 / 重启前要不要等她安静。
#: scripts 是「平铺」的：仓库里 scripts/X、vps-scripts/X 都发到 /root/X，不扫未登记
SERVICES: dict[str, dict] = {
    "nox-core": dict(root="/root/nox-core/code", repo=".", prefix="nox-core", units=["nox-core"],
                     health="http://127.0.0.1:8100/health", quiet=True, scan=True),
    "bridge": dict(root="/root/bridge/code", repo=".", prefix="bridge", units=["bridge"],
                   health="http://127.0.0.1:3003/api/health", quiet=True, scan=True),
    "ombre-brain": dict(root="/root/ombre-brain", repo="Ombre-Brain", prefix="", units=["ombre-brain"],
                        quiet=True, scan=True, exclude_dirs=["buckets", "profile"]),
    "co-reading": dict(root="/root/co-reading-mcp", repo=".", prefix="co-reading", units=["co-reading"], scan=True),
    "co-watching": dict(root="/root/co-watching/code", repo=".", prefix="co-watching", units=["co-watching"], scan=True),
    "co-listening": dict(root="/root/co-listening", repo="co-listening", prefix="", units=["co-listening"], scan=True),
    "stackchan": dict(root="/root/stackchan-mcp", repo="stackchan-mcp", prefix="",
                      units=["stackchan-gateway", "stackchan-tracker"], scan=True),
    "touch-mcp": dict(root="/root/touch-mcp/code", repo=".", prefix="touch-mcp", units=["touch-mcp"], scan=True),
    "touch-server": dict(root="/root/touch-server/code", repo=".", prefix="fsr402-touch-server",
                         units=["touch-server"], scan=True),
    "toy-mcp": dict(root="/root/toy-mcp", repo=".", prefix="toy-mcp", units=["toy-mcp"], scan=True),
    "health-mcp": dict(root="/root/health-mcp", repo=".", prefix="health-mcp",
                       units=["health-mcp", "health-sync"], scan=True),
    "app-tracker": dict(root="/root/app-tracker", repo=".", prefix="app-tracker", units=["app-tracker"], scan=True),
    "scripts": dict(root="/root", repo=".", flat=["scripts", "vps-scripts"], units=[], scan=False),
}

QUIET_S = 180

#: 等她安静：会话库最后一条消息离现在超过 QUIET_S 秒（最多等一小时）
QUIET_BLOCK = r'''for i in $(seq 1 120); do
  idle=$(python3 -c "
import sqlite3, datetime
c = sqlite3.connect('file:/root/nox-core/data/sessions.db?mode=ro', uri=True)
t = datetime.datetime.fromisoformat(c.execute('select max(created_at) from messages').fetchone()[0])
print(int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()))")
  echo "$(date +%T) idle=$idle" >> $J/log
  [ "$idle" -ge @QUIET@ ] && break
  [ "$i" = 120 ] && fail busy
  sleep 30
done'''


class Stop(Exception):
    """该停下来让人看的情况（不是脚本 bug）。"""


def strip_dot(p: str) -> str:
    """去掉开头的 ./（别用 lstrip("./") —— 会把 .gitignore 的点也吃掉）"""
    while p.startswith("./"):
        p = p[2:]
    return p


def src_of(repo: str, in_repo: str) -> str:
    return in_repo if repo == "." else f"{repo}/{in_repo}"


def sha(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def run(cmd: list[str], data: bytes | None = None, check: bool = True) -> bytes:
    r = subprocess.run(cmd, input=data, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}… 失败（{r.returncode}）：{r.stderr.decode('utf-8', 'replace')[-400:]}")
    return r.stdout


def ssh(cmd: str, check: bool = True) -> bytes:
    return run(["ssh", "-o", "ConnectTimeout=10", HOST, cmd], check=check)


def git(repo: str, *args: str, check: bool = True) -> bytes:
    return run(["git", "-C", os.path.join(REPO, repo), "-c", "core.quotepath=false", *args], check=check)


# ---------------------------------------------------------------- 路径换算

def to_remote(svc: dict, path: str) -> tuple[str, str]:
    """仓库路径（相对外层仓库根）→ (仓库内路径, 线上相对路径)。"""
    path = strip_dot(path.replace("\\", "/"))
    repo = svc["repo"]
    in_repo = path if repo == "." else path[len(repo) + 1:] if path.startswith(repo + "/") else None
    if in_repo is None:
        raise Stop(f"{path} 不在 {repo} 仓库里")
    if "flat" in svc:
        top = in_repo.split("/", 1)[0]
        if top not in svc["flat"] or in_repo.count("/") != 1:
            raise Stop(f"{path}：scripts 只发 {'/'.join(svc['flat'])} 下面的文件")
        return in_repo, in_repo.split("/", 1)[1]
    prefix = svc["prefix"]
    if prefix and not in_repo.startswith(prefix + "/"):
        raise Stop(f"{path} 不在 {prefix}/ 下")
    return in_repo, in_repo[len(prefix) + 1:] if prefix else in_repo


def from_remote(svc: dict, rel: str) -> list[str]:
    """线上相对路径 → 仓库内候选路径（flat 服务可能在几个目录里）。"""
    if "flat" in svc:
        return [f"{d}/{rel}" for d in svc["flat"]]
    return [f"{svc['prefix']}/{rel}" if svc["prefix"] else rel]


def history_shas(repo: str, in_repo: str, limit: int = 300) -> dict[str, str]:
    """这个文件在 git 历史里每个版本的 sha1（LF）→ 提交号。"""
    out: dict[str, str] = {}
    for c in git(repo, "log", "--format=%h", "-n", str(limit), "HEAD", "--", in_repo).decode().split():
        b = git(repo, "show", f"{c}:{in_repo}", check=False)
        if b:
            out.setdefault(sha(b.replace(b"\r\n", b"\n")), c)
    return out


# ---------------------------------------------------------------- 线上

def ensure_helper() -> None:
    """线上的 check-drift.py 和 HEAD 一致才用它（它自己也是走 ship 发的；第一次没有就装）。"""
    want = git(".", "show", "HEAD:scripts/check-drift.py")
    have = ssh(f"sha1sum {HELPER} 2>/dev/null | cut -c1-40", check=False).decode().strip()
    if have == sha(want):
        return
    if have:
        raise Stop(f"线上 {HELPER} 和 HEAD 不一致 —— 先 `python scripts/ship.py scripts scripts/check-drift.py`")
    run(["ssh", HOST, f"mkdir -p {MANIFEST_DIR} && cat > {HELPER} && chmod 755 {HELPER}"], data=want)
    print(f"  装上了 {HELPER}（第一次）")


def remote_hashes(paths: list[str]) -> dict[str, dict | None]:
    if not paths:
        return {}
    return json.loads(ssh(f"python3 {HELPER} --hash " + " ".join(f"'{p}'" for p in paths)))


def load_manifest(name: str) -> tuple[dict | None, str]:
    raw = ssh(f"cat {MANIFEST_DIR}/{name}.json 2>/dev/null", check=False)
    if not raw.strip():
        return None, ""
    return json.loads(raw), sha(raw)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 发

def ship(name: str, paths: list[str], restart: bool, dry: bool, wait_s: int) -> int:
    svc = SERVICES[name]
    ensure_helper()
    manifest, manifest_sha = load_manifest(name)
    if manifest is None:
        raise Stop(f"{name} 还没有清单 —— 先 `python scripts/ship.py --baseline {name}`")

    plan = []   # (in_repo, rel, content, commit, mode)
    for p in paths:
        in_repo, rel = to_remote(svc, p)
        repo = svc["repo"]
        if not git(repo, "ls-files", "--", in_repo).strip():
            raise Stop(f"{p} 没进 git —— 先提交")
        if git(repo, "status", "--porcelain", "--", in_repo).strip():
            raise Stop(f"{p} 工作区和 HEAD 不一致 —— 先提交（只发提交过的内容）")
        content = git(repo, "show", f"HEAD:{in_repo}")
        commit = git(repo, "log", "-1", "--format=%h", "HEAD", "--", in_repo).decode().strip()
        mode = git(repo, "ls-files", "-s", "--", in_repo).decode().split()[0]
        plan.append((in_repo, rel, content, commit, mode))

    live = remote_hashes([f"{svc['root']}/{rel}" for _, rel, *_ in plan])
    todo = []
    for in_repo, rel, content, commit, mode in plan:
        h = live.get(f"{svc['root']}/{rel}")
        ent = manifest["files"].get(rel)
        if h and h["sha1"] == sha(content):
            print(f"  = {rel} 线上已经是这一版（{commit}）")
        elif ent:
            if h is None:
                raise Stop(f"{rel}：清单里有，线上却没有 —— 有人删过，先弄清楚")
            if h["sha1"] != ent["sha1"]:
                raise Stop(f"{rel}：线上和清单记的不一样（上次发的是 {ent['commit']}）—— 有人绕过 ship 改过，先对清楚")
        elif h is not None:
            hist = history_shas(svc["repo"], in_repo)
            if h["sha1_lf"] not in hist:
                raise Stop(f"{rel}：线上这份不在 git 历史里（也不在清单里）—— 线上有没提交的改动，先补提交")
        todo.append((in_repo, rel, content, commit, mode))

    new_manifest = json.loads(json.dumps(manifest))
    for in_repo, rel, content, commit, mode in todo:
        new_manifest["files"][rel] = {"sha1": sha(content), "commit": commit, "src": src_of(svc["repo"], in_repo),
                                      "at": now_iso()}
    new_manifest["updated"] = now_iso()

    for _, rel, _, commit, _ in todo:
        print(f"  → {rel}  ({commit})")
    if dry:
        print("（--dry-run：检查都过了，没动线上）")
        return 0

    job_id = dt.datetime.now().strftime("%m%d-%H%M%S") + f"-{name}"
    stage = tempfile.mkdtemp(prefix="ship-")
    try:
        files = os.path.join(stage, "files")
        for _, rel, content, _, _ in todo:
            dst = os.path.join(files, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(content)
        with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(new_manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        with open(os.path.join(stage, "job.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(job_script(name, svc, todo, restart, manifest_sha, job_id))
        remote = f"/tmp/ship-{job_id}"
        run(["ssh", HOST, f"rm -rf {remote} && mkdir -p {remote}"])
        run(["scp", "-q", "-r", files, os.path.join(stage, "manifest.json"), os.path.join(stage, "job.sh"),
             f"{HOST}:{remote}/"])
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if restart and svc.get("quiet"):
        ssh(f"systemd-run --unit=ship-{job_id} --collect bash {remote}/job.sh")
        print(f"  等她安静 {QUIET_S // 60} 分钟再换再重启（job {job_id}）")
        return wait_result(job_id, wait_s)
    out = ssh(f"bash {remote}/job.sh; cat {remote}/result", check=False).decode()
    print(out.strip())
    return 0 if out.strip().endswith("ok") or "\nok" in out else 1


def job_script(name: str, svc: dict, todo: list, restart: bool, manifest_sha: str, job_id: str) -> str:
    root = svc["root"]
    rels = " ".join(f"'{rel}'" for _, rel, *_ in todo)
    exe = " ".join(f"'{rel}'" for _, rel, _, _, mode in todo if mode == "100755")
    quiet = restart and svc.get("quiet")
    units = " ".join(svc["units"]) if restart else ""
    health = svc.get("health", "")
    wait_quiet = QUIET_BLOCK.replace("@QUIET@", str(QUIET_S)) if quiet else ""
    #: 只清这次换掉的 .py 所在目录的 __pycache__ —— 🔴 不能 find 整个 $R：scripts 的 $R 是 /root，会扫到每个 .venv
    py_dirs = sorted({os.path.dirname(rel) or "." for _, rel, *_ in todo if rel.endswith(".py")})
    pycache = " ".join(f"'{d}/__pycache__'" for d in py_dirs)
    pycache = f"cd $R && rm -rf {pycache}" if pycache else ""
    return f"""#!/bin/bash
# ship.py 生成：{name} · {job_id}
set -u
J=/tmp/ship-{job_id}; R={root}; B=/root/backups/ship/{job_id}
fail() {{ echo "fail $*" > $J/result; echo "fail $*"; exit 1; }}
{wait_quiet}
cur=$(sha1sum {MANIFEST_DIR}/{name}.json 2>/dev/null | cut -c1-40)
[ "$cur" = "{manifest_sha}" ] || fail "清单在这期间被改过（别的部署？），没动"
mkdir -p $B
cd $R && for f in {rels}; do [ -e "$f" ] && cp --parents "$f" $B/; done
cp $MANIFEST_DIR/{name}.json $B/manifest.json 2>/dev/null
cd $J/files && cp --parents {rels} $R/ || fail copy
{f'cd $R && chmod +x {exe}' if exe else ''}
{pycache}
cp $J/manifest.json {MANIFEST_DIR}/{name}.json || fail manifest
{f'systemctl restart {units} || fail restart' if units else ''}
for j in $(seq 1 60); do
  ok=1
  for u in {units}; do [ "$(systemctl is-active $u)" = active ] || ok=0; done
  {f'[ "$(curl -s --noproxy "*" -o /dev/null -w "%{{http_code}}" {health})" = 200 ] || ok=0' if (health and units) else ''}
  [ $ok = 1 ] && {{ echo ok > $J/result; echo ok; exit 0; }}
  sleep 2
done
fail health
""".replace("$MANIFEST_DIR", MANIFEST_DIR)


def wait_result(job_id: str, wait_s: int) -> int:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        r = ssh(f"cat /tmp/ship-{job_id}/result 2>/dev/null", check=False).decode().strip()
        if r:
            print(f"  {r}")
            return 0 if r == "ok" else 1
        time.sleep(15)
    last = ssh(f"tail -1 /tmp/ship-{job_id}/log 2>/dev/null", check=False).decode().strip()
    print(f"  还在等她安静（{last}）。之后看：python scripts/ship.py --result {job_id}")
    return 3


# ---------------------------------------------------------------- 建清单

def baseline(name: str, force: bool) -> int:
    """给现在的线上建清单：只登记和 git 某个版本一致的文件；对不上的列出来、不登记。"""
    svc = SERVICES[name]
    ensure_helper()
    old, _ = load_manifest(name)
    if old is not None and not force:
        raise Stop(f"{name} 已经有清单了（--force 重建）")
    repo = svc["repo"]
    head = git(repo, "rev-parse", "--short", "HEAD").decode().strip()
    if svc.get("flat"):
        # 平铺：只认仓库 scripts/ 等目录里有同名文件的
        names = set()
        for d in svc["flat"]:
            for p in git(repo, "ls-tree", "--name-only", "HEAD", f"{d}/").decode().splitlines():
                names.add(p.split("/", 1)[1])
        live = {n: h for n, h in remote_hashes([f"/root/{n}" for n in sorted(names)]).items() if h}
        live = {k[len("/root/"):]: v for k, v in live.items()}
    else:
        ex = ",".join(svc.get("exclude_dirs", []))
        live = json.loads(ssh(f"python3 {HELPER} --list {svc['root']}" + (f" --exclude-dirs {ex}" if ex else "")))
    files, unmatched = {}, []
    for rel, h in sorted(live.items()):
        hit = None
        for in_repo in from_remote(svc, rel):
            if not git(repo, "ls-files", "--", in_repo).strip():
                continue
            head_b = git(repo, "show", f"HEAD:{in_repo}", check=False)
            if sha(head_b.replace(b"\r\n", b"\n")) == h["sha1_lf"]:
                hit = (in_repo, git(repo, "log", "-1", "--format=%h", "HEAD", "--", in_repo).decode().strip())
                break
            hist = history_shas(repo, in_repo, limit=40)
            if h["sha1_lf"] in hist:
                hit = (in_repo, hist[h["sha1_lf"]])
                break
        if hit:
            files[rel] = {"sha1": h["sha1"], "commit": hit[1], "src": src_of(repo, hit[0]),
                          "at": "baseline"}
        else:
            unmatched.append(rel)
    manifest = {"service": name, "root": svc["root"], "scan": svc.get("scan", False),
                "exclude_dirs": svc.get("exclude_dirs", []), "ignore": unmatched,
                "baseline": f"{now_iso()} @ {head}", "updated": now_iso(), "files": files}
    run(["ssh", HOST, f"mkdir -p {MANIFEST_DIR} && cat > {MANIFEST_DIR}/{name}.json"],
        data=json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
    print(f"{name}: 登记 {len(files)} 个文件；不在 git 里的 {len(unmatched)} 个记进 ignore（以后新冒出来的才报）")
    for u in unmatched:
        print(f"    ignore  {u}")
    return 0


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    restart = "--restart" in argv
    force = "--force" in argv
    args = [a for a in argv if not a.startswith("--")]
    try:
        if "--result" in argv:
            print(ssh(f"cat /tmp/ship-{args[0]}/result 2>/dev/null || tail -1 /tmp/ship-{args[0]}/log",
                      check=False).decode().strip() or "没有这个 job")
            return 0
        if "--baseline" in argv:
            for name in args or []:
                if name not in SERVICES:
                    raise Stop(f"不认识的服务 {name}（{', '.join(SERVICES)}）")
                baseline(name, force)
            return 0
        if len(args) < 2 or args[0] not in SERVICES:
            print(__doc__)
            return 2
        return ship(args[0], args[1:], restart, dry, wait_s=480)
    except Stop as e:
        print(f"停：{e}")
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
