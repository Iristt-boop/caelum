# Caelum Gateway 开机自启。
#
# 起的是 D:\deepseek-harness\caelum-os\start-gateway.mjs ——
# 那是**远端 Nox 用这台电脑的唯一入口**（PC 主动外连 VPS 的 wss）。
#
# ## 🔴 它和 dsh 是两回事
#
# 计划任务里那三个 dsh-* 起的是 dsh 自己那个 agent 的 web 界面（:3080），
# 跟 Nox 够不够得到本地**毫无关系**。
# 2026-08-31 就是栽在这儿：dsh 三个任务好好跑着，
# 网关从来没起过、也没有任何自启守着，于是 Nox 一直说「电脑没开机」。
#
# ## 为什么用 npx tsx 而不是 node
#
# start-gateway.mjs 直接 import TS 源码（见它自己的注释：
# 官方的 build:lib 靠一份逐个列文件的清单，我们的新文件不在里面，
# 加进去会破坏「内核 0 脚印」）。所以必须走 tsx。

# ⚠️ **不要设 SilentlyContinue。**
# 第一版设了，结果 `Get-NetTCPConnection` 出错被吞掉、判断变成"没在跑"，
# 于是每跑一次任务就叠一个网关（实测一次起了 6 个 node，
# 日志里刷 EADDRINUSE + 「1 秒后重连」）。
# 这个脚本宁可报错退出，也不要静悄悄地做错事。

$Harness = 'D:\deepseek-harness'
$LogDir  = 'D:\claude-code\logs'
#: 🔴 两份日志，别合成一份。
#  $Log 是**子进程的 stdout**，`Start-Process -RedirectStandardOutput`
#  每次都会把它**截断重写** —— 第一版我往同一个文件里 Add-Content，
#  写进去的判断记录全被子进程冲掉了，排查时看到的是一片空白。
$Log    = Join-Path $LogDir 'caelum-gateway.log'      # 网关自己的输出
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
$Netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
$held = (& $Netstat -ano | Select-String ':39100\s' | Select-String 'LISTENING')

if ($held) {
  Add-Content -Path $Boot -Value ("[{0}] 已在跑（39100 占着），跳过" -f (Get-Date -Format s))
  exit 0
}

Add-Content -Path $Boot -Value ("[{0}] 39100 空着，启动 Caelum Gateway" -f (Get-Date -Format s))

# 后台起，不弹窗。stdout/stderr 都进同一份日志 ——
# 链路建没建立、密钥读没读到，全在里面
Start-Process -FilePath 'npx.cmd' `
  -ArgumentList 'tsx', 'caelum-os/start-gateway.mjs' `
  -WorkingDirectory $Harness `
  -WindowStyle Hidden `
  -RedirectStandardOutput $Log `
  -RedirectStandardError (Join-Path $LogDir 'caelum-gateway.err.log')
