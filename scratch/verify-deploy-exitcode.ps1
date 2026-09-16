# Why the first version of this check was wrong, and what actually works.
#
# Invoke-Remote appends "`n: # end" to absorb the CR that PS 5.1 tacks onto
# the last line it writes to a native process's stdin.
#
# My first test sent `echo ...; exit 1` -- `exit` kills the shell, so the
# appended `:` never ran and the code propagated. THAT IS NOT THE REAL SHAPE.
# The real script is `bash deploy-remote.sh ...`: a CHILD exits 1, the outer
# shell keeps going, runs `:` (exit 0), and ssh reports 0.
# So every deploy looked successful -- the mirror image of the old bug.
#
# ASCII-only on purpose (a BOM-less .ps1 is decoded as GBK by PS 5.1).
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Key = "C:\Users\14372\.ssh\id_ed25519"
$SshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
$Vps = "root@43.133.211.140"

function Send([string]$Script, [string]$Tail) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $null = (($Script -replace "`r", "") + $Tail) | ssh @SshOpts $Vps "bash -s" 2>&1
    return $LASTEXITCODE
  }
  finally { $ErrorActionPreference = $prev }
}

$OLD = "`n: # end"
$NEW = "`n__rc=`$?`nexit `$__rc  # end"

# The real shape: a CHILD script exits non-zero.
$childFails = "bash -c 'exit 1'"
$childOk    = "bash -c 'exit 0'"

Write-Host ("REAL SHAPE (child exits 1)  old tail -> " + (Send $childFails $OLD) + "   want 1")
Write-Host ("REAL SHAPE (child exits 1)  new tail -> " + (Send $childFails $NEW) + "   want 1")
Write-Host ("REAL SHAPE (child exits 0)  new tail -> " + (Send $childOk    $NEW) + "   want 0")

# And the CR really does need absorbing: prove the tail still protects the payload.
$crProof = "printf 'ok\n' | head -n 1"
Write-Host ("CR guard still holds        new tail -> " + (Send $crProof $NEW) + "   want 0")

$a = Send $childFails $NEW
$b = Send $childOk $NEW
if ($a -eq 1 -and $b -eq 0) { Write-Host "VERDICT: new tail propagates the child's exit code" }
else { Write-Host "VERDICT: BROKEN"; exit 1 }
