param(
    [string]$FrontendUrl = "http://127.0.0.1:8001",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api/v1"
)

$ErrorActionPreference = "Stop"
$FrontendUrl = $FrontendUrl.TrimEnd('/')
$ApiBaseUrl = $ApiBaseUrl.TrimEnd('/')
$Headers = @{}
if ($env:IGL_API_TOKEN) { $Headers.Authorization = "Bearer $($env:IGL_API_TOKEN)" }

Write-Host "Frontend HTTP smoke check (read-only; does not create events or send notifications)."
$Page = Invoke-WebRequest -Uri $FrontendUrl -TimeoutSec 10
if ($Page.StatusCode -ne 200) { throw "Frontend returned HTTP $($Page.StatusCode)." }
$Html = $Page.Content
$RequiredViews = @('overview', 'cameras', 'workers', 'events', 'incidents', 'alarm', 'rules', 'notifications', 'evidence', 'analytics', 'system')
foreach ($View in $RequiredViews) {
    if ($Html -notmatch "data-view=`"$View`"") { throw "Frontend navigation is missing the '$View' view." }
}

$ScriptPath = [regex]::Match($Html, 'src="([^"]+app\.js[^"]*)"').Groups[1].Value
$StylePath = [regex]::Match($Html, 'href="([^"]+styles\.css[^"]*)"').Groups[1].Value
if (-not $ScriptPath -or -not $StylePath) { throw "Frontend script or stylesheet reference was not found." }
$BaseUri = [uri]($FrontendUrl + '/')
$ScriptUrl = [uri]::new($BaseUri, $ScriptPath).AbsoluteUri
$StyleUrl = [uri]::new($BaseUri, $StylePath).AbsoluteUri
$Script = (Invoke-WebRequest -Uri $ScriptUrl -TimeoutSec 10).Content
$Style = (Invoke-WebRequest -Uri $StyleUrl -TimeoutSec 10).Content
if ($Script -notmatch 'function renderWorkers' -or $Script -notmatch '/workers/tracks') { throw "Worker view/API wiring was not found in the served JavaScript." }
if ($Style -notmatch 'color-scheme:\s*dark') { throw "Industrial dark theme declaration was not found in served CSS." }

try {
    $Health = Invoke-RestMethod -Uri "$ApiBaseUrl/system/health" -Headers $Headers -TimeoutSec 5
    $Tracks = Invoke-RestMethod -Uri "$ApiBaseUrl/workers/tracks?limit=1" -Headers $Headers -TimeoutSec 5
    Write-Host "API health: $($Health.overall_status); workers endpoint: RESPONDED ($(@($Tracks).Count) returned records)."
} catch {
    throw "API/worker smoke request failed: $($_.Exception.Message). For authenticated deployments set IGL_API_TOKEN in this PowerShell process."
}

Write-Host "PASS: dashboard HTML, navigation, JS/CSS delivery, health endpoint, and worker-track API responded."
Write-Host "Browser layout, login interaction, console errors, and camera hardware require a real browser session and are not validated by this HTTP smoke check."
