' 静默启动 yt-dlp 下载服务（无窗口后台运行），供开机自启使用
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FileExists(base & "\ytdlp_host.exe") Then
    sh.Run """" & base & "\ytdlp_host.exe"" --start", 0, False
ElseIf fso.FileExists(base & "\native_host.py") Then
    sh.Run """pythonw"" """ & base & "\native_host.py"" --start", 0, False
Else
    sh.Run """pythonw"" """ & base & "\download_server.py""", 0, False
End If
