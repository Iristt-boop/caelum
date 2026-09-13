# Verify the new success check is wired to a real signal, without touching prod.
# ASCII-only on purpose (a BOM-less .ps1 is decoded as GBK by PS 5.1).
#
# What it proves:
#   1. a remote `exit 0` comes back as RemoteExit 0   -> deploy reported OK
#   2. a remote `exit 1` comes back as RemoteExit 1   -> deploy reported FAIL
# deploy-remote.sh already exits 0 on success and 1 on every failure path,
# so this is the same signal the real deploy now reads.
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Key = "C:\Users\14372\.ssh\id_ed25519"
$SshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
$Vps = "root@43.133.211.140"

function Try-Remote([string]$Script) {
  $Script = ($Script -replace "`r", "") + "`n: # end"
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = $Script | ssh @SshOpts $Vps "bash -s" 2>&1
    $code = $LASTEXITCODE
    return @{ out = (($out | ForEach-Object { $_.ToString() }) -join "`n"); code = $code }
  }
  finally { $ErrorActionPreference = $prev }
}

$ok = Try-Remote "echo 'pretend success'; exit 0"
Write-Host ("success path -> RemoteExit=" + $ok.code + "  (want 0)")

$bad = Try-Remote "echo 'pretend rollback'; exit 1"
Write-Host ("failure path -> RemoteExit=" + $bad.code + "  (want 1)")

# And the old check, for the record: does the Chinese marker survive the pipe now?
$cn = Try-Remote "printf '%s\n' '-- nox-core X --' | sed 's/X/\xe9\x83\xa8\xe7\xbd\xb2\xe5\xae\x8c\xe6\x88\x90/'"
$marker = [string][char]0x90E8 + [char]0x7F72 + [char]0x5B8C + [char]0x6210
Write-Host ("old string check now matches: " + ($cn.out -match $marker))

if ($ok.code -eq 0 -and $bad.code -eq 1) { Write-Host "VERDICT: exit code is a real signal" }
else { Write-Host "VERDICT: BROKEN"; exit 1 }
