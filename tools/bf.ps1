# Source-tree CLI launcher. Override with $env:BLOODFORGE_PYTHON if necessary.
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonPath = $env:BLOODFORGE_PYTHON
if (-not $pythonPath) {
    $localPython = Join-Path $projectDir '.venv\Scripts\python.exe'
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $localPython) { $pythonPath = $localPython }
    elseif (Test-Path -LiteralPath $bundledPython) { $pythonPath = $bundledPython }
    else { $pythonPath = 'python' }
}
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectDir 'src'
    & $pythonPath -m bloodforge @args
    exit $LASTEXITCODE
} finally { $env:PYTHONPATH = $previousPythonPath }
