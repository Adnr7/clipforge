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

function Ensure-PythonEnvironment {
    $py = Get-RequiredCommand 'py'
    & $py '-3.12' '-c' 'import sys; assert sys.version_info[:2] == (3, 12), "ClipForge requires Python 3.12"'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.12 was not found. Install Python 3.12 and enable the Python Launcher.'
    }

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
