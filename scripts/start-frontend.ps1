param([int]$Port = 7357, [string]$ApiBase = 'http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
try {
    flutter run -d web-server --web-hostname 127.0.0.1 --web-port $Port "--dart-define=API_BASE_URL=$ApiBase"
} finally { Pop-Location }
