[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = if ($PythonPath) {
    $PythonPath
} else {
    Join-Path $Root '.venv\Scripts\python.exe'
}
$Spec = Join-Path $Root 'packaging\ClipForge.spec'
$FrontendIndex = Join-Path $Root 'frontend\dist\index.html'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @()
    )

    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $File $($Arguments -join ' ')"
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw 'The project virtual environment is missing. Run .\scripts\setup_windows.ps1 -Task setup first.'
}
if (-not (Test-Path -LiteralPath $Spec)) {
    throw "PyInstaller spec file not found: $Spec"
}

$pythonVersion = (& $VenvPython '-c' 'import sys; print(str(sys.version_info[0]) + chr(46) + str(sys.version_info[1]))').Trim()
if ($pythonVersion -ne '3.12') {
    throw "The project virtual environment uses Python $pythonVersion. Recreate .venv with Python 3.12, then run setup again."
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($null -eq $npm) {
    throw 'Node.js/npm was not found. Install Node.js 22.12.0 and reopen PowerShell.'
}

Push-Location $Root
try {
    if (-not $SkipFrontendBuild) {
        Invoke-Checked $npm.Source @('--prefix', 'frontend', 'run', 'build')
    }
    if (-not (Test-Path -LiteralPath $FrontendIndex)) {
        throw 'The frontend build is missing. Run the build without -SkipFrontendBuild.'
    }

    Invoke-Checked $VenvPython @('scripts\create_windows_icon.py')

    Invoke-Checked $VenvPython @(
        '-m', 'PyInstaller',
        '--clean',
        '--noconfirm',
        '--distpath', 'dist',
        '--workpath', 'build\windows',
        $Spec
    )
}
finally {
    Pop-Location
}

$Artifact = Join-Path $Root 'dist\ClipForge.exe'
if (-not (Test-Path -LiteralPath $Artifact)) {
    throw "PyInstaller completed without producing $Artifact"
}

$sizeMiB = [math]::Round((Get-Item -LiteralPath $Artifact).Length / 1MB, 1)
Write-Host "Windows executable created: $Artifact ($sizeMiB MiB)" -ForegroundColor Green
