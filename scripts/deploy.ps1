# CAELUM 发布工具（本地跑，Windows PowerShell）
#
# ⚠️ **这个文件必须保存为「UTF-8 with BOM」。**
#    Windows PowerShell 5.1 会用系统 ANSI（这台是 GBK）解码没有 BOM 的 .ps1，
#    中文全乱、解析直接崩（2026-09-11 踩过）。别把 BOM 去掉。
#
# 用法（本机是 PowerShell 5.1，没有 pwsh）：
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Status
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1 nox-core
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1 nox-core a1b2c3d
#
# 为什么这么设计（修复排期 4.8）：
#   · `git archive` 打的是**一个 commit 的完整树** —— 物理上不可能漏传一个文件。
#     这正是 2026-09-11 那次事故：`api/world.py` 从没被 scp 上去，World 页 500 了好几天。
#   · 上传和生效分开：远端解到新目录、验证过、才翻软链。
#   · 回滚 = 软链翻回去，几秒。
#   · **前提：先 commit 再部署。** 未提交的改动不在 release 里（这是特性不是 bug）。
#     ⚠️ 2026-09-11 实测：`agent/loop.py` 和 `personality/scenes.py` 的改动**已经在线上跑**、
#        但没提交。用 git 部署会把它们回滚掉。脚本会检测并拦你。
#
# 加新服务：在 deploy-remote.sh 的 case 表里加一条，这里的 $Services 表里也加一条。
param(
  [Parameter(Position = 0)][string]$Service,
  [Parameter(Position = 1)][string]$Commit = "HEAD",
  [switch]$Status
)

$ErrorActionPreference = "Stop"
$Key  = "C:\Users\14372\.ssh\id_ed25519"
$Vps  = "root@43.133.211.140"
$SshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
$RepoRoot = Split-Path -Parent $PSScriptRoot

#: 服务 → 仓库里的子目录
$Services = @{
  "nox-core" = "nox-core"
  "bridge"   = "bridge"
}

function Invoke-Remote([string]$Script) {
  # 走 stdin 喂脚本。两个 PS 5.1 的坑都在这里处理掉：
  #
  # 🔴 坑 1：**PowerShell 5.1 往 native 进程 stdin 写时，会给最后一行补 CRLF。**
  #    于是最后一个命令收不到干净的参数 —— `head -n 20` 会报
  #    `invalid number of lines: '20\r'`（2026-09-11 被这个坑了半天）。
  #    解法：末尾补一行无害的 `: # end`，让那个 \r 落在它身上。
  #
  # 🔴 坑 2：`$ErrorActionPreference = "Stop"` 时，native 命令往 stderr 写一行
  #    会被 PS 当成**终止性错误**抛出来，把正常输出也一起打断。
  #    解法：调用期间临时切成 Continue，再把结果统一转成字符串。
  $Script = ($Script -replace "`r", "") + "`n: # end"
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = $Script | ssh @SshOpts $Vps "bash -s" 2>&1
    return ($out | ForEach-Object { $_.ToString() })
  }
  finally { $ErrorActionPreference = $prev }
}

# ── -Status：线上跑的是哪个版本 ─────────────────────────────────
if ($Status) {
  Write-Host "查询线上版本…" -ForegroundColor Cyan
  # 上传成文件再跑 —— 彻底绕开 stdin 的 CRLF 问题
  scp @SshOpts -q (Join-Path $PSScriptRoot "deploy-status.sh") "${Vps}:/root/deploy-status.sh"
  if ($LASTEXITCODE -ne 0) { Write-Host "上传 deploy-status.sh 失败" -ForegroundColor Red; exit 1 }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & ssh @SshOpts $Vps "bash /root/deploy-status.sh" 2>&1 | ForEach-Object { Write-Host $_.ToString() }
  }
  finally { $ErrorActionPreference = $prev }
  return
}

if (-not $Service) {
  Write-Host "用法: powershell -File scripts\deploy.ps1 服务名 [commit]" -ForegroundColor Yellow
  Write-Host "      powershell -File scripts\deploy.ps1 -Status"
  Write-Host "服务: $($Services.Keys -join ', ')"
  exit 1
}
if (-not $Services.ContainsKey($Service)) {
  Write-Host "不认识的服务: $Service（可选: $($Services.Keys -join ', ')）" -ForegroundColor Red
  exit 1
}

$Sub = $Services[$Service]
Push-Location $RepoRoot
try {
  # ── [0] 解析 commit + 提醒未提交的改动 ────────────────────────
  $Sha = (git rev-parse --short=12 $Commit).Trim()
  if ($LASTEXITCODE -ne 0) { throw "git rev-parse 失败：$Commit" }

  $dirty = git status --short -- $Sub
  if ($dirty) {
    Write-Host "⚠️  $Sub 在 $Sha 之后还有未提交的改动，**它们不会被部署**：" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkYellow }
    Write-Host "    （版本化部署的前提就是「先 commit 再部署」）" -ForegroundColor DarkGray
    $ans = Read-Host "    继续部署 $Sha 吗？(y/N)"
    if ($ans -ne "y") { Write-Host "已取消。" ; exit 1 }
  }

  $Tag = "$(Get-Date -Format 'yyyy-MM-dd')-$Sha"
  Write-Host "── 部署 $Service @ $Sha（tag=$Tag）──" -ForegroundColor Cyan

  # ── [1] git archive：一个 commit 的完整树 ─────────────────────
  $tar = Join-Path $env:TEMP "release-$Service.tar"
  if (Test-Path $tar) { Remove-Item $tar -Force }
  cmd /c "git archive --format=tar --output=`"$tar`" $Commit $Sub"
  if ($LASTEXITCODE -ne 0) { throw "git archive 失败" }
  $size = [math]::Round((Get-Item $tar).Length / 1KB, 1)
  $count = (tar -tf $tar | Measure-Object).Count
  Write-Host "[1] 打包完成：$size KB / $count 个条目"

  # ── [2] 上传 ─────────────────────────────────────────────────
  scp @SshOpts -q $tar "${Vps}:/tmp/release-$Service.tar"
  if ($LASTEXITCODE -ne 0) { throw "scp 失败" }
  Write-Host "[2] 已上传到 /tmp/release-$Service.tar"

  # ── [3] 远端：远端脚本要在位 ─────────────────────────────────
  scp @SshOpts -q (Join-Path $PSScriptRoot "deploy-remote.sh") "${Vps}:/root/deploy-remote.sh"
  if ($LASTEXITCODE -ne 0) { throw "上传 deploy-remote.sh 失败" }

  # ── [4] 远端执行 ─────────────────────────────────────────────
  $out = Invoke-Remote "bash /root/deploy-remote.sh '$Service' '$Tag' '/tmp/release-$Service.tar'"
  $out | ForEach-Object { Write-Host $_ }
  if (-not (($out -join "`n") -match "部署完成")) {
    Write-Host "🔴 部署未成功（详见上面的日志）" -ForegroundColor Red
    exit 1
  }

  Write-Host "✅ $Service 部署完成：$Tag" -ForegroundColor Green
}
finally { Pop-Location }
