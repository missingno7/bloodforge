# Source-tree CLI launcher. Override with $env:BLOODFORGE_PYTHON if necessary.
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonPath = $env:BLOODFORGE_PYTHON
if (-not $pythonPath) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $pythonPath = $bundledPython }
    else { $pythonPath = 'python' }
}
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectDir 'src'
    & $pythonPath -m bloodforge @args
    exit $LASTEXITCODE
} finally { $env:PYTHONPATH = $previousPythonPath }
