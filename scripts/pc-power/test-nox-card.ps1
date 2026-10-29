# nox-card.ps1 的离线测试：powershell -NoProfile -File scripts\pc-power\test-nox-card.ps1（退出码 0 = 全过）
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. "$PSScriptRoot\nox-card.ps1"
$fail = 0
function Check($name, $cond) { if ($cond) { Write-Output "ok   $name" } else { Write-Output "FAIL $name"; $script:fail++ } }

$good = @'
糖糖，写好了。

```json
{"title": "开关糖糖电脑 · 说明文档", "changes": ["新增 **一页** 说明文档", "docs/README.md 加了一行索引"],
 "verified": ["文档里 19 个数字逐项和代码比对，全对"], "unverified": ["没真的开关过机"]}
```
'@
$c = Get-Card $good
Check '正常的卡能读出来' ($null -ne $c -and $c.title -eq '开关糖糖电脑 · 说明文档')
Check '去掉 markdown 星号' ($c.changes[0] -eq '新增 一页 说明文档')
Check '没实测的单独一栏' ($c.unverified.Count -eq 1 -and $c.verified.Count -eq 1)
Check '总结里去掉 json 块' ((Get-SummaryText $good) -eq '糖糖，写好了。')

$two = $good + "`n" + '```json' + "`n" + '{"title": "第二张", "changes": ["改了按钮"], "verified": ["跑了测试"], "unverified": []}' + "`n" + '```'
Check '多个块取最后一个' ((Get-Card $two).title -eq '第二张')

Check '没有 json 块 → 不给卡' ($null -eq (Get-Card '糖糖，写好了。'))
Check '验证一句没有 → 不给卡' ($null -eq (Get-Card ('```json' + "`n" + '{"title": "x", "changes": ["a"], "verified": [], "unverified": []}' + "`n" + '```')))
Check '改动一条没有 → 不给卡' ($null -eq (Get-Card ('```json' + "`n" + '{"title": "x", "changes": [], "verified": ["a"]}' + "`n" + '```')))
Check '没标题 → 不给卡' ($null -eq (Get-Card ('```json' + "`n" + '{"changes": ["a"], "verified": ["b"]}' + "`n" + '```')))
Check 'json 坏了 → 不给卡' ($null -eq (Get-Card ('```json' + "`n" + '{"title": "x", ' + "`n" + '```')))
exit $fail
