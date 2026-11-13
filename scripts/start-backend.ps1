param([int]$Port = 8000, [switch]$NoFrontendWatch)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location -LiteralPath $projectRoot
$previousWatch = $env:STORY_FRONTEND_WATCH
try {
    $env:STORY_FRONTEND_WATCH = if ($NoFrontendWatch) { '0' } else { '1' }
    & $pythonPath -m backend.serve --port $Port
} finally {
    $env:STORY_FRONTEND_WATCH = $previousWatch
    Pop-Location
}
