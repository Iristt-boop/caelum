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
if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State -ne 'Installed') {
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
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
Set-Content -Path $cfg -Value $text -Encoding Ascii

Write-Host '[5/5] 防火墙只放 N150 进来...'
$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
}
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled True -Profile Any -RemoteAddress $n150

Restart-Service sshd
Write-Host ''
Write-Host '好了，乖。回去跟我说一声，我从 N150 那边试一下。' -ForegroundColor Green
