# 桌面图标专用：启动 DSH（若未运行）并打开页面，无窗口无感
# 由快捷方式以 -WindowStyle Hidden 调用
$ErrorActionPreference = "SilentlyContinue"

# 1) 确保 DSH 在跑（已在运行则 dsh-boot.cjs 自动跳过）
& "C:\Users\14372\.workbuddy\binaries\node\versions\22.22.2\node.exe" "D:\claude-code\scripts\dsh-boot.cjs" 3080

# 2) 等端口起来（最多 40 秒，已在跑则立即）
$deadline = (Get-Date).AddSeconds(40)
$up = $false
do {
    Start-Sleep -Milliseconds 800
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $tcp.BeginConnect("127.0.0.1", 3080, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(600)
        if ($ok -and $tcp.Connected) { $up = $true }
    } catch {} finally { try { $tcp.Close() } catch {} }
} while (-not $up -and (Get-Date) -lt $deadline)

# 3) 打开页面
if ($up) { Start-Process "http://127.0.0.1:3080" }
