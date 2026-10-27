#Requires -RunAsAdministrator
# 让家里 N150 上的 Nox 能关这台电脑（2026-10-27）。糖糖用「管理员身份」跑一次就行，重复跑也没事。
#
# 做的事：
#   1. 装上 Windows 自带的 OpenSSH 服务端，开机自启
#   2. 只放一把钥匙进来（N150 上的 /root/.ssh/pc_power），而且这把钥匙：
#        - 只能从 N150（192.168.1.52）来
#        - 只能跑 pc-power.ps1：关机 / 睡眠 / 取消 / 看状态，别的命令进不了 shell
#        - 不能转发端口、不能开终端
#   3. 关掉密码登录（只认钥匙），防火墙只放 N150 一个地址进来
# 开机不归这里管：开机是 N150 发网络唤醒包，网卡那边已经开着了。

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pub = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFmu85DaOpfOdOXs/WmWL0K8kzaVmdT5QrFhzk/CSKu2 nox-n150-pc-power'
$n150 = '192.168.1.52'

Write-Host '[1/5] 装 OpenSSH 服务端（第一次可能要一两分钟）...'
# 🔴 10-27 实测：Windows「可选功能」里的服务端是 2019 年的 8.1，而系统更新把客户端换成了 9.5，
#    共用的组件对不上，老 sshd.exe 一启动就 0xC0000139（找不到入口点），服务超时起不来。
#    所以不用可选功能那份，换微软在 GitHub 维护的新版（winget：Microsoft.OpenSSH.Preview，装到 Program Files\OpenSSH）
if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State -eq 'Installed') {
    Write-Host '      先卸掉系统自带的老服务端（坏的那份）...'
    Stop-Service sshd -ErrorAction SilentlyContinue
    Remove-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
}
if (-not (Test-Path "$env:ProgramFiles\OpenSSH\sshd.exe")) {
    Write-Host '      用 winget 装新版 OpenSSH（从 GitHub 下，可能要等一会儿）...'
    winget install --id Microsoft.OpenSSH.Preview -e --source winget
    if (-not (Test-Path "$env:ProgramFiles\OpenSSH\sshd.exe")) { throw 'OpenSSH 没装上，把上面的输出截图给 Nox' }
}
Set-Service sshd -StartupType Automatic
Start-Service sshd   # 第一次启动会生成 sshd_config 和主机钥匙

Write-Host '[2/5] 放关机脚本...'
New-Item -ItemType Directory -Force 'C:\ProgramData\nox' | Out-Null
Copy-Item "$here\nox-pc-power.ps1" 'C:\ProgramData\nox\pc-power.ps1' -Force

Write-Host '[3/5] 放钥匙（只能从 N150 来、只能跑关机脚本）...'
$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\nox\pc-power.ps1'
$line = "restrict,from=`"$n150`",command=`"$cmd`" $pub"
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $ak -Value $line -Encoding Ascii
# sshd 要求这份文件只有 管理员组 + SYSTEM 能碰，不然整份忽略（用 SID，中文系统组名不一样）
icacls $ak /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null

Write-Host '[4/5] 关掉密码登录...'
$cfg = 'C:\ProgramData\ssh\sshd_config'
$text = Get-Content $cfg -Raw
$text = $text -replace '(?m)^#?\s*PasswordAuthentication\s+\S+', 'PasswordAuthentication no'
if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = "PasswordAuthentication no`r`n" + $text }
# 「键盘交互」也是一种输密码的路（10-27 实测拒绝时还列着 keyboard-interactive），一起关
$text = $text -replace '(?m)^#?\s*KbdInteractiveAuthentication\s+\S+', 'KbdInteractiveAuthentication no'
if ($text -notmatch '(?m)^KbdInteractiveAuthentication no') { $text = "KbdInteractiveAuthentication no`r`n" + $text }
Set-Content -Path $cfg -Value $text -Encoding Ascii

Write-Host '[5/5] 防火墙只放 N150 进来...'
$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
}
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled True -Profile Any -RemoteAddress $n150
# 安装包可能自己另建一条「22 端口谁都能进」的规则 —— 除了上面这条，别的放行 22 的入站规则都关掉
Get-NetFirewallPortFilter -Protocol TCP | Where-Object { $_.LocalPort -eq '22' } | Get-NetFirewallRule |
    Where-Object { $_.Name -ne 'OpenSSH-Server-In-TCP' -and $_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True' } |
    ForEach-Object { Write-Host "      关掉多余的规则：$($_.DisplayName)"; Disable-NetFirewallRule -Name $_.Name }

Restart-Service sshd
Write-Host ''
Write-Host '好了，乖。回去跟我说一声，我从 N150 那边试一下。' -ForegroundColor Green
