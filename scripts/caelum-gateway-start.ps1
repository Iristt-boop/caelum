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
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 已在跑（39100 占着），跳过" -f (Get-Date -Format s))
  exit 0
}

Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 39100 空着，启动 Caelum Gateway" -f (Get-Date -Format s))

# 🔴 **npx 也要绝对路径 —— 而且要现找，不能写死版本号。**
#
# 2026-09-03 网关一整个上午没起来，根因在这儿：WorkBuddy 把 node 换到了
# 一个新的版本目录（末尾多了 -2），而用户 PATH 里还指着旧的那个（已经没了）。
# 于是 `Start-Process 'npx.cmd'` 在任务上下文里**找不到命令**，
# 而 -RedirectStandardOutput 已经把两份日志截断成 0 字节 ——
# 表现是：boot 日志写着「启动」，日志空白，端口空着。
# **无声失败，看起来像网关自己崩了。**
#
# 所以在 versions 底下挑最新的那个 npx，哪天 node 再更新也不用改这里。
#
#: ⚠️ 下面这个路径用**正斜杠**，别改回反斜杠。写这一行时被工具链坑过：
#  反斜杠加字母在某些写文件的路径上会被当成转义（b 变退格、v 变垂直制表），
#  落到盘上是控制字符 —— 路径看起来还挺正常，报错却是「找不到 npx」，
#  而且那个控制字符会让 PowerShell 的解析器在**几十行之后**才报错。
#  PowerShell 认正斜杠，换掉就没有转义可讲。
$NodeDir = Join-Path $env:USERPROFILE '.workbuddy/binaries/node/versions'
$Npx = Get-ChildItem -Path $NodeDir -Filter 'npx.cmd' -Recurse -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $Npx) {
  #: 退回 PATH 里找一次 —— 万一她换了 node 的装法
  $Npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
}
if (-not $Npx) {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 找不到 npx，没启动（找过 {1} 和 PATH）" -f (Get-Date -Format s), $NodeDir)
  exit 1
}
Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 用 {1}" -f (Get-Date -Format s), $Npx)

# 🔴 **光有 npx 的绝对路径还不够 —— npx.cmd 自己会去调 `node`。**
#
# 2026-09-03 第二层坑：npx 找到了、也起来了，然后 err.log 里躺着一句
# 「'node' 不是内部或外部命令」，而 boot 日志显示一切正常。
# 因为 PATH 里那条 node 记录指向的是**旧的版本目录**（已经不存在了）。
#
# 把 npx 所在的目录塞进 PATH 头部，子进程继承这份环境就找得到 node 了。
# ⚠️ 只改这个脚本进程自己的 $env:PATH，**不动系统/用户的 PATH 设置** ——
# 那是她机器的全局配置，不该由一个自启脚本去改。
$env:PATH = (Split-Path $Npx) + ';' + $env:PATH

# 后台起，不弹窗。stdout/stderr 都进同一份日志 ——
# 链路建没建立、密钥读没读到，全在里面
#
# ⚠️ **包在 try/catch 里**：Start-Process 抛异常时，异常走的是这个脚本的
# stderr，而计划任务没人收 —— 上面那次无声失败就是这么来的。
# 启动失败必须在 boot 日志里留下痕迹。
try {
  Start-Process -FilePath $Npx `
    -ArgumentList 'tsx', 'caelum-os/start-gateway.mjs' `
    -WorkingDirectory $Harness `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError (Join-Path $LogDir 'caelum-gateway.err.log')
} catch {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 启动失败：{1}" -f (Get-Date -Format s), $_.Exception.Message)
  exit 1
}
