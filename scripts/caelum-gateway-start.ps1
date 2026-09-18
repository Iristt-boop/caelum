# Caelum Gateway 开机自启 + 兜底看门狗。
#
# 起的是 D:/deepseek-harness/caelum-os/start-gateway.mjs ——
# 那是**远端 Nox 用这台电脑的唯一入口**（PC 主动外连 VPS 的 wss）。
#
# ## 🔴 它和 dsh 是两回事
#
# 计划任务里那三个 dsh-* 起的是 dsh 自己那个 agent 的 web 界面（:3080），
# 跟 Nox 够不够得到本地**毫无关系**。
# 2026-08-31 就是栽在这儿：dsh 三个任务好好跑着，
# 网关从来没起过、也没有任何自启守着，于是 Nox 一直说「电脑没开机」。
#
# ## 🔴 绕开 npx，直接 node + tsx 入口（2026-09-16）
#
# npx.cmd 走 WorkBuddy 打包的目录结构，内部再找 npm 时路径**双重拼接**
# （.../npm/bin/node_modules/npm/bin/npx-cli.js 不存在）→ npx 起不来
# → gateway 根本没启动，而任务显示"成功"（脚本本身跑完了）。
# caelum-gateway.err.log 里留了完整证据。
# node.exe 和 node_modules/tsx/dist/cli.mjs 都是稳定存在的，
# 直接组合，中间不经过任何会自己找路径的垫片。
#
# ## 为什么用 tsx 而不是构建产物
#
# start-gateway.mjs 直接 import TS 源码（官方的 build:lib 靠一份
# 逐个列文件的清单，我们的新文件不在里面，加进去会破坏「内核 0 脚印」）。
# node 22 的类型擦除跑不动 session-projection 的语法，必须完整转译 → tsx。
#
# ## ⚠️ 这个文件必须存成 UTF-8 **带 BOM**
#
# Windows PowerShell 5.1 在没有 BOM 时按 GBK 读，中文注释会被解成乱码，
# 而乱码会把后面的赋值整段吞掉 —— 表现是变量全 null、脚本半死不活。

# ⚠️ **不要设 SilentlyContinue。**
# 第一版设了，结果 `Get-NetTCPConnection` 出错被吞掉、判断变成"没在跑"，
# 于是每跑一次任务就叠一个网关（实测一次起了 6 个 node，
# 日志里刷 EADDRINUSE + 「1 秒后重连」）。
# 这个脚本宁可报错退出，也不要静悄悄地做错事。

$Harness = 'D:/deepseek-harness'
$LogDir  = 'D:/claude-code/logs'
#: 🔴 两份日志，别合成一份。
#  $Log 是**子进程的 stdout**，`Start-Process -RedirectStandardOutput`
#  每次都会把它**截断重写** —— 第一版我往同一个文件里 Add-Content，
#  写进去的判断记录全被子进程冲掉了，排查时看到的是一片空白。
$Log    = Join-Path $LogDir 'caelum-gateway.log'      # 网关自己的输出
$ErrLog = Join-Path $LogDir 'caelum-gateway.err.log'  # 子进程的 stderr（崩溃原因在这）
$Boot   = Join-Path $LogDir 'caelum-gateway-boot.log' # 这个脚本的判断记录

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ⚠️ 已经在跑就退出。没有这一步，每次登录/唤醒都会再叠一个网关，
#    两个实例抢同一条链路 —— VPS 只认一个设备，于是它们互相踢，
#    日志里刷「1 秒后重连」。2026-08-31 实测就是这样（一次起了 6 个 node）。
#
# 🔴 **用端口判断，不要匹配命令行。**
# `npx tsx` 会派生一串 node（npx-cli → tsx → 真正的脚本），
# 命令行匹配既容易漏、又容易**匹配到执行这条检查的进程自己**
# —— 我就是这么被自己骗了一轮的。
# 而 UI 审批通道是网关亲自绑的 127.0.0.1:39100，一个实例一个，
# 端口在 = 网关在，没有第二种解释。
#: 用 netstat 而不是 Get-NetTCPConnection —— 后者要 NetTCPIP 模块，
#  计划任务的上下文里不一定加载得上，一出错就成了「没在跑」。
#
#: ⚠️ **写绝对路径。** 任务上下文的 PATH 是精简过的，
#  直接写 `netstat` 会命令找不到 → $held 为空 → 每次都重复启动。
#  2026-08-31 实测就栽在这儿：同样一句在交互 shell 里好好的，
#  在任务里静悄悄地什么都没查到。
$Netstat = Join-Path $env:SystemRoot 'System32/netstat.exe'
$held = (& $Netstat -ano | Select-String ':39100\s' | Select-String 'LISTENING')

if ($held) {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 已在跑（39100 占着），跳过" -f (Get-Date -Format s))
  exit 0
}

Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 39100 空着，启动 Caelum Gateway" -f (Get-Date -Format s))

# ---- 找 node.exe：优先 PATH，退 WorkBuddy 目录里最新的那个 ----
$Node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if (-not $Node) {
  $Node = Get-ChildItem (Join-Path $env:USERPROFILE '.workbuddy/binaries/node/versions/*/node.exe') |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Node) { Add-Content -Encoding UTF8 -Path $Boot -Value '找不到 node.exe'; exit 1 }

# ---- tsx 入口：node_modules/tsx/dist/cli.mjs（完整转译，node 裸跑不动它）----
$Tsx = Join-Path $Harness 'node_modules/tsx/dist/cli.mjs'
if (-not (Test-Path $Tsx)) { Add-Content -Encoding UTF8 -Path $Boot -Value "tsx 入口不存在：$Tsx"; exit 1 }
Add-Content -Encoding UTF8 -Path $Boot -Value ("用 {0} + tsx 直跑" -f $Node)

# ⚠️ **包在 try/catch 里**：Start-Process 抛异常时，异常走的是这个脚本的
# stderr，而计划任务没人收 —— 上面那次无声失败就是这么来的。
# 启动失败必须在 boot 日志里留下痕迹。
try {
  Start-Process -FilePath $Node `
    -ArgumentList ('"{0}"' -f $Tsx), 'caelum-os/start-gateway.mjs' `
    -WorkingDirectory $Harness `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError $ErrLog
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 启动完成（output: {1}）" -f (Get-Date -Format s), $Log)
} catch {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 启动失败：{1}" -f (Get-Date -Format s), $_.Exception.Message)
  exit 1
}
