# 把 `claude setup-token` 给的订阅 token 存起来，给后台转过去的活用（2026-10-27）。
# 糖糖自己跑（普通终端就行）：粘贴时屏幕上不显示，存到 %USERPROFILE%\.nox\claude-token，只有她自己能读。
# Nox 不经手、也看不到这个 token。

$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:USERPROFILE '.nox'
New-Item -ItemType Directory -Force $dir | Out-Null
$sec = Read-Host '把 claude setup-token 打出来的 token 粘贴到这里（不会显示），回车' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ($plain -notmatch '^sk-ant-') { throw '这看起来不像 token（应该以 sk-ant- 开头），没存' }
$f = Join-Path $dir 'claude-token'
[IO.File]::WriteAllText($f, $plain, (New-Object Text.UTF8Encoding $false))
$me = "$env:USERDOMAIN\$env:USERNAME"
icacls $f /inheritance:r /grant "${me}:F" | Out-Null
Write-Host "存好了：$f（只有你能读）" -ForegroundColor Green
