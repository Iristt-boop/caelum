# Moments shadow 的每日拉取 —— **不经过任何模型**。
#
# 为什么不用 Claude Code 的定时任务（2026-09-15）：
# 那条路起的新会话拿不到 `~/.claude/settings.json` 里那套自定义凭据，
# 用的是 app 自己的订阅身份，于是 `glm-5.3-flash` / `deepseek-*` 一律报
# 「may not exist or you may not have access to it」——
# 换了两个厂商、两把 key、四个模型名，错误一模一样。
# 而同一个端点用同一把 key 直接打 /v1/messages 是 200。
#
# 这份汇总本来就是确定性脚本，不需要 LLM。所以交给 Windows 计划任务。
#
# 注册（管理员 PowerShell 跑一次）：
#   schtasks /Create /TN "Caelum-Moments-Shadow" /SC DAILY /ST 10:00 /F `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\claude-code\scripts\moments-shadow-pull.ps1"
# 手动跑一次：
#   schtasks /Run /TN "Caelum-Moments-Shadow"
# 取消：
#   schtasks /Delete /TN "Caelum-Moments-Shadow" /F

$ErrorActionPreference = "Stop"

# 🔴 控制台是 gb2312，远端吐的是 UTF-8。不改这个，报告里的中文全是乱码，
# 而「乱码」和「脚本挂了」在文件里长得很像（deploy.ps1 也栽过同一个坑）。
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$env:PYTHONIOENCODING = "utf-8"

$Key     = "$env:USERPROFILE\.ssh\id_ed25519"
# 🔴 认名字不认 IP：43.133.211.140 已经不是这台机器了（2026-09-15 踩过）
$Vps     = "root@noxtang.com"
$OutDir  = "D:\claude-code\scratch\moments-shadow"
$Local   = "D:\claude-code\.claude\worktrees\awesome-sanderson-1e4889\scripts\moments-shadow-digest.py"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$file  = Join-Path $OutDir "$stamp.txt"

$header = "── 拉取时间 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ──"

# 本机有更新的脚本就先推上去；没有就用 VPS 上那份（别因此失败）
if (Test-Path $Local) {
  & scp -i $Key -o BatchMode=yes -q $Local "${Vps}:/root/moments-shadow-digest.py" 2>&1 | Out-Null
}

$ErrorActionPreference = "Continue"   # native 命令写 stderr 不该当成终止错误
$out  = & ssh -i $Key -o BatchMode=yes -o ConnectTimeout=20 $Vps `
        "python3 /root/moments-shadow-digest.py --since '24 hours ago'" 2>&1
$code = $LASTEXITCODE

# 🔴 判据挑**退出码**，不挑中文输出（编码一变文本判据就永不成立）
$verdict = switch ($code) {
  0       { "正常" }
  1       { "🔴 shadow 期间库里出现了 moment 帖 —— 这是 bug，去看 moments/record.py 的结构闸门" }
  2       { "记录太少，不下结论（不是「他没想发」，是数据不够）" }
  3       { "journalctl 读不到（unit 名 / 权限）" }
  255     { "ssh 连不上" }
  default { "未知退出码 $code" }
}

$body = @($header, ($out | Out-String), "── 退出码 $code：$verdict ──") -join "`n"
[System.IO.File]::WriteAllText($file, $body, (New-Object System.Text.UTF8Encoding $false))
Copy-Item $file (Join-Path $OutDir "latest.txt") -Force

Write-Host $body
exit $code
