$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { python -m venv .venv }
    & '.venv/Scripts/python.exe' -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    Push-Location -LiteralPath 'frontend'
    try {
        flutter pub get
        if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency installation failed.' }
        dart run build_runner build
        if ($LASTEXITCODE -ne 0) { throw 'Dart serializer generation failed.' }
    } finally { Pop-Location }
} finally { Pop-Location }
