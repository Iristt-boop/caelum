# 上线卡的结构化字段（10-29，她：「卡片内容改成结构化字段生成，别再让 agent 写一整段自由文本」）。
# runner.ps1 点源（. ）进来用；单独拎出来是为了能离线测。
#
# 电脑上的 Claude Code 最后一条回复 = 给她的几句总结 + 最后一个 ```json 块：
#   {"title": "...", "changes": ["..."], "verified": ["..."], "unverified": ["..."]}
# 缺块 / 字段不全 → 返回 $null（runner 会追问它一次；还不行就不给卡，N150 那头拒发上线卡）

$CARD_SPEC = @'
最后一条回复的格式（N150 会按这个给糖糖生成上线卡，格式不对她就收不到卡）：
1. 先写给糖糖的总结：中文 3-8 句，说做了什么、怎么验证的、哪里没把握。不要贴代码。
2. 最后单独放一个 ```json 代码块，四个字段，每条都是一句人话（不要 markdown 星号、不要反引号、不要提交号）：
   - "title"：这件活叫什么，20 字以内（例：「开关糖糖电脑 · 说明文档」）
   - "changes"：2-5 条，每条说改了什么（例：「新增一页说明文档，讲开关电脑怎么用」）
   - "verified"：你**实际跑过、看到结果**的验证，每条一句（例：「文档里 19 个数字逐项和代码比对，全对」）
   - "unverified"：没实测过、没把握、要她上线后陪着试一次的，每条一句；真的没有就写 []
'@

function Strip-Md([string]$s) {
    if ($null -eq $s) { return '' }
    ($s -replace '\*\*|__|`', '').Trim()
}

function Get-Card([string]$text) {
    if (-not $text) { return $null }
    $ms = [regex]::Matches($text, '```json\s*([\s\S]*?)```')
    if ($ms.Count -eq 0) { return $null }
    try { $o = $ms[$ms.Count - 1].Groups[1].Value | ConvertFrom-Json } catch { return $null }
    $list = { param($v) @(@($v) | Where-Object { $_ -is [string] -and $_.Trim() } | ForEach-Object { Strip-Md $_ }) }
    $card = [ordered]@{
        title      = Strip-Md "$($o.title)"
        changes    = & $list $o.changes
        verified   = & $list $o.verified
        unverified = & $list $o.unverified
    }
    if (-not $card.title -or $card.title.Length -gt 40) { return $null }
    if ($card.changes.Count -lt 1) { return $null }
    if (($card.verified.Count + $card.unverified.Count) -lt 1) { return $null }   # 怎么验的一句没有 → 不给卡
    return $card
}

#: 总结里去掉那个 json 块（给她看的只剩人话）
function Get-SummaryText([string]$text) {
    if (-not $text) { return '' }
    ([regex]::Replace($text, '```json\s*[\s\S]*?```', '')).Trim()
}
