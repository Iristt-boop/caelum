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
#
# 卡上的状态栏（她 10-29 定）读 ship.json 的 step：**一句人话、写清楚在等什么、还要多久**，不带分支名。
# 重试（她在卡上点「重试」）：上次已经合进主线了的，跳过 1-4，直接部署那一批（线上是改前或改后版本都认）。

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
#: 🔴 core.quotepath=false：不然中文文件名被 git 转成 "docs/Nox-\345..." 带引号的样子，
#:    认不出是 docs/，被当成「要她上电脑弄」（10-29 第一次上线，他就这么跟她说了）
function Get-Changes($repo, $from, $to) {
    @(git -C $repo -c core.quotepath=false diff --name-status --no-renames "$from..$to" | ForEach-Object {
        $p = $_ -split "`t"; [pscustomobject]@{ status = $p[0]; path = $p[1] } })
}

try {
    Set-Ship 'state' 'running'; Set-Ship 'started' (Get-Date -Format s); Set-Ship 'error' $null; Save-Ship
    if ($st.state -ne 'done') { throw "这个活没做完，不能上线" }
    $repo = $st.repo_path; $wt = $st.worktree; $br = $st.branch
    $isCaelum = ($repo -eq 'D:\claude-code')

    # 重试：上次已经合进主线了 → 只补部署
    $retry = $false
    if ($ship.merged -and $ship.base) {
        git -C $repo merge-base --is-ancestor $ship.merged master 2>$null
        $retry = ($LASTEXITCODE -eq 0)
    }

    if ($retry) {
        $master = $ship.base; $head = $ship.merged
        $changes = Get-Changes $repo $master $head
    } else {
        if (-not (Test-Path $wt)) { throw "它改的那份副本不在了，没法上线" }
        # 快进合并合的是主工作区当前检出的分支 —— 不是 master 就别合（她可能正在别的分支上干活）
        $cur = "$(git -C $repo symbolic-ref --short HEAD 2>$null)".Trim()
        if ($cur -ne 'master') { throw "电脑上的主工作区现在不在主线上（在 $cur），没合" }

        Step '正在把改动接到主线最新…'
        $master = (git -C $repo rev-parse master).Trim()
        git -C $wt rebase -q master 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            git -C $wt rebase --abort 2>&1 | Out-Null
            throw '它的改动和主线后来的提交冲突了，没合，要上电脑看'
        }
        $head = (git -C $wt rev-parse HEAD).Trim()
        $changes = Get-Changes $wt $master $head
        if ($changes.Count -eq 0) { throw '分支上没有改动，没什么可上线的' }
    }

    $core = @($changes | Where-Object { $isCaelum -and $_.path -like 'nox-core/*' })
    $deploy = @($core | Where-Object { $_.path -notlike 'nox-core/tests/*' -and $_.status -in 'A', 'M' })
    $manual = @($changes | Where-Object {
        -not ($_.path -like 'docs/*' -or $_.path -like 'scratch/*' -or $_.path -like '*.md' -or
              ($isCaelum -and $_.path -like 'nox-core/tests/*') -or ($deploy -contains $_)) })
    Set-Ship 'files' @($changes | ForEach-Object { "$($_.status) $($_.path)" })

    if (-not $retry -and $core.Count -gt 0) {
        Step '正在跑 nox-core 的全量测试（大约 4 分钟）…'
        Push-Location (Join-Path $wt 'nox-core')
        try {
            $env:PYTHONDONTWRITEBYTECODE = '1'
            cmd.exe /c "`"$PY`" -m pytest tests -q -x -p no:cacheprovider > `"$jd\ship-tests.txt`" 2>&1"
            $ok = ($LASTEXITCODE -eq 0)
        } finally { Pop-Location }
        $tail = (Get-Content "$jd\ship-tests.txt" -Tail 3 -Encoding UTF8) -join ' / '
        Set-Ship 'tests' $tail
        if (-not $ok) { throw "测试没过，没合进主线" }
    }

    if ($deploy.Count -gt 0) {
        Step '正在核对线上的文件…'
        $drift = @()
        foreach ($c in $deploy) {
            $rel = $c.path.Substring('nox-core/'.Length)
            $live = "$(N150 "sha1sum $LIVE/$rel 2>/dev/null | cut -c1-40 || true" | Select-Object -First 1)".Trim()
            if ($c.status -eq 'A') { $want = @('') } else {
                $tmp = Join-Path $jd "base\$rel"
                Git-Blob $repo $master $c.path $tmp
                $want = @(Sha1-File $tmp)
            }
            if ($retry) {   # 上次可能已经换上了、只是没重启成 —— 改后版本也认
                $tmp2 = Join-Path $jd "new\$rel"
                Git-Blob $repo $head $c.path $tmp2
                $want += Sha1-File $tmp2
            }
            if ($want -notcontains $live) { $drift += $rel }
        }
        if ($drift.Count -gt 0) {
            throw "线上有 $($drift.Count) 个文件和改之前对不上（有人在线上动过），没合"
        }
    }

    if (-not $retry) {
        Step '正在合入主线…'
        $out = git -C $repo merge --ff-only -q $br 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "合不进主线：电脑上有没提交的改动碰到同一批文件"
        }
        Set-Ship 'base' $master; Set-Ship 'merged' $head; Save-Ship
    }

    if ($deploy.Count -gt 0) {
        Step '正在部署到 N150…'
        $stage = Join-Path $jd 'stage'
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
        foreach ($c in $deploy) {
            Git-Blob $repo $head $c.path (Join-Path $stage ("files\" + $c.path.Substring('nox-core/'.Length)))
        }
        $rels = ($deploy | ForEach-Object { $_.path.Substring('nox-core/'.Length) }) -join ' '
        $remote = "/tmp/stage-ship-$Id"
        # 换文件的脚本：等她安静 3 分钟（最多等 60 分钟）→ 备份 → 换 → 清 pycache → 重启 → 等 health
        # progress：wait <还要几秒> / restart —— 卡上的状态栏据此说「在等什么、还要多久」；结果写 result
        $swap = @"
#!/bin/bash
set -u
S=$remote; C=$LIVE; B=/root/backups/ship-$Id
for i in `$(seq 1 360); do
  idle=`$(python3 - <<'EOF'
import sqlite3, datetime
c = sqlite3.connect("file:/root/nox-core/data/sessions.db?mode=ro", uri=True)
last = c.execute("select max(created_at) from messages").fetchone()[0]
t = datetime.datetime.fromisoformat(last)
print(int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()))
EOF
)
  if [ "`$idle" -ge 180 ]; then
    echo restart > `$S/progress
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
  echo "wait `$((180 - idle))" > `$S/progress
  sleep 10
done
echo "fail busy" > `$S/result; exit 3
"@
        [IO.File]::WriteAllText((Join-Path $stage 'swap.sh'), ($swap -replace "`r`n", "`n"), $utf8)
        N150 "rm -rf $remote && mkdir -p $remote" | Out-Null
        & scp.exe -q @sshOpts -r "$stage\files" "$stage\swap.sh" "${N150}:$remote/"
        if ($LASTEXITCODE -ne 0) { throw '传到 N150 失败（已经合进主线了，线上没动）' }

        N150 "chmod +x $remote/swap.sh && systemd-run --unit=ship-$Id --collect $remote/swap.sh" | Out-Null
        $res = ''
        for ($i = 0; $i -lt 450; $i++) {   # 最多约 75 分钟（swap 自己最多等 60 分钟）
            Start-Sleep 10
            $res = "$(N150 "cat $remote/result 2>/dev/null || true" | Select-Object -First 1)".Trim()
            if ($res) { break }
            $pg = "$(N150 "cat $remote/progress 2>/dev/null || true" | Select-Object -First 1)".Trim()
            if ($pg -eq 'restart') {
                Step '正在重启 nox-core，会短暂离线半分钟左右…'
            } elseif ($pg -match '^wait (\d+)') {
                $left = [math]::Max(1, [math]::Ceiling([int]$Matches[1] / 60))
                Step "已传到 N150，等你安静 3 分钟后换上、重启 nox-core（还要约 $left 分钟，你一说话就重新等）…"
            }
        }
        Set-Ship 'deploy' $res
        if ($res -ne 'ok') {
            $why = switch -Wildcard ($res) {
                'fail busy' { '等了一个小时你一直在说话，没找到空当重启' }
                'fail health' { '换上以后 nox-core 没起来（旧文件备份在 N150 上）' }
                'fail copy' { 'N150 上换文件失败，线上没动' }
                default { '一直没等到 N150 回话' }
            }
            throw "已经合进主线了，但部署没成：$why"
        }
        Set-Ship 'deployed' @($deploy | ForEach-Object { $_.path })
    }

    Set-Ship 'manual' @($manual | ForEach-Object { $_.path })
    # 合进去了，它的 worktree 就没用了（分支留着，已经在主线里）
    if (Test-Path $wt) { git -C $repo worktree remove --force $wt 2>&1 | Out-Null }
    Set-Ship 'step' $null; Set-Ship 'finished' (Get-Date -Format s); Set-Ship 'state' 'shipped'; Save-Ship
}
catch {
    Set-Ship 'state' 'failed'; Set-Ship 'step' $null; Set-Ship 'error' "$($_.Exception.Message)"; Set-Ship 'finished' (Get-Date -Format s); Save-Ship
}
