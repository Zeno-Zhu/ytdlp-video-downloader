# 重新打包 Native Messaging 启动器 ytdlp_host.exe（含 download_server 模块）
# 用法：双击运行，或在 PowerShell 中执行  .\build_host.ps1
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host '== 1/3 备份旧启动器 =='
if (Test-Path .\ytdlp_host.exe) {
    Copy-Item .\ytdlp_host.exe .\ytdlp_host.exe.bak -Force
    Write-Host '   已备份为 ytdlp_host.exe.bak'
}

Write-Host '== 2/3 PyInstaller 打包（单文件 / 无窗口 / 内置服务代码）=='
python -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx `
    --name ytdlp_host --paths . --hidden-import download_server `
    --distpath . --workpath .\build\work --specpath .\build native_host.py
if (-not (Test-Path .\ytdlp_host.exe)) { Write-Host '!! 打包失败'; exit 1 }

Write-Host '== 3/3 自检（跑一次 --start）=='
$size = [math]::Round((Get-Item .\ytdlp_host.exe).Length / 1MB, 1)
Write-Host "   新启动器大小: $size MB"
Remove-Item .\build -Recurse -Force -ErrorAction SilentlyContinue
Write-Host '完成。可运行 python .\test_launcher.py 做协议自检。'
