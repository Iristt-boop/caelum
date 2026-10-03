# register-moments-task.ps1 —— 注册 Moments shadow 的每日拉取，并**空跑一次验它真跑**。
#
# 2026-09-21 立。为什么要一个脚本而不是一行命令：
# 9-15 那天 `moments-shadow-pull.ps1` 的注释里写好了 schtasks 命令，
# 但**没人真跑过那一行** —— 于是六天没有一份自动报告，
# 而 `scratch/moments-shadow/` 里躺着 9-15 那份，看起来像「有在跑」。
# 光 /Create 不算完：必须 /Run 一次、并确认**今天日期的那个文件真落了地**
# （新建定时任务必须空跑一次，memory: claude-code-runs-on-deepseek）。
#
# 🔴 要管理员 PowerShell（/Create 写的是系统计划任务）。
# 跑法：右键 PowerShell「以管理员身份运行」，然后
#   powershell -NoProfile -ExecutionPolicy Bypass -File D:\claude-code\scripts\register-moments-task.ps1
#
# 退出码：0 注册并验过 / 1 不是管理员 / 2 注册失败 / 3 注册了但空跑没落地文件

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Task   = "Caelum-Moments-Shadow"
$Script = "D:\claude-code\scripts\moments-shadow-pull.ps1"
$OutDir = "D:\claude-code\scratch\moments-shadow"

# ---------------------------------------------------------------- 0. 前提
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Write-Host "🔴 不是管理员 —— schtasks /Create 会失败。"
  Write-Host "   右键 PowerShell「以管理员身份运行」再跑一次。"
  exit 1
}
if (-not (Test-Path $Script)) {
  Write-Host "🔴 找不到要跑的脚本：$Script"
  exit 2
}

# ---------------------------------------------------------------- 1. 注册
Write-Host "── 注册 $Task（每天 10:00）──"
$action = "powershell -NoProfile -ExecutionPolicy Bypass -File $Script"
& schtasks /Create /TN $Task /SC DAILY /ST 10:00 /F /TR $action
if ($LASTEXITCODE -ne 0) {
  Write-Host "🔴 注册失败（退出码 $LASTEXITCODE）"
  exit 2
}

# 🔴 判据挑退出码，不挑中文输出（控制台是 gb2312，文本判据编码一变就永不成立，
#    memory: dont-judge-success-by-text）
& schtasks /Query /TN $Task | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "🔴 /Create 说成功了，但 /Query 查不到这个任务 —— 不算注册上"
  exit 2
}
Write-Host "  /Query 查得到 ✓"
Write-Host ""

# ---------------------------------------------------------------- 2. 空跑一次
# 「注册上了」和「跑起来能出东西」是两件事。这一步验的是后者：
# 今天日期那个文件必须**在这次空跑之后**被写过。
$stamp  = Get-Date -Format "yyyy-MM-dd"
$expect = Join-Path $OutDir "$stamp.txt"
$before = if (Test-Path $expect) { (Get-Item $expect).LastWriteTime } else { [datetime]::MinValue }

Write-Host "── 空跑一次（schtasks /Run），最多等 3 分钟 ──"
& schtasks /Run /TN $Task | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "🔴 /Run 失败（退出码 $LASTEXITCODE）"
  exit 3
}

$ok = $false
foreach ($i in 1..18) {
  Start-Sleep -Seconds 10
  if ((Test-Path $expect) -and (Get-Item $expect).LastWriteTime -gt $before) { $ok = $true; break }
  Write-Host "  第 $($i*10) 秒：$stamp.txt 还没被写过，继续等"
}

if (-not $ok) {
  Write-Host "🔴 任务注册上了，但空跑 3 分钟没写出 $expect —— 任务在跑但没产出。"
  Write-Host "   去看：schtasks /Query /TN '$Task' /FO LIST /V 里的「上次运行结果」"
  exit 3
}

Write-Host ""
Write-Host "✅ 注册好了，而且空跑真写出了报告："
Write-Host "   $expect"
Write-Host ""
Get-Content (Join-Path $OutDir "latest.txt") -Encoding UTF8 | Select-Object -Last 20
exit 0
