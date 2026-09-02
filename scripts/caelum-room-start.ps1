# 我们的小家（caelum-room）开机自启。
#
# 起的是 D:\claude-code\caelum-room 里的房间服务：
# 静态 Phaser 前端 + 状态服务 + MCP，跑在 127.0.0.1:8877。
# Caelum OS 的 World 页用 iframe 指着它 —— **它没起，那一页就是「够不到房间」**。
#
# ## 为什么需要自启
#
# 房间是本机服务，不在 VPS 上。糖糖打开 Caelum OS 的时候它必须已经在跑，
# 不然她看到的是一页启动命令 —— 那不叫「我们的家」。
# 同 Caelum Gateway 那次的教训（2026-08-31）：**没人守着的进程就是没在跑**。
#
# ## ⚠️ 这个文件必须存成 UTF-8 **带 BOM**
#
# Windows PowerShell 5.1 在没有 BOM 时按 GBK 读，中文注释会被解成乱码，
# 而**乱码会把后面的赋值整段吞掉** —— 表现是变量全 null、脚本半死不活。
# 2026-08-31 在 caelum-gateway-start.ps1 上栽过一次，别再栽第二次。
# 改完存盘后确认头三个字节是 EF BB BF。

# ⚠️ **不要设 SilentlyContinue。** 宁可报错退出，也不要静悄悄地做错事
# （网关那次：判断出错被吞掉 → 判成「没在跑」→ 每跑一次任务叠一个实例）。

$RoomDir = 'D:\claude-code\caelum-room'
$Python  = Join-Path $RoomDir '.venv\Scripts\python.exe'
$LogDir  = 'D:\claude-code\logs'

#: 🔴 两份日志，别合成一份。
#  $Log 是**子进程的 stdout**，`Start-Process -RedirectStandardOutput`
#  每次都会把它截断重写；判断记录写进同一个文件会被子进程冲掉，
#  排查时只看到一片空白。
#: ⚠️ 判断记录一律 `-Encoding UTF8` 写。PowerShell 5.1 的 Add-Content 默认
#  按系统 ANSI（这台机器是 GBK）落盘，中文在 bash/Python 那边读出来是乱码 ——
#  而排查的时候恰恰是从那边读。日志读不出来等于没有日志（网关那次的教训）。
$Log  = Join-Path $LogDir 'caelum-room.log'       # 房间服务自己的输出
$Boot = Join-Path $LogDir 'caelum-room-boot.log'  # 这个脚本的判断记录

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# venv 不在就别硬起 —— 硬起会得到一个「python 不存在」的空日志，
# 看起来和「起了但崩了」一模一样。如实记一笔再退出。
if (-not (Test-Path $Python)) {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 找不到 venv：{1}，没启动" -f (Get-Date -Format s), $Python)
  exit 1
}

# 🔴 **用端口判断，不要匹配命令行。**
# 命令行匹配容易**匹配到执行这条检查的进程自己**（网关那次被自己骗了一轮）。
# 房间服务亲自绑 127.0.0.1:8877，一个实例一个端口，端口在 = 它在。
#
#: ⚠️ **写绝对路径。** 任务上下文的 PATH 是精简过的，直接写 `netstat`
#  会命令找不到 → 查不到占用 → 每次登录都再叠一个实例。
#  同样一句在交互 shell 里是好的，在任务里静悄悄地什么都查不到。
$Netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
$held = (& $Netstat -ano | Select-String ':8877\s' | Select-String 'LISTENING')

if ($held) {
  Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 已在跑（8877 占着），跳过" -f (Get-Date -Format s))
  exit 0
}

Add-Content -Encoding UTF8 -Path $Boot -Value ("[{0}] 8877 空着，启动房间服务" -f (Get-Date -Format s))

# 后台起，不弹窗。⚠️ 必须给 -WorkingDirectory：脚本里用的是相对路径
# （web/、room_service/data/），从别的目录起会找不到素材和状态库。
Start-Process -FilePath $Python `
  -ArgumentList 'tools\run_room_shared_dev.py', '--port', '8877' `
  -WorkingDirectory $RoomDir `
  -WindowStyle Hidden `
  -RedirectStandardOutput $Log `
  -RedirectStandardError (Join-Path $LogDir 'caelum-room.err.log')
