# Nox 从 N150 开关糖糖电脑时，电脑这边只认这四个字（2026-10-27）。
#
# administrators_authorized_keys 里那把钥匙带 command= 强制跑本脚本：
# ssh 那头传什么命令都进不了 shell，只会落到下面的 switch 里 —— 认不得的一律拒。
# 装在 C:\ProgramData\nox\pc-power.ps1（setup-pc-power.ps1 拷过去），每次调用留一行日志。

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cmd = "$env:SSH_ORIGINAL_COMMAND".Trim()
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path   # 日志就写在脚本旁边
Add-Content -Path "$dir\pc-power.log" -Value "$(Get-Date -Format s) $cmd" -Encoding UTF8

function Out-Json($o) { $o | ConvertTo-Json -Compress }

switch -Regex ($cmd) {
    '^status$' {
        $os = Get-CimInstance Win32_OperatingSystem
        $user = (Get-CimInstance Win32_ComputerSystem).UserName
        # LogonUI 在跑 = 锁屏或者停在登录界面
        $locked = [bool](Get-Process LogonUI -ErrorAction SilentlyContinue)
        Out-Json @{ ok = $true; boot = $os.LastBootUpTime.ToString('s'); user = $user; locked = $locked }
    }
    '^shutdown (\d{1,3})$' {
        $t = [Math]::Min([int]$Matches[1], 600)
        shutdown.exe /s /t $t /c "Nox：$t 秒后关机。不想关就跟我说一声，我马上取消。"
        Out-Json @{ ok = ($LASTEXITCODE -eq 0); code = $LASTEXITCODE; delay = $t }
    }
    '^sleep$' {
        # 不能在这条 ssh 会话里直接睡：会话一断，子进程可能跟着被收走，回包也送不回去。
        # 交给计划任务（SYSTEM），10 秒后睡；任务睡完自己删
        $a = New-ScheduledTaskAction -Execute 'rundll32.exe' -Argument 'powrprof.dll,SetSuspendState 0,1,0'
        $tr = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10)
        $tr.EndBoundary = (Get-Date).AddMinutes(5).ToString('s')
        $s = New-ScheduledTaskSettingsSet -DeleteExpiredTaskAfter (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries
        Register-ScheduledTask -TaskName 'NoxSleep' -Action $a -Trigger $tr -Settings $s -User 'SYSTEM' -Force | Out-Null
        Out-Json @{ ok = $true; delay = 10 }
    }
    '^cancel$' {
        shutdown.exe /a
        Unregister-ScheduledTask -TaskName 'NoxSleep' -Confirm:$false -ErrorAction SilentlyContinue
        # 1116 = 没有正在倒计时的关机，也算取消成功
        Out-Json @{ ok = ($LASTEXITCODE -in 0, 1116); code = $LASTEXITCODE }
    }
    default {
        Out-Json @{ ok = $false; error = 'unknown command' }
        exit 2
    }
}
