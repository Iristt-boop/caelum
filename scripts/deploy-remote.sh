# CAELUM 发布：远端部分（跑在 VPS 上）
#
# 由 scripts/deploy.ps1 调用，也可以手工跑：
#     ssh root@VPS 'bash /root/deploy-remote.sh nox-core 2026-09-11-a1b2c3d'
#
# 职责：解包 → 验证 → 翻软链 → 重启 → 健康检查 → 失败自动回滚
#
# 设计要点（对齐修复排期 4.8）：
#   · **上传和生效是两个动作**：解到新目录，验证过了才翻软链
#   · **回滚 = 软链翻回去 + 重启**，几秒钟
#   · **永不就地覆盖**：正在跑的那份目录在整个过程中不动
set -u

SVC="${1:?用法: deploy-remote.sh <服务> <版本标签>}"
TAG="${2:?用法: deploy-remote.sh <服务> <版本标签>}"
TARBALL="${3:-/tmp/release-$SVC.tar}"

# ── 每个服务的布局。加服务就在这里加一行 ────────────────────────────
#   link_path  : 指向"当前这份代码"的软链（unit 的 WorkingDirectory 指它）
#   unit       : systemd 单元名
#   health     : 健康检查 URL（返回 200 算好）
#   keep       : 保留几份旧 release
case "$SVC" in
  nox-core)
    LINK=/root/nox-core/code
    UNIT=nox-core
    HEALTH=http://127.0.0.1:8100/health
    KEEP=5
    ;;
  bridge)
    LINK=/root/bridge/code
    UNIT=bridge
    HEALTH=http://127.0.0.1:3003/api/health
    KEEP=5
    #: 🔴 bridge 是 ESM（package.json 里 type=module），而 Node 解析 import 时按
    #: **文件的真实路径**向上找 node_modules —— release 在 /root/releases/bridge/<tag>/，
    #: 向上找不到 /root/bridge/node_modules。NODE_PATH 对 ESM **无效**。
    #: 所以每个 release 里都要建一个 node_modules 软链（磁盘几乎不花）。
    NODE_MODULES=/root/bridge/node_modules
    ;;
  touch-server)
    LINK=/root/touch-server/code
    UNIT=touch-server
    HEALTH=http://127.0.0.1:9333/health
    KEEP=5
    #: ⚠️ 它为啥**能**安全铺 release 布局：数据目录是 unit 里**显式**指定的
    #: （`TOUCH_DATA_DIR=/root/touch-server/data`），在 release 目录**外面** ——
    #: 所以换代不会跑到新目录里新建一份空的记录文件。
    #:
    #: 反过来，靠 `__file__` 找数据的服务（比如 co-watching）必须先补一个显式路径，
    #: 否则表现是「服务起来了、健康检查 200、但数据看起来全没了」—— noc-core
    #: 当初栽的就是这个，而且它不报错。
    ;;
  co-watching)
    LINK=/root/co-watching/code
    UNIT=co-watching
    HEALTH=http://127.0.0.1:3200/health
    KEEP=5
    #: ⚠️ **它的 WorkingDirectory 故意留在 /root/co-watching**，不指向 release 目录。
    #: 这个服务目前所有数据路径都是绝对的（DATA_DIR / COOKIES / LEDGER /
    #: /tmp/watching-*），所以 CWD 指哪都一样；但把 CWD 留在稳定目录，
    #: 以后有人加一个相对路径（`./data` 那种）也不会跟着 release 跑掉。
    #: **改 unit 时别顺手把 WorkingDirectory 也指到 code。**
    ;;
  touch-mcp)
    LINK=/root/touch-mcp/code
    UNIT=touch-mcp
    HEALTH=http://127.0.0.1:9336/health
    KEEP=5
    #: 它自己不存数据 —— 读的是 touch-server 那份 jsonl
    #: （TOUCH_DATA_FILE，绝对路径）。所以换代没有数据风险，
    #: 但 /health 里仍然查了那个目录在不在（防的是"路径指到别处"）。
    ;;
  *)
    echo "❌ 不认识的服务: $SVC（见 deploy-remote.sh 的 case 表）"
    exit 2
    ;;
esac

REL_ROOT=/root/releases/$SVC
NEW="$REL_ROOT/$TAG"
LOG=/root/releases/$SVC.deploy.log

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "── 部署 $SVC → $TAG ──"

# ── [1] 解包到新目录（不碰正在跑的）────────────────────────────────
if [ ! -f "$TARBALL" ]; then
  log "❌ 找不到 $TARBALL"
  exit 1
fi
if [ -e "$NEW" ]; then
  log "❌ $NEW 已存在 —— 同一个版本不重复部署（版本号是唯一的）"
  exit 1
fi
mkdir -p "$NEW"
tar xf "$TARBALL" -C "$NEW" --strip-components=1
log "[1] 已解到 $NEW（$(find "$NEW" -type f | wc -l) 个文件）"

# ── [1b] node 服务：补 node_modules 软链（见 case 表里的说明）──────
if [ -n "${NODE_MODULES:-}" ]; then
  if [ -d "$NODE_MODULES" ]; then
    ln -sfn "$NODE_MODULES" "$NEW/node_modules"
    log "[1b] node_modules → $NODE_MODULES"
  else
    log "❌ [1b] 找不到 $NODE_MODULES —— 不翻软链，直接退出"
    exit 1
  fi
fi

# ── [2] 验证：语法 / 导入 ─────────────────────────────────────────
# ⚠️ 这一步是"新目录能不能跑"，跟正在跑的那份无关。
#    用服务自己的解释器（nox-core 的 venv 在 /root/nox-core/.venv）。
case "$SVC" in
  nox-core)
    PY=/root/nox-core/.venv/bin/python
    if [ -x "$PY" ]; then
      if ! (cd "$NEW" && "$PY" -m compileall -q . >/dev/null 2>&1); then
        log "❌ [2] 语法检查失败 —— 不翻软链，直接退出"
        exit 1
      fi
      if ! (cd "$NEW" && "$PY" -c "import api.server" >/dev/null 2>&1); then
        log "❌ [2] import api.server 失败 —— 不翻软链，直接退出"
        (cd "$NEW" && "$PY" -c "import api.server" 2>&1 | tail -5) | tee -a "$LOG"
        exit 1
      fi
    else
      log "⚠️ [2] 找不到 $PY，跳过 import 检查"
    fi
    ;;
  bridge)
    if ! node --check "$NEW/server.js" >/dev/null 2>&1; then
      log "❌ [2] node --check server.js 失败 —— 不翻软链，直接退出"
      node --check "$NEW/server.js" 2>&1 | tail -3 | tee -a "$LOG"
      exit 1
    fi
    ;;
  touch-server)
    if ! python3 -m py_compile "$NEW/touch_server.py" >/dev/null 2>&1; then
      log "❌ [2] py_compile touch_server.py 失败 —— 不翻软链，直接退出"
      python3 -m py_compile "$NEW/touch_server.py" 2>&1 | tail -3 | tee -a "$LOG"
      exit 1
    fi
    ;;
  co-watching)
    if ! python3 -m py_compile "$NEW/app.py" >/dev/null 2>&1; then
      log "❌ [2] py_compile app.py 失败 —— 不翻软链，直接退出"
      python3 -m py_compile "$NEW/app.py" 2>&1 | tail -3 | tee -a "$LOG"
      exit 1
    fi
    ;;
  touch-mcp)
    if ! python3 -m py_compile "$NEW/server.py" >/dev/null 2>&1; then
      log "❌ [2] py_compile server.py 失败 —— 不翻软链，直接退出"
      python3 -m py_compile "$NEW/server.py" 2>&1 | tail -3 | tee -a "$LOG"
      exit 1
    fi
    ;;
esac
log "[2] 验证通过"

# ── [3] 翻软链（这一步才"生效"）────────────────────────────────────
PREV=""
[ -L "$LINK" ] && PREV=$(readlink "$LINK")
mkdir -p "$(dirname "$LINK")"
ln -sfn "$NEW" "$LINK"
log "[3] 软链 $LINK → $NEW（上一份: ${PREV:-无}）"

# ── [4] 重启 + 健康检查 ──────────────────────────────────────────
systemctl restart "$UNIT"
ok=0
for i in $(seq 1 20); do
  sleep 2
  st=$(systemctl is-active "$UNIT" 2>/dev/null)
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH" 2>/dev/null || echo 000)
  if [ "$st" = "active" ] && [ "$code" = "200" ]; then ok=1; break; fi
done

if [ "$ok" = "1" ]; then
  log "[4] ✅ $UNIT active 且 $HEALTH 返回 200"
else
  log "🔴 [4] 健康检查没过（unit=$st health=$code）—— 自动回滚"
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    ln -sfn "$PREV" "$LINK"
    systemctl restart "$UNIT"
    sleep 5
    log "    回滚到 $PREV，unit=$(systemctl is-active "$UNIT")"
  else
    log "    ⚠️ 没有上一份可回滚（首次部署），请人工看 journalctl -u $UNIT"
  fi
  journalctl -u "$UNIT" -n 20 --no-pager | tail -12 | tee -a "$LOG"
  exit 1
fi

# ── [5] 记账 + 清理旧 release ─────────────────────────────────────
{
  echo "service   : $SVC"
  echo "tag       : $TAG"
  echo "path      : $NEW"
  echo "deployed  : $(date '+%F %T')"
} > "$REL_ROOT/CURRENT"
log "[5] 已记账到 $REL_ROOT/CURRENT"

# 只保留最近 $KEEP 份（永不删当前指向的那份）
n=0
for d in $(ls -1dt "$REL_ROOT"/*/ 2>/dev/null); do
  n=$((n+1))
  if [ "$n" -gt "$KEEP" ]; then
    case "$(readlink -f "$LINK")" in
      "$(readlink -f "$d")") continue ;;
    esac
    rm -rf "$d" && log "    清掉旧 release: $d"
  fi
done

log "── $SVC 部署完成 ──"
exit 0
