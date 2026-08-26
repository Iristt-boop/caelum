' DSH 隐藏启动器（供计划任务/登录自启调用，无窗口）
' 调 dsh-boot.cjs 后台启动 DSH，立即返回
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\claude-code"
WshShell.Run """C:\Users\14372\.workbuddy\binaries\node\versions\22.22.2\node.exe"" D:\claude-code\scripts\dsh-boot.cjs 3080", 0, False
