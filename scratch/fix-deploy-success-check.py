#!/usr/bin/env python3
r"""deploy.ps1：成功判定从来没生效过 + 远端日志全是乱码（2026-09-13）。

两处根因，都在 PowerShell 5.1 的 native 进程边界上：

  ① `Console.OutputEncoding` 在这台机器上是 gb2312 —— ssh 子进程吐的是
     UTF-8 字节，PS 按 GBK 解，"部署完成" 到手变成 6 个乱码字。
     于是那句 `-match "部署完成"` **永远不成立**：每一次成功部署都打红字
     + exit 1。实测收到的码点是 38318,12583,35762,28729,23678,22426。
  ② `tar -tf C:\...\x.tar` —— GNU tar 把 `C:` 当成远程主机名
     （"Cannot connect to C: resolve failed"），条目数恒为 0，
     看起来像"什么都没打进去"。

⚠️ 保 BOM、保 CRLF：这文件没 BOM 会被 PS 5.1 按 GBK 解码、中文全崩
（文件头自己写着这条）。所以按字节改，不用文本工具。

锚点必须正好出现 1 次，否则中止不猜；改完 `git diff` 可审。
"""
import pathlib
import sys

P = pathlib.Path("scripts/deploy.ps1")
raw = P.read_bytes()
assert raw[:3] == b"\xef\xbb\xbf", "BOM 不见了，先别动"
# 整个文件是 CRLF（.gitattributes 规定的）。锚点按 LF 写，所以先归一化，
# 最后再原样还回去 —— 不这么做的话锚点一条都对不上（这次就撞了）。
s = raw.decode("utf-8-sig").replace("\r\n", "\n")
assert "\r" not in s, "混着裸 CR，先别动"

PATCHES: list[tuple[str, str]] = [
    # ── ① 让子进程的 UTF-8 输出能被正确解码 ──────────────────────
    (
        '''$ErrorActionPreference = "Stop"
$Key  = "C:\\Users\\14372\\.ssh\\id_ed25519"''',
        '''$ErrorActionPreference = "Stop"

# 🔴 **这台机器的 Console.OutputEncoding 是 gb2312。**
# ssh / git 这些 native 进程吐的是 UTF-8 字节，PS 5.1 会拿上面那个编码去解
# —— 于是远端日志里的中文全变乱码，「详见上面的日志」这句话就成了废话。
# （2026-09-13：正是它让第 [4] 步的成功判定一直失效，见那里的注释。）
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Key  = "C:\\Users\\14372\\.ssh\\id_ed25519"''',
    ),
    # ── ② 把远端退出码带回来 ────────────────────────────────────
    (
        '''    $out = $Script | ssh @SshOpts $Vps "bash -s" 2>&1
    return ($out | ForEach-Object { $_.ToString() })''',
        '''    $out = $Script | ssh @SshOpts $Vps "bash -s" 2>&1
    # 远端脚本的退出码（ssh 原样带回来）。**这才是"成功了没有"的判据**，
    # 见第 [4] 步那段注释 —— 别再回去 match 日志里的中文。
    $script:RemoteExit = $LASTEXITCODE
    return ($out | ForEach-Object { $_.ToString() })''',
    ),
    # ── ③ 条目数别再问 tar ──────────────────────────────────────
    (
        '''  $count = (tar -tf $tar | Measure-Object).Count''',
        '''  # ⚠️ 别用 `tar -tf $tar` 数 —— GNU tar 会把 `C:\\...` 当成**远程主机**
  #    ("Cannot connect to C: resolve failed")，条目数恒为 0，
  #    看起来像"什么都没打进去"。问 git 要，它本来就知道。
  $count = (git ls-tree -r --name-only $Commit $Sub | Measure-Object).Count''',
    ),
    # ── ④ 成功判定改成看退出码 ──────────────────────────────────
    (
        '''  if (-not (($out -join "`n") -match "部署完成")) {
    Write-Host "🔴 部署未成功（详见上面的日志）" -ForegroundColor Red
    exit 1
  }''',
        '''  # 🔴 **判据是远端的退出码，不是日志里的中文**（2026-09-13 修）。
  #
  # 原来这里写的是 `($out -join "`n") -match "部署完成"`，
  # 而它**从来没有成立过** —— 上面那条 OutputEncoding 的注释解释了原因。
  # 结果是每一次成功部署都打红字 + exit 1。
  #
  # 那比没有检查更坏：狼来了喊多了，真失败的那一次也没人信。
  # 而 `deploy-remote.sh` 每条失败路径都 exit 1、成功 exit 0，
  # ssh 会把它原样带回来 —— 这个信号一直都在，只是没人用。
  if ($script:RemoteExit -ne 0) {
    Write-Host "🔴 部署未成功（远端退出码 $script:RemoteExit，详见上面的日志）" -ForegroundColor Red
    exit 1
  }''',
    ),
]

for old, new in PATCHES:
    n = s.count(old)
    if n != 1:
        print(f"🔴 锚点出现 {n} 次，期望 1 次 —— 中止，不猜：\n{old.splitlines()[0][:70]}…")
        sys.exit(2)
    s = s.replace(old, new)

P.write_bytes(b"\xef\xbb\xbf" + s.replace("\n", "\r\n").encode("utf-8"))
out = P.read_bytes()
assert out[:3] == b"\xef\xbb\xbf", "BOM 丢了"
assert out.count(b"\n") == out.count(b"\r\n"), "混进了裸 LF"
print("✅ 改好了；BOM 在，CRLF 全")
