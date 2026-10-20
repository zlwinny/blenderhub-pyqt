# BlenderHub 绿色版打包脚本
# 用法：
#   powershell -File build.ps1              # 目录版（推荐，启动快）
#   powershell -File build.ps1 -OneFile    # 单文件版（便于分发，启动稍慢）
param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($dir in @("build", "dist")) {
    if (Test-Path $dir) {
        Remove-Item $dir -Recurse -Force
    }
}

if ($OneFile) {
    $env:BLENDERHUB_ONEFILE = "1"
    $mode = "单文件版"
} else {
    Remove-Item Env:\BLENDERHUB_ONEFILE -ErrorAction SilentlyContinue
    $mode = "目录版（绿色便携）"
}

Write-Host "==> 开始打包 BlenderHub（$mode）"
python -m PyInstaller BlenderHub.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    Write-Host "打包失败，请检查上方日志" -ForegroundColor Red
    exit 1
}

$exe = Join-Path $PSScriptRoot "dist\BlenderHub\BlenderHub.exe"
if ($OneFile) {
    $exe = Join-Path $PSScriptRoot "dist\BlenderHub.exe"
}
$size = [math]::Round((Get-ChildItem (Split-Path $exe) -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "==> 打包完成：$exe （约 $size MB）"
