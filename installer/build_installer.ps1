param(
    [switch]$RebuildExe
)

$ErrorActionPreference = "Stop"
$InstDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $InstDir
Set-Location $Root

if ($RebuildExe -or -not (Test-Path "dist\ADOFAI PV Renderer.exe")) {
    Write-Host "[1/2] Building standalone EXE first..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "build_exe.ps1") -SkipShortcut
    if ($LASTEXITCODE -ne 0) { throw "EXE build failed." }
}

$isccCands = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $null
foreach ($c in $isccCands) {
    if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) was not found. Please install Inno Setup 6 first."
}

Write-Host "[2/2] Compiling Inno Setup 6 installer via $iscc..." -ForegroundColor Cyan
$issPath = Join-Path $InstDir "setup.iss"
& $iscc $issPath
if ($LASTEXITCODE -ne 0) {
    throw "ISCC compilation failed with exit code $LASTEXITCODE"
}

$outExe = Join-Path $Root "dist\installer\ADOFAI_PV_Renderer_Setup_2.0.0.exe"
if (-not (Test-Path $outExe)) {
    throw "Expected installer was not created: $outExe"
}

$mb = [math]::Round((Get-Item $outExe).Length / 1MB, 1)
Write-Host "Installer built successfully: $outExe ($mb MB)" -ForegroundColor Green
