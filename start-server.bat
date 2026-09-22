@echo off
chcp 65001 >nul
title yt-dlp 下载服务
cd /d "%~dp0"
echo ============================================
echo   yt-dlp 视频下载服务  (Ctrl+C 停止)
echo   服务地址: http://127.0.0.1:8787
echo ============================================
echo.
if not exist "yt-dlp-server\ytdlp_host.exe" goto :pymode
echo [内置模式] 使用打包好的启动器运行（无需 Python 环境）
"yt-dlp-server\ytdlp_host.exe" --serve
goto :end
:pymode
echo [Python 模式] python yt-dlp-server\download_server.py
python "yt-dlp-server\download_server.py"
:end
echo.
pause
