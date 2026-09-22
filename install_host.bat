@echo off
chcp 65001 >nul
title yt-dlp 插件一键启动服务 - 安装
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ============================================
echo   yt-dlp 插件「一键启动服务」安装
echo ============================================
echo.
set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3
if defined PY (
  "%PY%" "%~dp0install_host.py" %*
  goto :done
)
echo [提示] 未找到 Python，改用内置启动器直接拉起服务…
if exist "%~dp0yt-dlp-server\ytdlp_host.exe" (
  "%~dp0yt-dlp-server\ytdlp_host.exe" --start
) else (
  echo [错误] 既没有 Python 也没有 yt-dlp-server\ytdlp_host.exe
)
:done
echo.
pause
