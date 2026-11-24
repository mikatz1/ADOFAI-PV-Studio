param(
    [switch]$SkipShortcut
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$IconPath = Join-Path $Root "assets\app_icon.ico"
$AgentDll = Join-Path $Root "agent\build\AdofaiRenderAgent.dll"
$HarmonyDll = Join-Path $Root "agent\build\0Harmony.dll"

if (-not (Test-Path $IconPath)) {
    throw "App icon not found: $IconPath"
}
if (-not (Test-Path $AgentDll)) {
    throw "AdofaiRenderAgent.dll not found: $AgentDll. Run agent\build.ps1 first."
}
if (-not (Test-Path $HarmonyDll)) {
    throw "0Harmony.dll not found: $HarmonyDll"
}

Write-Host "[1/3] Building single-file standalone executable via PyInstaller..." -ForegroundColor Cyan

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "ADOFAI PV Renderer",
    "--icon", $IconPath,
    "--add-data", "assets;assets",
    "--add-data", "agent\build\AdofaiRenderAgent.dll;agent\build",
    "--add-data", "agent\build\0Harmony.dll;agent\build",
    "--hidden-import", "PySide6.QtMultimedia",
    "--exclude-module", "tkinter",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "PIL",
    "--exclude-module", "pandas",
    "--exclude-module", "torch",
    "--exclude-module", "IPython",
    "--exclude-module", "pytest",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtSql",
    "--exclude-module", "PySide6.QtTest",
    "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtDesigner",
    "render.py"
)

& python @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$ExePath = Join-Path $Root "dist\ADOFAI PV Renderer.exe"
if (-not (Test-Path $ExePath)) {
    throw "Expected output EXE was not created: $ExePath"
}

$sizeMB = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
Write-Host "[2/3] Built successfully: $ExePath ($sizeMB MB)" -ForegroundColor Green

if (-not $SkipShortcut) {
    Write-Host "[3/3] Creating Desktop shortcut..." -ForegroundColor Cyan
    $WshShell = New-Object -ComObject WScript.Shell
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "ADOFAI PV Renderer.lnk"
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $ExePath
    $Shortcut.WorkingDirectory = (Split-Path $ExePath -Parent)
    $Shortcut.IconLocation = "$ExePath,0"
    $Shortcut.Description = "ADOFAI Offline PV Renderer"
    $Shortcut.Save()
    Write-Host "      Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
} else {
    Write-Host "[3/3] Skipping Desktop shortcut (-SkipShortcut)." -ForegroundColor Yellow
}
