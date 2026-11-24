param(
    [string]$AdofaiDir = "C:\Program Files (x86)\Steam\steamapps\common\A Dance of Fire and Ice"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $scriptDir "src"
$buildDir = Join-Path $scriptDir "build"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$managed = Join-Path $AdofaiDir "A Dance of Fire and Ice_Data\Managed"
$ummDir = Join-Path $managed "UnityModManager"

if (-not (Test-Path $managed)) {
    throw "ADOFAI Managed directory not found at: $managed"
}

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    throw "C# compiler not found at: $csc"
}

$harmonySrc = Join-Path $ummDir "0Harmony.dll"
if (-not (Test-Path $harmonySrc)) {
    throw "0Harmony.dll not found at: $harmonySrc"
}
Copy-Item $harmonySrc (Join-Path $buildDir "0Harmony.dll") -Force

$refs = @(
    (Join-Path $managed "mscorlib.dll"),
    (Join-Path $managed "System.dll"),
    (Join-Path $managed "System.Core.dll"),
    (Join-Path $managed "netstandard.dll"),
    (Join-Path $managed "Assembly-CSharp.dll"),
    (Join-Path $managed "Assembly-CSharp-firstpass.dll"),
    (Join-Path $managed "UnityEngine.dll"),
    (Join-Path $managed "UnityEngine.CoreModule.dll"),
    (Join-Path $managed "UnityEngine.AudioModule.dll"),
    (Join-Path $managed "UnityEngine.UI.dll"),
    (Join-Path $managed "UnityEngine.UIModule.dll"),
    (Join-Path $managed "UnityEngine.VideoModule.dll"),
    (Join-Path $managed "UnityEngine.ParticleSystemModule.dll"),
    (Join-Path $managed "UnityEngine.SpriteMaskModule.dll"),
    (Join-Path $managed "UnityEngine.JSONSerializeModule.dll"),
    (Join-Path $managed "UnityEngine.InputLegacyModule.dll"),
    (Join-Path $managed "DOTween.dll"),
    (Join-Path $managed "RDTools.dll"),
    (Join-Path $buildDir "0Harmony.dll")
)

$refArgs = $refs | ForEach-Object { "/r:$_" }
$sources = Get-ChildItem $srcDir -Filter "*.cs" | ForEach-Object { $_.FullName }
$outDll = Join-Path $buildDir "AdofaiRenderAgent.dll"

Write-Host "Compiling AdofaiRenderAgent.dll..."
& $csc /nologo /nostdlib+ /noconfig /target:library /optimize+ /out:$outDll $refArgs $sources

if ($LASTEXITCODE -ne 0) {
    throw "csc.exe compilation failed with exit code $LASTEXITCODE"
}

Write-Host "Build succeeded: $outDll"
