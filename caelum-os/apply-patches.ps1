# apply-patches.ps1 - Caelum OS kernel patch replay (one-shot)
#
# Reapplies our kernel changes after an upstream harness upgrade (git pull /
# version switch). Reads patches\*.patch (git diff snapshots) and applies each
# with git apply; already-applied patches are skipped (idempotent).
#
# Usage:
#   .\apply-patches.ps1                       # default harness at D:\deepseek-harness
#   .\apply-patches.ps1 -HarnessPath <path>   # custom harness path
#
# Adding a new patch:
#   1. Edit code in the harness (D:\deepseek-harness)
#   2. git -C D:\deepseek-harness diff -- <changed files> > caelum-os\patches\NNNN-desc.patch
#   3. Run this script to verify

param(
    [string]$HarnessPath = "D:\deepseek-harness"
)

# NOTE: must stay "Continue". With "Stop", PowerShell 5.1 turns any native
# command's stderr write (e.g. git apply failing a forward check) into a
# terminating NativeCommandError, even when redirected to $null. We drive all
# success/failure off $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$PatchDir = Join-Path $PSScriptRoot "patches"

if (-not (Test-Path (Join-Path $HarnessPath ".git"))) {
    Write-Host "[ERROR] Not a git repo: $HarnessPath"
    exit 1
}

$patches = @(Get-ChildItem -Path $PatchDir -Filter "*.patch" -ErrorAction SilentlyContinue | Sort-Object Name)
if ($patches.Count -eq 0) {
    Write-Host "No patches to replay: $PatchDir"
    exit 0
}

$failed = @()
foreach ($p in $patches) {
    # Forward check: does the patch apply cleanly (i.e. not yet applied)?
    & git -C $HarnessPath apply --check $p.FullName 2>$null
    $forward = $LASTEXITCODE

    if ($forward -eq 0) {
        & git -C $HarnessPath apply $p.FullName 2>$null
        $applyCode = $LASTEXITCODE
        if ($applyCode -eq 0) {
            Write-Host "[OK]   applied   $($p.Name)"
        } else {
            $failed += $p.Name
            Write-Host "[FAIL] apply failed $($p.Name)"
        }
    } else {
        # Forward check failed; confirm it is already applied via reverse check.
        & git -C $HarnessPath apply --check --reverse $p.FullName 2>$null
        $reverse = $LASTEXITCODE
        if ($reverse -eq 0) {
            Write-Host "[SKIP] already applied $($p.Name)"
        } else {
            $failed += $p.Name
            Write-Host "[FAIL] neither applies nor matches applied state $($p.Name)"
        }
    }
}

if ($failed.Count -gt 0) {
    Write-Host "[ERROR] Patch replay failed (likely context conflict, resolve manually): $($failed -join ', ')"
    exit 1
}

Write-Host "Done: processed $($patches.Count) patch(es)."
