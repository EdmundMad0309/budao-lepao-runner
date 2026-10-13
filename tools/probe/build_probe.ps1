# 编译设备端探针 LProbe/LAccel -> probe.jar / accel.jar
#
# 依赖:
#   - JDK (javac, Java 8+ 目标)
#   - r8.jar  (取 com.android.tools.r8.D8，用于生成 dex)
#     下载: https://maven.google.com/com/android/tools/r8/9.5.23/r8-9.5.23.jar
#
# 用法: .\build_probe.ps1 -R8Jar C:\path\to\r8.jar -Adb "C:\Program Files\NetEase\MuMu\nx_device\12.0\shell\adb.exe"

param(
    [Parameter(Mandatory = $true)][string]$R8Jar,
    [string]$Adb = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

New-Item -ItemType Directory -Force -Path classes, dex_probe, dex_accel | Out-Null

javac --release 8 -d classes src\LProbe.java src\LAccel.java

java -cp $R8Jar com.android.tools.r8.D8 --output dex_probe --min-api 31 --no-desugaring `
    classes\LProbe.class classes\LProbe`$1.class

java -cp $R8Jar com.android.tools.r8.D8 --output dex_accel --min-api 31 --no-desugaring `
    classes\LAccel.class classes\LAccel`$1.class classes\LAccel`$2.class

Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path probe.jar) { Remove-Item -LiteralPath probe.jar }
if (Test-Path accel.jar) { Remove-Item -LiteralPath accel.jar }
[IO.Compression.ZipFile]::CreateFromDirectory("$PWD\dex_probe", "$PWD\probe.jar")
[IO.Compression.ZipFile]::CreateFromDirectory("$PWD\dex_accel", "$PWD\accel.jar")

Write-Host "生成 probe.jar / accel.jar" -ForegroundColor Green

if ($Adb -and (Test-Path $Adb)) {
    & $Adb push probe.jar /data/local/tmp/probe.jar
    & $Adb push accel.jar /data/local/tmp/accel.jar
    Write-Host "已推送到设备 /data/local/tmp/" -ForegroundColor Green
}