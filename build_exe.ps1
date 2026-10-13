# 打包单文件 EXE
#   .\build_exe.ps1
# 产物: dist\LePaoRunner.exe （脚本会同时复制一份中文名）

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装 PyInstaller..." -ForegroundColor Yellow
    python -m pip install pyinstaller
}

python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name LePaoRunner `
    --paths src `
    --distpath dist `
    --workpath build `
    --specpath build `
    gui.py

Copy-Item "dist\LePaoRunner.exe" "dist\步道乐跑助手.exe" -Force
Write-Host "打包完成: dist\步道乐跑助手.exe" -ForegroundColor Green