# 凭据外泄普查 —— 一次把所有凭据撞所有"外面够得到"的表面
#
# 用法：  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\credential-sweep.ps1
#
# 🔴 为什么要写它（2026-09-12）：
#
#   今天一整天，「藏一个漏一个」被发现了六次。每一次都是同一个错误：
#   **从一个样本建清单** ——
#     · 历史重写的表达式清单是从初始提交的 touch-mcp/Caddyfile 建的
#       → 漏了 /watch/ 和 /agent/ 那两个 24 位的
#     · 查「谁在用这个 token」时只从本机 grep 起
#       → 漏了 claude.ai 网页连接器（那在浏览器里，磁盘上根本没有）
#     · 找到 toy.html 的 token 之后，只想到"谁引用了它"
#       → 没意识到它自己就在公网上
#
#   这份脚本把顺序倒过来：**先把凭据全部列出来，再逐个撞所有表面。**
#   表面有三个维度，缺一不可：
#     ① 公网可取的内容（最高优先级 —— 外面碰得到的才算"漏"）
#     ② 每个 git 仓库的全历史
#     ③ 本机文件（分清"被跟踪"和"只是躺在盘上"）
#
#   跑一次大约一两分钟。**以后每加一个凭据、每接一个服务，跑一遍。**
#
# ⚠️ 它只报告，不改任何东西。

$ErrorActionPreference = "Continue"
$Vps = "root@43.133.211.140"

Write-Host "═══ 1/4  从 VPS 取凭据清单（按形状找，不按名字找）═══" -ForegroundColor Cyan
# ⚠️ 2026-09-12 修掉五个**结构性**盲区。上一版是"漏一个补一个"，
#    结果就是糖糖那句"怎么每次藏一个就漏一个"。根因是清单来源本身有洞：
#      ① 文件清单手写 → 新接的服务 .env 永远收不进来。改成 glob。
#      ② 只认键名里带 TOKEN/SECRET 的 → 叫 FOO 的凭据看不见。改成"名字 **或** 形状"。
#      ③ 值短于 16 字符直接丢 → 12 位的密码看不见。floor 降到 8，短的标"(弱形状)"。
#      ④ **配置里硬写的路径暗号**完全不可见 —— panel.noxtang.com 那两条订阅 hex
#         就写在 Caddyfile 里，env 里根本没有，所以旧版结构上看不见它们。
#         新增来源 B：扫 Caddyfile / systemd unit 的每一段路径。
#      ⑤ 新增来源 C：高熵兜底 —— 长得像密钥的，不管它叫什么。
#    ⚠️ 仍然只报告，不改任何东西。
$py = @'
import glob, os, re, sys

BORING = re.compile(r'^(true|false|yes|no|on|off|none|null|nil|debug|info|warn|warning|error|trace|critical|\d+)$', re.I)
NAMISH = re.compile(r'(?i)(token|key|secret|password|passwd|pwd|cookie|cred|auth|sign|salt|bearer|apikey|access|refresh|client|session|jwt|hmac|vapid|private)')
TOKENY = re.compile(r'^[A-Za-z0-9_\-+/=.:]{8,}$')

def has_entropy(v):
    u = any(c.isupper() for c in v); l = any(c.islower() for c in v); d = any(c.isdigit() for c in v)
    return (u and l) or (d and (u or l))

def looks_secret(v):
    if len(v) < 8 or BORING.match(v): return False
    if not TOKENY.match(v): return False          # 有中文/空格 → 是句子，不是凭据
    if UNITISH.search(v): return False            # 文件名 / unit 名
    if v.startswith('/'):
        # ⚠️ 纯文件系统路径不是凭据。`len(v) >= 24` 那条曾经把
        #    `/root/co-reading-mcp/data`（25 位）这种路径也算成凭据。
        #    路径只有在**含有一段长得像暗号的成分**时才算。
        return bool([s for s in PATHSEG.findall(v) if seg_is_secret(s)])
    # ⚠️ 2026-09-12 第二轮收误报：光看"字符类别混着来"是不够的 ——
    #    域名、仓库名、模型名同样是大写小写+数字混着，于是
    #    `devapi.qweather.com` / `Iristt-boop/Claude` / `claude-3-5-sonnet-20241022`
    #    全被算成凭据，git 那一栏红了 7 条假的，真东西被淹。
    #    下面四条按**结构**排掉人类可读的名字，只留下真正像密钥的串。
    if HOSTNAME.match(v): return False            # devapi.qweather.com
    if REPOSLUG.match(v): return False            # Iristt-boop/Claude
    if SLUGISH.match(v): return False             # claude-3-5-sonnet-20241022
    if v.endswith('.'): return False
    # 走到这里还剩下的：要么是"长度 ≥24 且不含分隔符"（hex/base64 暗号，
    # 如 234afcf0…（24 位 hex）），要么是大小写数字混排的紧凑串。
    # ⚠️ 这里**只写指纹不写值** —— 第一版我把完整值抄进注释里当例子，
    #    结果被自己的工具抓出来（这脚本是跟踪文件），等于换个地方重新泄一次。
    return has_entropy(v) or (len(v) >= 24 and '-' not in v and '.' not in v)

def seg_is_secret(s):
    # 路径暗号：≥16 位，且不是纯小写字母。
    # ⚠️ 上一版判据是「≥20 位」**或**「混合大小写」，于是 panel.noxtang.com 那两条
    #    **16 位纯小写 hex**（0b1dccd1… / 99b2bead…）两条都不满足 ——
    #    我加这个来源就是为了抓它们，结果自己把它们漏了。判据改成「字母+数字就算」，
    #    只排除纯小写无数字的（multi-user / site-packages 这种正常路径名）。
    if len(s) < 16: return False
    if re.fullmatch(r'[a-z]+', s): return False
    if UNITISH.search(s): return False
    if HOSTNAME.match(s) or SLUGISH.match(s) or REPOSLUG.match(s): return False
    if any(c.isdigit() for c in s) and any(c.isalpha() for c in s): return True
    return any(c.isupper() for c in s) and any(c.islower() for c in s)

PATHSEG = re.compile(r'/([A-Za-z0-9_\-]{12,})(?=[/\s*{]|$)')
ENTROPY = re.compile(r'[A-Za-z0-9_\-]{28,}')
URL = re.compile(r'^[a-z][a-z0-9+.\-]*://', re.I)
# 文件名 / systemd unit 名不是凭据。高熵兜底误抓过
# `dbus-org.freedesktop.resolve1.service` 这类，必须在判据里排掉。
UNITISH = re.compile(r'\.(service|socket|target|timer|mount|path|conf|cfg|json|ya?ml|py|sh|js|mjs|ts|md|txt|log|env|example|bak|old|png|jpg|gif|ttf|woff2?)$', re.I)
# 人类可读的**名字**不是凭据 —— 按结构排掉（第二轮收误报）
HOSTNAME = re.compile(r'^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$', re.I)   # devapi.qweather.com
REPOSLUG = re.compile(r'^[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+$')        # Iristt-boop/Claude
SLUGISH  = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]{1,12}){2,}$')       # claude-3-5-sonnet-20241022
#   ⚠️ SLUGISH 刻意要求「每一段都 ≤12 位」：真实的 `sk-ant-api03-<很长的随机段>`
#      最后一段远超 12 位，所以不会被误排掉。收紧前它会把真实 API key 也吃掉。

rows = []; skipped = 0; n_shape = 0; n_path = 0; n_ent = 0

# ── 来源 A：所有 env 形状的文件（glob，不再手写清单）──
files = []
for pat in ['/etc/nox/*.env','/etc/nox/*/*.env','/etc/**/*.env','/root/*/.env','/root/*/*.env',
            '/root/*/.secret','/root/*/.netease_cred','/root/watch/cookies.txt','/root/.backup-pass']:
    files += glob.glob(pat, recursive=True)
files = sorted(set(f for f in files if os.path.isfile(f) and os.path.getsize(f) < 2_000_000))

for f in files:
    base = os.path.basename(f)
    try: lines = open(f, encoding='utf-8', errors='replace').read().splitlines()
    except Exception: continue
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k, v = s.split('=', 1); k = k.strip(); v = v.strip().strip('"').strip("'")
        if not v or '\t' in v: continue
        name_hit = bool(NAMISH.search(k)) or bool(NAMISH.search(base))
        if URL.match(v):
            # ⚠️ 2026-09-12：URL **整体**不是凭据 —— 第一版把整个 URL 当凭据，
            #    于是 `NOX_OB_URL=http://127.0.0.1:8002/mcp` 这种回环地址全被算成"凭据"，
            #    git 那栏一下子红了 20 条（PROJECT.md 里就写着这些 URL），
            #    真东西被淹掉。只取它**路径段**和**查询参数**里长得像暗号的部分。
            found = 0
            for seg in set(PATHSEG.findall(v)):
                if seg_is_secret(seg):
                    rows.append(('%s:%s(URL路径段)' % (base, k), seg)); n_path += 1; found += 1
            for q in re.findall(r'[?&][A-Za-z0-9_\-]{3,}=([A-Za-z0-9_\-+/=.]{16,})', v):
                if looks_secret(q):
                    rows.append(('%s:%s(URL查询参数)' % (base, k), q)); n_path += 1; found += 1
            if not found: skipped += 1
            continue
        if len(v) >= 8 and not BORING.match(v) and name_hit:
            mark = '' if looks_secret(v) else '(弱形状)'
            rows.append(('%s:%s%s' % (base, k, mark), v))
            if mark: n_shape += 1
        elif looks_secret(v):
            rows.append(('%s:%s(形状命中)' % (base, k), v))
            n_shape += 1
        else:
            skipped += 1
        for seg in set(PATHSEG.findall(v)):
            if seg_is_secret(seg):
                rows.append(('%s:%s(URL里的暗号)' % (base, k), seg)); n_path += 1

# ── 来源 B：配置文件里**硬写**的路径暗号 ──
cfgs = ['/etc/caddy/Caddyfile'] + glob.glob('/etc/caddy/*') + glob.glob('/etc/systemd/system/*.service')
for f in sorted(set(cfgs)):
    if not os.path.isfile(f): continue
    try: text = open(f, encoding='utf-8', errors='replace').read()
    except Exception: continue
    for seg in set(PATHSEG.findall(text)):
        if seg_is_secret(seg):
            rows.append(('路径暗号:%s' % os.path.basename(f), seg)); n_path += 1

# ── 来源 C：高熵兜底（长得像密钥的，不管叫什么）──
for f in sorted(set(['/etc/caddy/Caddyfile'] + glob.glob('/etc/systemd/system/*.service'))):
    if not os.path.isfile(f): continue
    try: text = open(f, encoding='utf-8', errors='replace').read()
    except Exception: continue
    for s in set(ENTROPY.findall(text)):
        if looks_secret(s):
            rows.append(('高熵兜底:%s' % os.path.basename(f), s)); n_ent += 1

seen = set()
for n, v in rows:
    if v in seen or '\t' in v: continue
    seen.add(v)
    # 路径暗号 / 高熵 / 形状命中这几类，把「名字 + 前 4 位 + 长度」打出来当指纹 ——
    # 这样"工具到底覆盖到了哪些"本身可审计，而值不外显（4 位前缀单独无用）。
    if n.startswith('路径暗号') or n.startswith('高熵兜底') or '(形状' in n:
        print('###HIT %s = %s…(%d位)' % (n.split(':')[0], v[:4], len(v)))
    print('%s\t%s' % (n, v))
print('###STAT 文件 %d 个 / 形状命中 %d / 路径暗号 %d / 高熵兜底 %d / 判定为非凭据跳过 %d'
      % (len(files), n_shape, n_path, n_ent, skipped))
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$raw = & ssh -o BatchMode=yes -o ConnectTimeout=20 $Vps "echo '$b64' | base64 -d > /tmp/sweep.py && python3 /tmp/sweep.py; rm -f /tmp/sweep.py"
$creds = @()
foreach ($l in $raw) {
  if ($l -like '###*') { Write-Host ("  " + $l.Substring(3)) -ForegroundColor DarkGray; continue }
  $p = $l -split "`t", 2
  if ($p.Count -eq 2) { $creds += [pscustomobject]@{ Name = $p[0]; Val = $p[1] } }
}
Write-Host "  $($creds.Count) 个凭据" -ForegroundColor Green
if ($creds.Count -eq 0) { Write-Host "  ⚠ 一个都没取到，先查 ssh 和 /etc/nox/*.env" -ForegroundColor Red; exit 1 }

Write-Host "`n═══ 2/4  抓公网能取到的内容 ═══" -ForegroundColor Cyan
# ⚠️ 这一维是重点：外面碰得到才算漏。
#    页面 + 它引用的每个本地资源都要抓（钥匙常常在 js 里，不在 html 里）。
$seeds = @(
  'https://noxtang.com/', 'https://noxtang.com/toy.html', 'https://noxtang.com/nox-voice.html',
  'https://music.noxtang.com/', 'https://reading.noxtang.com/'
)
$seen = @{}
$sb = New-Object System.Text.StringBuilder
foreach ($u in $seeds) {
  $b = (& curl.exe -s -L --max-time 15 $u 2>$null) -join "`n"
  if (-not $b) { continue }
  $seen[$u] = $b.Length; [void]$sb.Append($b)
  $refs = [regex]::Matches($b, '(?:src|href)="([^"#?]+)"') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
  foreach ($r in $refs) {
    if ($r -match '^(https?:)?//' -and $r -notmatch 'noxtang\.com') { continue }
    $full = if ($r -like 'http*') { $r } elseif ($r.StartsWith('/')) { ([uri]$u).GetLeftPart('Authority') + $r } else { ($u -replace '/[^/]*$', '/') + $r }
    if ($seen.ContainsKey($full)) { continue }
    $sub = (& curl.exe -s -L --max-time 15 $full 2>$null) -join "`n"
    if ($sub) { $seen[$full] = $sub.Length; [void]$sb.Append($sub) }
  }
}
$all = $sb.ToString()
Write-Host "  $($seen.Count) 个资源 / $([math]::Round($all.Length/1024)) KB" -ForegroundColor Green
if ($seen.Count -lt 2) { Write-Host "  ⚠ 抓到的太少，公网检查结果不可信" -ForegroundColor Red }

Write-Host "`n═══ 3/4  逐个凭据撞三个表面 ═══" -ForegroundColor Cyan
$repos = @{ 'caelum' = '.'; 'nox-app' = 'nox-app'; 'deploy-config' = 'deploy-config'
            'Ombre-Brain' = 'Ombre-Brain'; 'caelum-room' = 'caelum-room'; 'ai-fishing-game' = 'ai-fishing-game' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRevs = @{}
foreach ($k in $repos.Keys) {
  $p = Join-Path $repoRoot $repos[$k]
  if (Test-Path (Join-Path $p '.git')) { $repoRevs[$k] = (& git -C $p rev-list --all 2>$null) }
}

$rows = @()
foreach ($c in $creds) {
  $pub = $all.Contains($c.Val)
  $gitHit = @()
  foreach ($k in $repoRevs.Keys) {
    if ($repoRevs[$k] -and (& git -C (Join-Path $repoRoot $repos[$k]) grep -l -F $c.Val @($repoRevs[$k]) 2>$null)) { $gitHit += $k }
  }
  $row = [pscustomobject]@{ Name = $c.Name; Val = $c.Val; Len = $c.Val.Length; 公网 = $pub; Git = ($gitHit -join ','); 本机 = '' }
  $rows += $row
}

# ── 本机文件（第三维，不能省）──────────────────────────────
# ⚠️ 2026-09-12 第一版这里空着 —— 那正是"检查的维度比要查的东西窄"的老毛病。
#    本机这一维要跟 git 那栏交叉看才下结论：
#      文件被 git 跟踪 → 会进远端（那就是 git 那栏的事）
#      只是躺在盘上   → 不外泄，但别把它 scp/导出/贴进文档
$alt = ($creds | ForEach-Object { [regex]::Escape($_.Val) }) -join '|'
Write-Host "  扫本机文件…" -ForegroundColor DarkGray
$hitPaths = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch '\\node_modules\\|\\\.git\\|\\backups\\|\\dist|Cache|\\\.venv|file-history|\\projects\\|\\logs\\' -and $_.Length -lt 4MB } |
  Select-String -Pattern $alt -List -ErrorAction SilentlyContinue |
  ForEach-Object { $_.Path } | Select-Object -Unique
if ($hitPaths) {
  Write-Host "    命中的文件: $($hitPaths.Count) 个" -ForegroundColor DarkGray
  foreach ($r in $rows) {
    $where = @()
    foreach ($h in $hitPaths) {
      if (Select-String -LiteralPath $h -Pattern ([regex]::Escape($r.Val)) -Quiet -ErrorAction SilentlyContinue) {
        $where += $h.Replace("$repoRoot\", '')
      }
    }
    if ($where) { $r.本机 = ($where -join ' ') }
  }
}

Write-Host "`n═══ 4/4  结果 ═══" -ForegroundColor Cyan
$pubLeaks = $rows | Where-Object { $_.公网 }
if ($pubLeaks) {
  Write-Host "🔴 公网可取（最严重 —— 外面碰得到）：" -ForegroundColor Red
  $pubLeaks | ForEach-Object { Write-Host "     $($_.Name)   (长度 $($_.Len))" -ForegroundColor Red }
} else {
  Write-Host "✅ 公网可取：0 个" -ForegroundColor Green
}
$gitLeaks = $rows | Where-Object { $_.Git }
if ($gitLeaks) {
  Write-Host "🟠 进了 git 历史（私有仓库，外面碰不到但读权限会扩散）：" -ForegroundColor Yellow
  $gitLeaks | ForEach-Object { Write-Host "     $($_.Name)   → $($_.Git)" -ForegroundColor Yellow }
} else {
  Write-Host "✅ git 历史：0 个" -ForegroundColor Green
}

Write-Host "`n── 全部 $($creds.Count) 个凭据（只列名字，不打值）──"
# 🔴 **必须显式写出要显示的列。**
#    只写 `Format-Table -AutoSize` 会把 $rows 的**全部属性**打出来 ——
#    而 $rows 里带着 `Val`，于是整张凭据表原样铺在屏幕上。
#    第一版就是这么翻的车（2026-09-12）：一个专门查泄露的工具，
#    自己把 45 个凭据打进了终端（以及可能的日志里）。
#    这是同一类错误的又一次：**忘了问「这个输出会落到哪儿」。**
$rows | Sort-Object { -not $_.公网 }, { -not $_.Git }, Name |
  Format-Table Name, Len, 公网, Git, 本机 -AutoSize |
  Out-String -Width 220 | Write-Host

Write-Host @"

判读方式：
  · 公网那栏红了 → 立刻轮换 + 把那个页面里的硬编码去掉（今天 toy.html 就是这么修的）
  · git 那栏红了 → 先测这个凭据还活不活着：
                    活着就【吊销】才是修复；改历史是次要的，而且要做就得删库重建
                    （实测：GitHub 上「重写 + 强推」不彻底，旧根提交仍可达）
  · 两栏都绿 → 它只在应该待的地方

已知但仍在这张表里的（不是因为漏，是因为机制）：
  · CADDY_TOKEN_WATCH 是**客户端凭据** —— 轮换无效（新值照样被打进安装包），
    要换的是机制（短时效票）。见排期第七批 ②。
"@ -ForegroundColor DarkGray
