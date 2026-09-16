#!/usr/bin/env bash
# ============================================================
# deploy-vps.sh —— 逐文件部署 + 部署指纹（2026-09-16 起）
#
# 为什么有这个脚本：线上是逐文件 scp 的演化线（不是整仓同步），
# 多个会话并行开发时，线上文件集合来自不同 commit 甚至不同工作树。
# 2026-09-16 撞过一次：server.py 来自最新 master，而它引用的
# decay_engine 属性来自另一会话未提交的本地改动 —— 线上当场报错，
# 排障只能靠猜。「线上到底跑着哪版」从此有了答案。
#
# 用法：
#   deploy-vps.sh deploy  <service> <file1> [file2...]   # scp 上去并记指纹
#   deploy-vps.sh record  <service> <file1> [file2...]   # 不传文件，只补录指纹
#   deploy-vps.sh version [service]                       # 查线上指纹
#
# 指纹含义：
#   <sha>          文件干净，最后 commit 是它
#   dirty@<sha>    commit 之后本地又改过（未提交）—— 最危险的一类，
#                  配套断裂几乎都出在这
#   untracked      git 里根本没这个文件（全新文件没提交就部署了）
#
# 服务映射写死在下面 —— 加新服务在这里加一行。
# ============================================================
set -euo pipefail

HOST="43.153.154.237"
KEY="$HOME/.ssh/id_ed25519"
BASE_LOCAL="/d/claude-code"
INFO_DIR="/root/DEPLOY_INFO"

# 服务名 → (本地仓库目录, 线上目录)。文件路径相对仓库根。
service_dir() {
  case "$1" in
    nox-core)     echo "$BASE_LOCAL/nox-core /root/nox-core" ;;
    bridge)       echo "$BASE_LOCAL/bridge /root/bridge" ;;
    ombre-brain)  echo "$BASE_LOCAL/Ombre-Brain /root/ombre-brain" ;;
    *) echo "" ;;
  esac
}

# 一个文件的指纹：<sha> / dirty@<sha> / untracked
fingerprint() {  # $1=repo_dir $2=rel_path
  local dir="$1" rel="$2"
  git -C "$dir" ls-files --error-unmatch "$rel" >/dev/null 2>&1 || { echo "untracked"; return; }
  local sha
  sha=$(git -C "$dir" log -1 --format=%h -- "$rel" 2>/dev/null || echo "unknown")
  if [ -n "$(git -C "$dir" status --porcelain -- "$rel" 2>/dev/null)" ]; then
    echo "dirty@${sha}"
  else
    echo "${sha}"
  fi
}

ssh_vps() { ssh -i "$KEY" -o ConnectTimeout=12 "root@${HOST}" "$@"; }
scp_vps() { scp -i "$KEY" -o ConnectTimeout=12 -q "$1" "root@${HOST}:$2"; }

# 重建某服务的 summary（当前集合视图）。deploy 和 record 都要走——
# 漏了的话 record 只进历史 jsonl，version 看到的还是旧集合（第一次就踩了）
rebuild_summary() {  # $1=service
  ssh_vps "python3 - <<'PYEOF'
import json
rows=[json.loads(l) for l in open('$INFO_DIR/$1.jsonl',encoding='utf-8')]
cur={}
for r in rows: cur[r['file']]=r
json.dump({'service':'$1','files':list(cur.values())},
          open('$INFO_DIR/$1.summary','w',encoding='utf-8'),ensure_ascii=False,indent=1)
PYEOF"
}

cmd="${1:-}"
[ -z "$cmd" ] && { sed -n '2,20p' "$0"; exit 1; }

case "$cmd" in

  # ---------------------------------------------------------- deploy
  deploy)
    service="${2:-}"
    [ -z "$service" ] && { echo "deploy 需要 service（nox-core / bridge / ombre-brain）"; exit 1; }
    map=$(service_dir "$service")
    [ -z "$map" ] && { echo "未知服务：$service"; exit 1; }
    read -r LOCAL_DIR REMOTE_DIR <<< "$map"
    shift 2
    [ $# -eq 0 ] && { echo "deploy 需要至少一个文件"; exit 1; }
    lines=""
    for rel in "$@"; do
      src="$LOCAL_DIR/$rel"
      [ -f "$src" ] || { echo "✗ 本地不存在：$src"; exit 1; }
      fp=$(fingerprint "$LOCAL_DIR" "$rel")
      scp_vps "$src" "$REMOTE_DIR/$rel"
      ts=$(date '+%Y-%m-%d %H:%M:%S')
      lines+="{\"ts\":\"$ts\",\"file\":\"$rel\",\"fp\":\"$fp\"}"$'\n'
      echo "↑ $rel  [$fp]"
    done
    # 指纹上服务器：jsonl 追加历史 + summary 覆盖为「当前集合」
    ssh_vps "mkdir -p $INFO_DIR"
    printf '%s' "$lines" | ssh_vps "cat >> $INFO_DIR/$service.jsonl"
    rebuild_summary "$service"
    echo "✓ 部署完成，指纹已记录（$service）"
    ;;

  # ---------------------------------------------------------- record
  record)
    service="${2:-}"
    [ -z "$service" ] && { echo "record 需要 service"; exit 1; }
    map=$(service_dir "$service")
    [ -z "$map" ] && { echo "未知服务：$service"; exit 1; }
    read -r LOCAL_DIR REMOTE_DIR <<< "$map"
    shift 2
    [ $# -eq 0 ] && { echo "record 需要至少一个文件"; exit 1; }
    lines=""
    for rel in "$@"; do
      fp=$(fingerprint "$LOCAL_DIR" "$rel")
      ts=$(date '+%Y-%m-%d %H:%M:%S')
      lines+="{\"ts\":\"$ts\",\"file\":\"$rel\",\"fp\":\"$fp\",\"note\":\"补录\"}"$'\n'
      echo "○ $rel  [$fp]"
    done
    ssh_vps "mkdir -p $INFO_DIR"
    printf '%s' "$lines" | ssh_vps "cat >> $INFO_DIR/$service.jsonl"
    rebuild_summary "$service"
    echo "✓ 补录完成（不部署文件）"
    ;;

  # ---------------------------------------------------------- version
  version)
    service="${2:-all}"
    if [ "$service" != "all" ]; then
      ssh_vps "cat $INFO_DIR/$service.summary 2>/dev/null || echo '$service 没有部署指纹记录'"
    else
      ssh_vps "for s in nox-core bridge ombre-brain; do
        echo \"== \$s ==\"
        cat $INFO_DIR/\$s.summary 2>/dev/null || echo '  （无记录 —— 该服务的线上版本未知）'
      done"
    fi
    ;;

  *)
    echo "未知命令：$cmd（deploy / record / version）"; exit 1
    ;;
esac
