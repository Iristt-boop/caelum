param(
    [int]$Port = 3080,
    [switch]$Register,
    [switch]$Unregister,
    [switch]$Stop
)

# DSH 自启动 / 后台运行脚本
#  - 默认 3080 端口，隐藏窗口后台运行，不依赖 cmd
#  - -Register    注册开机自启（计划任务 dsh-boot，登录时自动拉起）
#  - -Unregister  删除自启任务
#  - -Stop        停止对应端口的后台实例（按 pid 文件）
#  - -Port N      用别的端口启动（测试用）
# 底层启动逻辑在 scripts/dsh-boot.cjs（detached spawn，脱离 cmd 存活）

$ErrorActionPreference = "Stop"
$node = "C:\Users\14372\.workbuddy\binaries\node\versions\22.22.2\node.exe"
$boot = "D:\claude-code\scripts\dsh-boot.cjs"
$taskName = "dsh-boot"

if ($Stop) {
    & $node $boot stop $Port
    return
}

if ($Unregister) {
    schtasks /Delete /F /TN $taskName 2>&1 | Out-String | Write-Host
    Write-Host "unregistered: $taskName"
    return
}

Write-Host "== start DSH on :$Port (hidden background) =="
& $node $boot $Port

# 等待端口起来（dsh-boot.cjs 是异步的，这里轮询确认）
$deadline = (Get-Date).AddSeconds(35)
while ((Get-Date) -lt $deadline) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $up = $false
    try {
        $iar = $tcp.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        if ($ok -and $tcp.Connected) { $up = $true }
    } catch { $up = $false }
    finally { try { $tcp.Close() } catch {} }
    if ($up) {
        Write-Host "OK, DSH is up on http://127.0.0.1:$Port"
        break
    }
    Start-Sleep -Milliseconds 1200
}
if (-not $up) { Write-Host "TIMEOUT: :$Port 没起来，看 D:\claude-code\logs\dsh-boot.log" }

if ($Register) {
    Write-Host "== register auto-start =="
    schtasks /Create /F /TN $taskName `
        /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"D:\claude-code\scripts\dsh-start.ps1\"" `
        /SC ONLOGON /RL LIMITED 2>&1 | Out-String | Write-Host
    Write-Host "registered: $taskName (auto-start on login)"
}
