# deploy-moments-body.ps1 —— 用**能用的那个 bash** 跑 deploy-moments-body.sh。
#
# 2026-09-21 立。为什么需要这一层壳：
# 糖糖点 Run 按钮跑 `bash …` 时，命令被送进了 WSL，而那个发行版里
# 连 /bin/bash 都没有：
#     WSL (13 - Relay) ERROR: execvpe(/bin/bash) failed: No such file or directory
# 这台机器上真正能用的 bash 是 Claude app 自带的 Git Bash
# （`%LOCALAPPDATA%\hermes\git\bin\bash.exe`），它不在 PATH 上。
#
# 🔴 必须把 System32\bash.exe 排除掉 —— 那就是 WSL 那个入口，
#    「找到了 bash」和「找到了能用的 bash」是两件事。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File D:\claude-code\scripts\deploy-moments-body.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File D:\claude-code\scripts\deploy-moments-body.ps1 -Wait
#
# 退出码：0 上线并验过 / 1 找不到能用的 bash / 其余原样透传 .sh 的退出码
#   （2 = 线上文件里没有 body=%s；3 = 没等到带 body= 的 tick）

param([switch]$Wait)

$ErrorActionPreference = "Stop"
# 控制台是 gb2312，脚本吐的是 UTF-8。不改这个中文全是乱码，
# 而「乱码」和「脚本挂了」在屏幕上长得很像
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Sh = "/d/claude-code/scripts/deploy-moments-body.sh"

# ---------------------------------------------------------------- 找 bash
# 顺序：app 自带的 Git Bash → 常规安装位置 → PATH 上的（最后才信）
$candidates = @(
  (Join-Path $env:LOCALAPPDATA "hermes\git\bin\bash.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe"),
  "C:\Program Files\Git\bin\bash.exe",
  "C:\Program Files (x86)\Git\bin\bash.exe"
)
foreach ($p in (& where.exe bash 2>$null)) { $candidates += $p }

# 🔴 判据：让候选自己去 `test -f` 那个 .sh，**看退出码**。
#
# 走了三版错的，全记在这儿省下次的力气：
#  ① 路径黑名单（排掉 System32\bash.exe）—— 这台机器上还有个
#     `%LOCALAPPDATA%\Microsoft\WindowsApps\bash.exe`，同样是 WSL，没挡住。
#     往下猜还有几个入口是猜不完的。
#  ② `bash -lc "echo __BASH_OK__"` 看输出 —— WSL 那两个也照样回，
#     echo 是内建，不用 spawn /bin/bash。判据是假的。
#  ③ 改成 `test -f … && echo __SEES_REPO__` 还是看输出 —— **更坏**：
#     WSL 失败时 PowerShell 的 NativeCommandError 会把**我自己那行命令**
#     回显进 stderr，里头就带着 `__SEES_REPO__` 这几个字，
#     于是 `-match` 恒为真，四个候选全判成「看得见」。
#
# 所以挑退出码，一个字都不匹配（memory: dont-judge-success-by-text）。
# 这一条同时也是真正要的条件：Git Bash 把 D: 映射成 /d/，WSL 映射成 /mnt/d/，
# 看不见就是不能用它跑。实测 WSL → 1，Git Bash → 0。
$bash = $null
$tried = @()
foreach ($c in $candidates) {
  if (-not $c) { continue }
  if (-not (Test-Path $c)) { $tried += "不在         $c"; continue }
  #: `*> $null` 把四路输出全丢掉 —— 不留输出就不会再有人想去匹配它。
  #: 也别加管道：管道会让 $LASTEXITCODE 变成管道末端的码
  #: （memory: pipe-eats-the-exit-code）
  & $c -lc "test -f '$Sh'" *> $null
  if ($LASTEXITCODE -eq 0) { $bash = $c; break }
  $tried += "看不见仓库   $c  ← 退出码 $LASTEXITCODE"
}

if (-not $bash) {
  Write-Host "🔴 没有一个 bash 看得见 $Sh。"
  Write-Host "   （WSL 那几个入口看不见 —— 它把 D: 映射成 /mnt/d/，而且这台机器上"
  Write-Host "     那个发行版里连 /bin/bash 都没有）"
  Write-Host "   试过这些："
  foreach ($t in $tried) { Write-Host "     $t" }
  exit 1
}
Write-Host "── 用这个 bash：$bash ──"
Write-Host ""

# ---------------------------------------------------------------- 跑
$argv = @("-lc", $(if ($Wait) { "bash '$Sh' --wait" } else { "bash '$Sh'" }))
& $bash @argv
$code = $LASTEXITCODE

Write-Host ""
# 判据挑**退出码**，不挑中文输出（memory: dont-judge-success-by-text）
switch ($code) {
  0 { Write-Host "✅ 退出码 0 —— 上线了，线上文件已回读验过" }
  1 { Write-Host "🔴 退出码 1 —— 部署或重启失败，线上还是旧的" }
  2 { Write-Host "🔴 退出码 2 —— 线上那份 record.py 里没有 body=%s：传上去的不是新代码，或者进了野目录" }
  3 { Write-Host "🔴 退出码 3 —— 没等到带 body= 的 tick，跑着的可能还是旧代码" }
  default { Write-Host "未知退出码 $code" }
}
exit $code
