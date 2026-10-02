param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python compatible with backend/requirements.txt."
}
python -c "import fastapi, uvicorn, sqlalchemy" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Backend dependencies are missing. Run: python -m pip install -r backend/requirements.txt"
}

if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    Write-Warning "No .env found; development defaults apply (anonymous access enabled, localhost only)."
}

python -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed; API was not started." }

$Arguments = @("-m", "uvicorn", "backend.app.main:app", "--host", $HostAddress, "--port", "$Port")
if ($Reload) { $Arguments += "--reload" }
& python @Arguments
exit $LASTEXITCODE
