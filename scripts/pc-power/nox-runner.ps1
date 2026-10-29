# 真正干活的那一段（relay.ps1 用计划任务以糖糖的身份起，ssh 断了也接着跑）。2026-10-27。
#
#   1. 在仓库里给这个活开一个独立 worktree（D:\nox-jobs\<id>，分支 nox/<id>）—— 碰不到她正在改的工作区
#   2. 无人值守跑 Claude Code：
#        - 不加载任何 MCP（她用户配置里挂着记忆 / Notion / GitHub，后台活不许往她正式环境里写）
#        - 只能改文件 + 跑白名单里的命令；不许 push / ssh / scp / curl / 上网
#        - 最多 60 分钟
#   3. 写 result.json：给她看的总结、提交了什么、还有没提交的改动
# 上线不在这里做：停在分支，她亲口说「上线」才另起一个活（她 10-27 定）。

param([Parameter(Mandatory)][string]$Id)

$ErrorActionPreference = 'Stop'
# git 的输出按 UTF-8 读（不设的话按 GBK 读，提交信息全成乱码 —— 10-27 第一次实测）
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:LC_ALL = 'C.UTF-8'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$jd = Join-Path $dir "jobs\$Id"
$utf8 = New-Object Text.UTF8Encoding $false
$st = Get-Content "$jd\state.json" -Raw -Encoding UTF8 | ConvertFrom-Json
. "$dir\card.ps1"   # 上线卡的结构化字段（10-29）：$CARD_SPEC / Get-Card / Get-SummaryText
function Save-State { [IO.File]::WriteAllText("$jd\state.json", ($st | ConvertTo-Json -Compress -Depth 5), $utf8) }
function Set-St($k, $v) { $st | Add-Member -NotePropertyName $k -NotePropertyValue $v -Force }
function Finish($state, $result) {
    Set-St 'state' $state; Set-St 'finished' (Get-Date -Format s); Save-State
    [IO.File]::WriteAllText("$jd\result.json", ($result | ConvertTo-Json -Compress -Depth 5), $utf8)
}

#: 找 claude：她机器上 npm -g 装在 workbuddy 的 node 下（持久 PATH 指向的旧目录可能已经不在了）
function Find-Claude {
    $c = @(
        "$env:USERPROFILE\.workbuddy\binaries\node\workspace\node_modules\.bin\claude.cmd",
        "$env:APPDATA\npm\claude.cmd"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($c) { return $c }
    $g = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($g) { return $g.Source }
    throw '找不到 claude'
}

try {
    Set-St 'state' 'running'; Set-St 'started' (Get-Date -Format s); Set-St 'pid' $PID; Save-State
    $repo = $st.repo_path
    $wt = "D:\nox-jobs\$Id"
    $branch = "nox/$Id"
    $base = (git -C $repo rev-parse HEAD).Trim()
    git -C $repo worktree add -q -b $branch $wt $base 2>&1 | Out-Null
    if (-not (Test-Path $wt)) { throw "worktree 没开成：$wt" }
    Set-St 'worktree' $wt; Set-St 'branch' $branch; Set-St 'base' $base; Save-State

    $task = Get-Content "$jd\task.txt" -Raw -Encoding UTF8
    $prompt = @"
你是糖糖电脑上的 Claude Code。这个活是 Nox（她的另一个我，住在家里 N150 上）从她手机上的一句话转过来的，**无人值守**：没有人会回答你的问题，也没有人能给你批权限。

规矩：
- 你在独立的 worktree 里（$wt，分支 $branch），只在这里改。别去碰 D:\claude-code 主工作区。
- 先读 CLAUDE.md 和它要求必读的文件，按里面的验证纪律做（改完故意改坏一次，确认检查会红）。
- nox-core 的测试：用主仓的解释器 D:/claude-code/nox-core/.venv/Scripts/python.exe，在 worktree 的 nox-core 目录下跑。
- **不部署、不 push、不 ssh、不碰 N150**。上线要糖糖亲口说，那是下一步、另一个活。
- 没有任何 MCP 工具（记忆 / Notion / GitHub 都没有），别想着 breath / hold / grow。
- 做完把你的改动提交到当前分支（只提交你自己的改动，提交信息写清楚为什么）。
- 拿不准她要什么，就做最小、最稳的那一版，在总结里说清楚你的取舍和没做的部分。

$CARD_SPEC

她的原话 / Nox 转述的任务：
$task
"@
    [IO.File]::WriteAllText("$jd\prompt.txt", $prompt, $utf8)

    $claude = Find-Claude
    $allowed = @(
        'Read', 'Edit', 'Write', 'Glob', 'Grep', 'TodoWrite', 'Task',
        'Bash(git status:*)', 'Bash(git diff:*)', 'Bash(git log:*)', 'Bash(git show:*)',
        'Bash(git add:*)', 'Bash(git commit:*)', 'Bash(git restore:*)', 'Bash(git stash:*)', 'Bash(git rev-parse:*)',
        'Bash(ls:*)', 'Bash(cat:*)', 'Bash(head:*)', 'Bash(tail:*)', 'Bash(wc:*)', 'Bash(grep:*)', 'Bash(rg:*)',
        'Bash(mkdir:*)', 'Bash(cp:*)', 'Bash(mv:*)', 'Bash(sed:*)', 'Bash(diff:*)', 'Bash(sort:*)',
        'Bash(python:*)', 'Bash(D:/claude-code/nox-core/.venv/Scripts/python.exe:*)',
        'Bash(node:*)', 'Bash(npm test:*)', 'Bash(npm run:*)', 'Bash(npx vitest:*)', 'Bash(npx vite build:*)',
        'Bash(bash scripts/check-boundaries.sh:*)', 'Bash(python scripts/check-design.py:*)'
    ) -join ','
    $denied = @(
        'Bash(git push:*)', 'Bash(git worktree:*)', 'Bash(git checkout:*)', 'Bash(git switch:*)', 'Bash(git reset:*)',
        'Bash(ssh:*)', 'Bash(scp:*)', 'Bash(curl:*)', 'Bash(wget:*)', 'Bash(rm -rf:*)',
        'WebFetch', 'WebSearch'
    ) -join ','
    $mcp = "$jd\no-mcp.json"
    [IO.File]::WriteAllText($mcp, '{"mcpServers":{}}', $utf8)
    # 干净的环境：别从 ssh / 计划任务那头带进来什么 ANTHROPIC_*，以她 settings.json 为准
    Get-ChildItem env: | Where-Object { $_.Name -match '^(ANTHROPIC_|CLAUDE_?CODE|CLAUDECODE)' } |
        ForEach-Object { Remove-Item "env:$($_.Name)" }

    # 用哪个模型（她 10-27 定）：**先用 GLM**（她 settings.json 里配的智谱 glm-5.3-flash，她有订阅），
    # 节点稳定了再把 $USE_SUBSCRIPTION 改成 $true 换回订阅 Sonnet。下面那条路 10-27 已经铺好、只差稳定的节点：
    #   - 只在这里用 --settings 把她 settings 里指向智谱的 env 盖成空，她平时命令行的设置不动
    #   - 凭据：`claude setup-token` 的长期 token，她自己用 save-claude-token.ps1 存（`claude auth login` 在这儿用不上）
    #   - 订阅走 Anthropic，国内 IP 直连一律 403，命令行又不认 Windows 系统代理 → 显式指到她的 Clash（7897）
    $USE_SUBSCRIPTION = $false
    $modelArgs = ''
    if ($USE_SUBSCRIPTION) {
        $ovr = "$jd\use-subscription.json"
        $blank = @{}
        foreach ($k in 'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL',
                       'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL_NAME',
                       'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL_NAME',
                       'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME') { $blank[$k] = '' }
        [IO.File]::WriteAllText($ovr, (@{ env = $blank } | ConvertTo-Json -Compress), $utf8)
        $tok = Join-Path $env:USERPROFILE '.nox\claude-token'
        if (-not (Test-Path $tok)) { throw "没有订阅 token（$tok）：要糖糖跑一次 claude setup-token + save-claude-token.ps1" }
        $env:CLAUDE_CODE_OAUTH_TOKEN = (Get-Content $tok -Raw).Trim()   # 内容不打日志、不进 state / result
        $proxyPort = 7897
        $probe = New-Object Net.Sockets.TcpClient
        try { $probe.Connect('127.0.0.1', $proxyPort) } catch { }
        $up = $probe.Connected; $probe.Close()
        if (-not $up) { throw "电脑上的 Clash 没开（127.0.0.1:$proxyPort 连不上），订阅连不出去。多半是刚开机、她还没登录 Windows" }
        $env:HTTPS_PROXY = "http://127.0.0.1:$proxyPort"; $env:HTTP_PROXY = $env:HTTPS_PROXY
        $env:NO_PROXY = 'localhost,127.0.0.1'
        $modelArgs = "--model sonnet --settings `"$ovr`" "
    }

    function Invoke-Claude($promptFile, $name, $extra, $minutes) {
        $cargs = "/c `"`"$claude`" -p $modelArgs$extra--output-format json --permission-mode acceptEdits " +
            "--strict-mcp-config --mcp-config `"$mcp`" --allowedTools `"$allowed`" --disallowedTools `"$denied`" " +
            "< `"$promptFile`" > `"$jd\$name.json`" 2> `"$jd\$name.err`"`""
        $proc = Start-Process cmd.exe -ArgumentList $cargs -WorkingDirectory $wt -WindowStyle Hidden -PassThru
        Set-St 'claude_pid' $proc.Id; Save-State
        if (-not $proc.WaitForExit($minutes * 60 * 1000)) {
            taskkill.exe /T /F /PID $proc.Id 2>$null | Out-Null
            throw "超过 $minutes 分钟，掐掉了"
        }
        try { return (Get-Content "$jd\$name.json" -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
    }

    $out = Invoke-Claude "$jd\prompt.txt" 'claude' '--max-turns 120 ' 60
    $card = if ($out) { Get-Card $out.result } else { $null }
    if ($out -and -not $out.is_error -and -not $card -and $out.session_id) {
        # 最后那个 json 块没给 / 给坏了：接着它的会话追问一次，只要卡（10-29）
        [IO.File]::WriteAllText("$jd\card-prompt.txt", "你最后一条回复里少了上线卡的 json 块（或者格式不对）。现在只回复那个 json 代码块，别的什么都不要做。`n`n$CARD_SPEC", $utf8)
        $again = Invoke-Claude "$jd\card-prompt.txt" 'claude-card' "--resume $($out.session_id) --max-turns 3 " 5
        if ($again) { $card = Get-Card $again.result }
    }

    $commits = @(git -C $wt log --oneline "$base..HEAD" 2>$null)
    $stat = (git -C $wt diff --stat "$base..HEAD" 2>$null | Select-Object -Last 1)
    $dirty = @(git -C $wt status --porcelain 2>$null).Count
    # 改了哪些文件：N150 据此算「点了上线会发生什么」（要不要部署、会不会重启）。quotepath=false：中文名别被转义
    $files = @(git -C $wt -c core.quotepath=false diff --name-status --no-renames "$base..HEAD" 2>$null | ForEach-Object {
        $p = $_ -split "`t"; [ordered]@{ status = $p[0]; path = $p[1] } })
    $result = @{
        card = $card; files = $files
        summary = if ($out) { Get-SummaryText $out.result } else { (Get-Content "$jd\claude.err" -Raw -ErrorAction SilentlyContinue) }
        is_error = if ($out) { [bool]$out.is_error } else { $true }
        turns = if ($out) { $out.num_turns } else { $null }
        # 实际跑的是哪个模型（以回包为准，不以参数为准）
        models = if ($out -and $out.modelUsage) { @($out.modelUsage.PSObject.Properties.Name) } else { @() }
        commits = $commits; diffstat = "$stat"; uncommitted = $dirty
        branch = $branch; worktree = $wt
    }
    Finish ($(if ($result.is_error) { 'failed' } else { 'done' })) $result
}
catch {
    Finish 'failed' @{ summary = "没跑完：$($_.Exception.Message)"; is_error = $true }
}
finally {
    Unregister-ScheduledTask -TaskName "NoxJob-$Id" -Confirm:$false -ErrorAction SilentlyContinue
}
