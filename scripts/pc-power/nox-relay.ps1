# Nox 从 N150 把活转给糖糖电脑上的 Claude Code（2026-10-27，她定：默认 Claude Code、停在分支、只在 Caelum 仓库）。
#
# 第二把钥匙（N150 的 /root/.ssh/pc_relay）带 command= 强制跑本脚本，只认：
#   start <id> <repo>   任务正文从 stdin 进（UTF-8）；repo 只能是 caelum / nox-app
#   status <id>         这个活现在怎样（state.json + 做完了的总结）
#   list                最近 10 个活
#   cancel <id>         掐掉
# 真正干活的是 runner.ps1：用计划任务（S4U，以她的身份、不存密码）起，ssh 断了也接着跑。
# 装在 C:\ProgramData\nox\relay.ps1（setup-pc-power.ps1 拷过去）。

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = [Text.Encoding]::UTF8
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$jobs = Join-Path $dir 'jobs'
$cmd = "$env:SSH_ORIGINAL_COMMAND".Trim()
Add-Content -Path "$dir\relay.log" -Value "$(Get-Date -Format s) $cmd" -Encoding UTF8

#: 能动手的仓库只有这两个（她 10-27 选「只在 Caelum 的仓库里」）
$REPOS = @{ 'caelum' = 'D:\claude-code'; 'nox-app' = 'D:\claude-code\nox-app' }
$ID = '[a-z0-9][a-z0-9-]{5,39}'

function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 5 }
function Read-State($id) {
    $f = Join-Path $jobs "$id\state.json"
    if (-not (Test-Path $f)) { return $null }
    Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
}

# 收尾了的活，计划任务顺手清掉（runner 以她的普通身份跑，删不掉管理员建的计划任务）
@(Get-ScheduledTask -TaskName 'NoxJob-*' -ErrorAction SilentlyContinue) + @(Get-ScheduledTask -TaskName 'NoxShip-*' -ErrorAction SilentlyContinue) |
    Where-Object { $_ -and $_.State -ne 'Running' } | ForEach-Object {
    $s = Read-State ($_.TaskName -replace '^Nox(Job|Ship)-', '')
    if ($null -eq $s -or $s.state -in 'done', 'failed', 'cancelled') {   # 上线任务：活本身是 done，跑完了也一样清
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

switch -Regex ($cmd) {
    "^start ($ID) (caelum|nox-app)$" {
        $id = $Matches[1]; $repo = $Matches[2]
        $jd = Join-Path $jobs $id
        if (Test-Path $jd) { Out-Json @{ ok = $false; error = 'id 已经用过了' }; exit 2 }
        # 按字节读再按 UTF-8 解：不吃控制台代码页（10-27 本机管道测试时中文全成了 ?）
        $ms = New-Object IO.MemoryStream
        [Console]::OpenStandardInput().CopyTo($ms)
        $task = [Text.Encoding]::UTF8.GetString($ms.ToArray()).TrimStart([char]0xFEFF).Trim()
        if ($task.Length -lt 2 -or $task.Length -gt 8000) { Out-Json @{ ok = $false; error = '任务正文太短或太长' }; exit 2 }
        New-Item -ItemType Directory -Force $jd | Out-Null
        [IO.File]::WriteAllText("$jd\task.txt", $task, (New-Object Text.UTF8Encoding $false))
        $st = @{ id = $id; repo = $repo; repo_path = $REPOS[$repo]; state = 'queued'; created = (Get-Date -Format s) }
        [IO.File]::WriteAllText("$jd\state.json", (Out-Json $st), (New-Object Text.UTF8Encoding $false))
        # 以她的身份跑（S4U：她登没登录都能跑，不存密码、不带网络凭据 —— 只要本机文件 + HTTPS 就够）
        $user = (Get-CimInstance Win32_ComputerSystem).UserName
        if (-not $user) { $user = "$env:USERDOMAIN\$env:USERNAME" }
        $a = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\runner.ps1`" -Id $id" -WorkingDirectory $jd
        $p = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
        $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 90) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName "NoxJob-$id" -Action $a -Principal $p -Settings $s -Force | Out-Null
        Start-ScheduledTask -TaskName "NoxJob-$id"
        Out-Json @{ ok = $true; id = $id; repo = $repo }
    }
    "^status ($ID)$" {
        $id = $Matches[1]
        $st = Read-State $id
        if ($null -eq $st) { Out-Json @{ ok = $false; error = '没有这个活' }; exit 2 }
        $res = Join-Path $jobs "$id\result.json"
        $out = @{ ok = $true; state = $st }
        if (Test-Path $res) { $out.result = Get-Content $res -Raw -Encoding UTF8 | ConvertFrom-Json }
        $shp = Join-Path $jobs "$id\ship.json"
        if (Test-Path $shp) { $out.ship = Get-Content $shp -Raw -Encoding UTF8 | ConvertFrom-Json }
        Out-Json $out
    }
    "^ship ($ID)$" {
        # 上线（10-29）：只有她在手机上点了上线卡，N150 的确认端点才会发这个。合并 + 部署是 shipper.ps1 写死的步骤
        $id = $Matches[1]
        $st = Read-State $id
        if ($null -eq $st) { Out-Json @{ ok = $false; error = '没有这个活' }; exit 2 }
        if ($st.state -ne 'done') { Out-Json @{ ok = $false; error = "这个活没做完（$($st.state)），不能上线" }; exit 2 }
        $shp = Join-Path $jobs "$id\ship.json"
        $new = @{ state = 'queued'; created = (Get-Date -Format s); step = '正在叫电脑开始上线…' }
        if (Test-Path $shp) {
            $old = Get-Content $shp -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($old.state -in 'queued', 'running', 'shipped') { Out-Json @{ ok = $false; error = "已经在上线 / 上过了（$($old.state)）" }; exit 2 }
            # 重试：上次已经合进主线的，记着合了什么 —— shipper 据此只补部署、不再合一遍
            if ($old.merged) { $new.merged = $old.merged; $new.base = $old.base; $new.attempt = [int]$old.attempt + 1 }
        }
        [IO.File]::WriteAllText($shp, (Out-Json $new), (New-Object Text.UTF8Encoding $false))
        $user = (Get-CimInstance Win32_ComputerSystem).UserName
        if (-not $user) { $user = "$env:USERDOMAIN\$env:USERNAME" }
        $a = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\shipper.ps1`" -Id $id" -WorkingDirectory (Join-Path $jobs $id)
        $p = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
        $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 120) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName "NoxShip-$id" -Action $a -Principal $p -Settings $s -Force | Out-Null
        Start-ScheduledTask -TaskName "NoxShip-$id"
        Out-Json @{ ok = $true; id = $id; state = 'queued' }
    }
    '^list$' {
        $items = @(Get-ChildItem $jobs -Directory -ErrorAction SilentlyContinue | Sort-Object CreationTime -Descending |
            Select-Object -First 10 | ForEach-Object { Read-State $_.Name } | Where-Object { $_ })
        Out-Json @{ ok = $true; items = $items }
    }
    "^cancel ($ID)$" {
        $id = $Matches[1]
        $st = Read-State $id
        if ($null -eq $st) { Out-Json @{ ok = $false; error = '没有这个活' }; exit 2 }
        Stop-ScheduledTask -TaskName "NoxJob-$id" -ErrorAction SilentlyContinue
        if ($st.pid) { taskkill.exe /T /F /PID $st.pid 2>$null | Out-Null }
        if ($st.state -in 'queued', 'running') {
            $st.state = 'cancelled'
            [IO.File]::WriteAllText((Join-Path $jobs "$id\state.json"), (Out-Json $st), (New-Object Text.UTF8Encoding $false))
        }
        Out-Json @{ ok = $true; state = $st.state }
    }
    default {
        Out-Json @{ ok = $false; error = 'unknown command' }
        exit 2
    }
}
