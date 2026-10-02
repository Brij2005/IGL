param([int]$Port = 8001)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendPath = Join-Path $ProjectRoot "frontend"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python 3 to run the static frontend server."
}
if (-not (Test-Path (Join-Path $FrontendPath "index.html"))) {
    throw "Frontend files were not found at $FrontendPath."
}
Set-Location $ProjectRoot
python -m http.server $Port --bind 127.0.0.1 --directory frontend
