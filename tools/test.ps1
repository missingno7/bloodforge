$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonPath = $env:BLOODFORGE_PYTHON
if (-not $pythonPath) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $pythonPath = $bundledPython }
    else { $pythonPath = 'python' }
}
$oldPath = $env:PYTHONPATH
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
try {
    $env:PYTHONPATH = Join-Path $projectDir 'src'
    $env:TEMP = Join-Path $projectDir 'data\test-tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    & $pythonPath -m unittest discover -s (Join-Path $projectDir 'tests') -v
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $oldPath
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}
