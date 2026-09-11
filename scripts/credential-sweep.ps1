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

Write-Host "═══ 1/4  从 VPS 取凭据清单（只留名字和值，值不外显）═══" -ForegroundColor Cyan
$py = @'
import glob, os, re
files = glob.glob('/etc/nox/*.env') + ['/root/nox-core/.env','/root/co-reading-mcp/.env',
  '/root/health-mcp/.env','/root/netease-music-mcp/.env','/root/eryu/server/.secret',
  '/root/eryu/server/.netease_cred','/root/watch/cookies.txt','/root/.backup-pass']
KEY = re.compile(r'TOKEN|KEY|SECRET|PASSWORD|COOKIE|PASS', re.I)
for f in files:
    if not os.path.isfile(f): continue
    for line in open(f, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, v = line.split('=', 1); v = v.strip().strip('"').strip("'")
        if len(v) < 16 or '\t' in v: continue
        if KEY.search(k) or KEY.search(os.path.basename(f)):
            print('%s\t%s' % (os.path.basename(f) + ':' + k, v))
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$raw = & ssh -o BatchMode=yes -o ConnectTimeout=20 $Vps "echo '$b64' | base64 -d > /tmp/sweep.py && python3 /tmp/sweep.py; rm -f /tmp/sweep.py"
$creds = @()
foreach ($l in $raw) { $p = $l -split "`t", 2; if ($p.Count -eq 2) { $creds += [pscustomobject]@{ Name = $p[0]; Val = $p[1] } } }
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
