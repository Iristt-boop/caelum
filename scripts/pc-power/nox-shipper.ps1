# 「上线」：把电脑上 Claude Code 做完的活合进主线，nox-core 的改动部署到 N150（2026-10-29）。
#
# 只有她在手机上点了上线卡才会跑到这里（N150 的确认端点 → relay.ps1 ship <id> → 计划任务以她的身份起本脚本）。
# 🔴 每一步都是写死的，不交给模型：
#   1. 把它的分支挪到主线最新（rebase），冲突就停
#   2. 改了 nox-core 就在它的 worktree 里跑一遍测试，没过就停
#   3. 要部署的文件逐个比对：线上那份必须和「改之前的版本」一模一样 —— 不一样说明线上有人动过，停
#   4. 以上都过了才快进合并（主工作区有没提交的改动碰到同一批文件，git 自己会拒，停）
#   5. 部署：文件从 git 里取（LF），拷到 N150 暂存 → 等她安静 3 分钟 → 备份旧文件、换、清 pycache、
#      重启 nox-core → 等 /health 200
#   只改了文档的：合进去就完。别的目录（bridge / nox-app / ha-mcp…）：合进去，不部署，告诉她要上电脑弄。

param([Parameter(Mandatory)][string]$Id)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:LC_ALL = 'C.UTF-8'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$jd = Join-Path $dir "jobs\$Id"
$utf8 = New-Object Text.UTF8Encoding $false
$N150 = 'root@192.168.1.52'
$LIVE = '/root/nox-core/code'
$PY = 'D:\claude-code\nox-core\.venv\Scripts\python.exe'
$sshOpts = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8')

$st = Get-Content "$jd\state.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$ship = Get-Content "$jd\ship.json" -Raw -Encoding UTF8 | ConvertFrom-Json
function Save-Ship { [IO.File]::WriteAllText("$jd\ship.json", ($ship | ConvertTo-Json -Compress -Depth 6), $utf8) }
function Set-Ship($k, $v) { $ship | Add-Member -NotePropertyName $k -NotePropertyValue $v -Force }
function Step($msg) { Set-Ship 'step' $msg; Save-Ship }
function N150([string]$cmd) {
    $out = & ssh.exe @sshOpts $N150 $cmd 2>&1
    if ($LASTEXITCODE -ne 0) { throw "N150 上没跑成（$cmd）：$($out | Select-Object -Last 3)" }
    return $out
}
function Sha1-File($path) { (Get-FileHash -Algorithm SHA1 $path).Hash.ToLower() }
#: git 里某个版本的文件原样落到磁盘（走 cmd 重定向：PowerShell 管道会按文本改坏字节）
function Git-Blob($repo, $rev, $path, $dest) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    cmd.exe /c "git -C `"$repo`" show `"${rev}:$path`" > `"$dest`""
    if ($LASTEXITCODE -ne 0) { throw "git 里取不到 ${rev}:$path" }
}

try {
    Set-Ship 'state' 'running'; Set-Ship 'started' (Get-Date -Format s); Save-Ship
    if ($st.state -ne 'done') { throw "这个活没做完（$($st.state)），不能上线" }
    $repo = $st.repo_path; $wt = $st.worktree; $br = $st.branch
    if (-not (Test-Path $wt)) { throw "它的 worktree 不在了（$wt）" }

    # 快进合并合的是主工作区当前检出的分支 —— 不是 master 就别合（她可能正在别的分支上干活）
    $cur = "$(git -C $repo symbolic-ref --short HEAD 2>$null)".Trim()
    if ($cur -ne 'master') { throw "主工作区现在不在 master 上（在 $cur），没合" }

    Step '把分支挪到主线最新'
    $master = (git -C $repo rev-parse master).Trim()
    git -C $wt rebase -q master 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        git -C $wt rebase --abort 2>&1 | Out-Null
        throw '它的改动和主线后来的提交冲突了，没合。要上电脑看'
    }
    $head = (git -C $wt rev-parse HEAD).Trim()
    $changes = @(git -C $wt diff --name-status --no-renames "$master..$head" | ForEach-Object {
        $p = $_ -split "`t"; [pscustomobject]@{ status = $p[0]; path = $p[1] } })
    if ($changes.Count -eq 0) { throw '分支上没有改动，没什么可上线的' }

    $isCaelum = ($repo -eq 'D:\claude-code')
    $core = @($changes | Where-Object { $isCaelum -and $_.path -like 'nox-core/*' })
    $deploy = @($core | Where-Object { $_.path -notlike 'nox-core/tests/*' -and $_.status -in 'A', 'M' })
    $manual = @($changes | Where-Object {
        -not ($_.path -like 'docs/*' -or $_.path -like 'scratch/*' -or $_.path -like '*.md' -or
              ($isCaelum -and $_.path -like 'nox-core/tests/*') -or ($deploy -contains $_)) })
    Set-Ship 'files' @($changes | ForEach-Object { "$($_.status) $($_.path)" })

    if ($core.Count -gt 0) {
        Step '跑 nox-core 的测试'
        Push-Location (Join-Path $wt 'nox-core')
        try {
            $env:PYTHONDONTWRITEBYTECODE = '1'
            cmd.exe /c "`"$PY`" -m pytest tests -q -x -p no:cacheprovider > `"$jd\ship-tests.txt`" 2>&1"
            $ok = ($LASTEXITCODE -eq 0)
        } finally { Pop-Location }
        $tail = (Get-Content "$jd\ship-tests.txt" -Tail 3 -Encoding UTF8) -join ' / '
        Set-Ship 'tests' $tail
        if (-not $ok) { throw "测试没过，没合：$tail" }
    }

    if ($deploy.Count -gt 0) {
        Step '比对线上的文件'
        $drift = @()
        foreach ($c in $deploy) {
            $rel = $c.path.Substring('nox-core/'.Length)
            $live = (N150 "sha1sum $LIVE/$rel 2>/dev/null | cut -c1-40 || true" | Select-Object -First 1)
            $live = "$live".Trim()
            if ($c.status -eq 'A') { $want = '' } else {
                $tmp = Join-Path $jd "base\$rel"
                Git-Blob $repo $master $c.path $tmp
                $want = Sha1-File $tmp
            }
            if ($live -ne $want) { $drift += $rel }
        }
        if ($drift.Count -gt 0) {
            throw "线上这几份和改之前的版本对不上（有人在线上动过，或者有没提交的线上改动），没合：$($drift -join '、')"
        }
    }

    Step '合进主线'
    $out = git -C $repo merge --ff-only -q $br 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "合不进去（主工作区里有没提交的改动碰到同一批文件）：$($out | Select-Object -Last 2)"
    }
    Set-Ship 'merged' $head

    if ($deploy.Count -gt 0) {
        Step '拷到 N150'
        $stage = Join-Path $jd 'stage'
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
        foreach ($c in $deploy) {
            Git-Blob $repo $head $c.path (Join-Path $stage ("files\" + $c.path.Substring('nox-core/'.Length)))
        }
        $rels = ($deploy | ForEach-Object { $_.path.Substring('nox-core/'.Length) }) -join ' '
        $remote = "/tmp/stage-ship-$Id"
        # 换文件的脚本：等她安静 3 分钟（最多等 60 分钟）→ 备份 → 换 → 清 pycache → 重启 → 等 health；结果写 result
        $swap = @"
#!/bin/bash
set -u
S=$remote; C=$LIVE; B=/root/backups/ship-$Id
for i in `$(seq 1 120); do
  idle=`$(python3 - <<'EOF'
import sqlite3, datetime
c = sqlite3.connect("file:/root/nox-core/data/sessions.db?mode=ro", uri=True)
last = c.execute("select max(created_at) from messages").fetchone()[0]
t = datetime.datetime.fromisoformat(last)
print(int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()))
EOF
)
  if [ "`$idle" -ge 180 ]; then
    mkdir -p `$B
    for f in $rels; do [ -f `$C/`$f ] && mkdir -p `$B/`$(dirname `$f) && cp `$C/`$f `$B/`$f; done
    cp -r `$S/files/. `$C/ || { echo "fail copy" > `$S/result; exit 1; }
    find `$C -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
    systemctl restart nox-core
    for j in `$(seq 1 60); do
      curl -s --noproxy '*' -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/health 2>/dev/null | grep -q 200 && { echo ok > `$S/result; exit 0; }
      sleep 2
    done
    echo "fail health" > `$S/result; exit 1
  fi
  sleep 30
done
echo "fail busy" > `$S/result; exit 3
"@
        [IO.File]::WriteAllText((Join-Path $stage 'swap.sh'), ($swap -replace "`r`n", "`n"), $utf8)
        N150 "rm -rf $remote && mkdir -p $remote" | Out-Null
        & scp.exe -q @sshOpts -r "$stage\files" "$stage\swap.sh" "${N150}:$remote/"
        if ($LASTEXITCODE -ne 0) { throw '拷到 N150 失败（已经合进主线了，线上没动）' }

        Step '等你安静 3 分钟再换、重启'
        N150 "chmod +x $remote/swap.sh && systemd-run --unit=ship-$Id --collect $remote/swap.sh" | Out-Null
        $res = ''
        for ($i = 0; $i -lt 140; $i++) {
            Start-Sleep 30
            $res = "$(N150 "cat $remote/result 2>/dev/null || true" | Select-Object -First 1)".Trim()
            if ($res) { break }
        }
        Set-Ship 'deploy' $res
        if ($res -ne 'ok') {
            throw "部署没成（$(if ($res) { $res } else { '一直没等到结果' })）。已经合进主线了；线上旧文件备份在 /root/backups/ship-$Id"
        }
        Set-Ship 'deployed' @($deploy | ForEach-Object { $_.path })
    }

    Set-Ship 'manual' @($manual | ForEach-Object { $_.path })
    # 合进去了，它的 worktree 就没用了（分支留着，已经在主线里）
    git -C $repo worktree remove --force $wt 2>&1 | Out-Null
    Set-Ship 'state' 'shipped'; Set-Ship 'finished' (Get-Date -Format s); Step '完成'
}
catch {
    Set-Ship 'state' 'failed'; Set-Ship 'error' "$($_.Exception.Message)"; Set-Ship 'finished' (Get-Date -Format s); Save-Ship
}
