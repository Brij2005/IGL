param(
    [switch]$SkipTests,
    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api/v1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

Write-Host "IGL Safety Intelligence local validation (read-only except pytest temporary files)"
Write-Host "Repository: $ProjectRoot"
git status --short --branch
git rev-parse HEAD

Write-Host "`nPython and dependency check"
python --version
python -c "import fastapi, cv2, sqlalchemy, ultralytics; print('FastAPI', fastapi.__version__); print('OpenCV', cv2.__version__); print('Ultralytics', ultralytics.__version__)"
if ($LASTEXITCODE -ne 0) { throw "One or more runtime dependencies are unavailable." }

Write-Host "`nAI weights search (.pt/.onnx/.engine)"
$Weights = Get-ChildItem -Path $ProjectRoot -Recurse -File -Include *.pt,*.onnx,*.engine -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\.git\\|\\.codex-tmp\\|\\node_modules\\' }
if ($Weights) { $Weights | Select-Object FullName,Length } else { Write-Host "MODEL_NOT_CONFIGURED: no model binary found in repository." }

Write-Host "`nBackend health (read-only; no credentials or provider sends)"
try {
    $Health = Invoke-RestMethod -Uri "$($ApiBaseUrl.TrimEnd('/'))/system/health" -TimeoutSec 5
    $Health | Select-Object application,overall_status,database,migrations,model_state,camera_state,access_control,validation_status | Format-List
} catch {
    Write-Host "BACKEND_NOT_REACHABLE: $($_.Exception.Message)"
}

Write-Host "`nWindows camera device and privacy facts"
if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    $CameraDevices = @(Get-PnpDevice -Class Camera -PresentOnly -ErrorAction SilentlyContinue)
    if ($CameraDevices.Count) { $CameraDevices | Select-Object Status,FriendlyName,InstanceId | Format-Table -AutoSize }
    else { Write-Host "NO_PRESENT_PNP_CAMERA_DEVICE" }
    $ConsentPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    if (Test-Path $ConsentPath) {
        $Consent = Get-ItemProperty $ConsentPath -ErrorAction SilentlyContinue
        Write-Host "Machine webcam consent registry value: $($Consent.Value)"
    } else { Write-Host "Machine webcam consent registry value: NOT_REPORTED" }
} else { Write-Host "Windows PnP camera inspection is unavailable on this host." }

Write-Host "`nPhysical webcam probe: OpenCV DirectShow, MSMF and automatic backend alternatives"
python scripts/test_webcam.py --index 0 --seconds 2
$WebcamExitCode = $LASTEXITCODE
if ($WebcamExitCode -ne 0) { Write-Warning "Webcam probe did not validate a real camera. See the exact JSON error above." }

if (-not $SkipTests) {
    $TempPath = Join-Path $ProjectRoot ".codex-tmp\pytest"
    New-Item -ItemType Directory -Force (Join-Path $ProjectRoot ".codex-tmp") | Out-Null
    New-Item -ItemType Directory -Force $TempPath | Out-Null
    Write-Host "`nComplete backend test suite"
    python -m pytest backend/tests -q --basetemp $TempPath -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Test suite reported failures." }
}

Write-Host "`nNotification configuration states are in API /api/v1/notifications/channels/status; test-send endpoints send real messages."
Write-Host "No email, WhatsApp message, browser camera permission, or production network probe is sent/requested by this script."
Write-Host "Validation complete. Hardware/provider/model results remain as individually reported above."
