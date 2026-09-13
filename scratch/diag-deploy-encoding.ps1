# Diagnose why deploy.ps1's success check misses the remote's Chinese marker.
# ASCII-only on purpose: a BOM-less .ps1 is decoded as GBK by PS 5.1.
$Key = "C:\Users\14372\.ssh\id_ed25519"
$SshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")

# The remote prints the exact marker deploy-remote.sh prints.
$script = "printf '%s\n' '-- nox-core BUSHU_WANCHENG --' | sed 's/BUSHU_WANCHENG/\xe9\x83\xa8\xe7\xbd\xb2\xe5\xae\x8c\xe6\x88\x90/'" + "`n: # end"

$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$out = $script | ssh @SshOpts root@43.133.211.140 "bash -s" 2>&1
$ErrorActionPreference = $prev

$s = ($out | ForEach-Object { $_.ToString() }) -join "`n"

Write-Host ("Console.OutputEncoding : " + [Console]::OutputEncoding.WebName)
Write-Host ("chars received         : " + (($s.ToCharArray() | ForEach-Object { [int]$_ }) -join ","))
# U+90E8 U+7F72 U+5B8C U+6210 is the marker; build it without literal CJK in this file
$marker = [string][char]0x90E8 + [char]0x7F72 + [char]0x5B8C + [char]0x6210
Write-Host ("marker codepoints      : 90E8,7F72,5B8C,6210")
Write-Host ("MATCHES                : " + ($s -match $marker))
