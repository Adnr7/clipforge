[CmdletBinding()]
param(
    [ValidateSet('setup', 'build', 'test', 'run', 'all')]
    [string]$Task = 'setup'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'

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

function Get-RequiredCommand {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        if ($Name -eq 'py') {
            throw "Python 3.12 was not found. Install Python 3.12 from python.org, enable the Python Launcher, and reopen PowerShell."
        }
        if ($Name -eq 'npm') {
            throw "Node.js/npm was not found. Install Node.js 22.12.0, then reopen PowerShell."
        }
        throw "Required command '$Name' was not found. Install it and reopen PowerShell."
    }
    return $command.Path
}

function Test-Python312 {
    param([Parameter(Mandatory = $true)][string]$PythonLauncher)

    & $PythonLauncher '-3.12' '-c' 'import sys; assert sys.version_info[:2] == (3, 12), "ClipForge requires Python 3.12"'
    return $LASTEXITCODE -eq 0
}

function Ensure-Python312 {
    $py = Get-RequiredCommand 'py'
    if (Test-Python312 $py) {
        return $py
    }

    $winget = Get-Command 'winget' -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw 'Python 3.12 is not installed. Install it with `winget install --id Python.Python.3.12 --exact --scope user`, reopen PowerShell, and run this script again.'
    }

    Write-Host 'Python 3.12 is missing. Installing the official Python 3.12 package with winget...' -ForegroundColor Yellow
    & $winget.Source 'install' '--id' 'Python.Python.3.12' '--exact' '--source' 'winget' '--scope' 'user' '--accept-source-agreements' '--accept-package-agreements'
    if ($LASTEXITCODE -ne 0) {
        throw 'winget could not install Python 3.12. Install it manually with `winget install --id Python.Python.3.12 --exact --scope user`, reopen PowerShell, and run this script again.'
    }

    if (-not (Test-Python312 $py)) {
        throw 'Python 3.12 was installed, but the launcher cannot see it yet. Close and reopen PowerShell, run `py --list` to verify it, and then run this script again.'
    }
    return $py
}

function Ensure-PythonEnvironment {
    $py = Ensure-Python312

    Push-Location $Root
    try {
        if (-not (Test-Path -LiteralPath $VenvPython)) {
            Invoke-Checked $py @('-3.12', '-m', 'venv', '.venv')
        }
    }
    finally {
        Pop-Location
    }
}

function Install-Dependencies {
    Ensure-PythonEnvironment
    $npm = Get-RequiredCommand 'npm'

    Push-Location $Root
    try {
        Invoke-Checked $VenvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-r', 'requirements-dev.lock')
        Invoke-Checked $npm @('--prefix', 'frontend', 'ci')

        # Installs the pinned, checksum-verified native Windows media runtime
        # without administrator access or a machine-wide PATH change.
        Invoke-Checked $VenvPython @('scripts/setup_ffmpeg_windows.py')
    }
    finally {
        Pop-Location
    }
}

function Build-Frontend {
    $npm = Get-RequiredCommand 'npm'
    Push-Location $Root
    try {
        Invoke-Checked $npm @('--prefix', 'frontend', 'run', 'build')
    }
    finally {
        Pop-Location
    }
}

function Test-Backend {
    Ensure-PythonEnvironment
    Push-Location $Root
    try {
        Invoke-Checked $VenvPython @('-B', '-m', 'pytest', '-q', '--tb=short', 'tests')
    }
    finally {
        Pop-Location
    }
}

function Run-ClipForge {
    Ensure-PythonEnvironment
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'frontend\dist\index.html'))) {
        throw 'The frontend is not built. Run .\scripts\setup_windows.ps1 -Task build first.'
    }

    Push-Location $Root
    try {
        & $VenvPython 'main.py' '--web'
    }
    finally {
        Pop-Location
    }
}

switch ($Task) {
    'setup' { Install-Dependencies }
    'build' { Build-Frontend }
    'test' { Test-Backend }
    'run' { Run-ClipForge }
    'all' {
        Install-Dependencies
        Build-Frontend
        Test-Backend
        Run-ClipForge
    }
}
