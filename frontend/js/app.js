"use strict";

const API_STORAGE_KEY = "iglSafetyApiBase";
const AUTH_TOKEN_KEY = "iglSafetyAccessToken";
const DEFAULT_API = "http://127.0.0.1:8000/api/v1";
const ALARM_EVENT_TYPES = new Set(["FIRE", "SMOKE", "RESTRICTED_ZONE_INTRUSION"]);
const labels = {
  overview: ["OPERATIONS / CURRENT STATE", "Overview"],
  cameras: ["MONITOR / INPUTS", "Cameras"],
  events: ["REVIEW / WORKFLOW", "Events"],
  evidence: ["REVIEW / EVENT MATERIAL", "Evidence"],
  configuration: ["OPERATOR-SUPPLIED / NO DEFAULTS", "Configuration"],
  analytics: ["RECORDS / SUMMARY", "Analytics"],
  system: ["SUBSYSTEMS / STATUS", "System health"],
  audit: ["SECURITY / ACTIVITY", "Audit log"],
  unavailable: ["CAPABILITY / NOT IMPLEMENTED", "Unavailable modules"],
};

const storedApiBase = sessionStorage.getItem(API_STORAGE_KEY);
const sanitizedApiBase = storedApiBase && !/localhost:8011|127\.0\.0\.1:8011/.test(storedApiBase) ? storedApiBase : DEFAULT_API;
if (storedApiBase !== sanitizedApiBase) {
  sessionStorage.setItem(API_STORAGE_KEY, sanitizedApiBase);
}
let apiBase = sanitizedApiBase;
let authToken = sessionStorage.getItem(AUTH_TOKEN_KEY);
let eventsCache = [];
let currentView = "overview";
let activeAlarmEvents = [];
let alarmPollingTimer = null;
let alarmSoundTimer = null;
let alarmAudioContext = null;
let alarmSoundEnabled = false;
let alarmRequestInFlight = false;
let webcamDiscovery = null;
let webcamCamera = null;
let webcamSourceStatus = null;
let webcamSystemHealth = null;
let webcamPollingTimer = null;
let webcamBusy = false;
let webcamCameraError = null;

const appShell = document.querySelector("#app-shell");
const loginShell = document.querySelector("#login-shell");
const content = document.querySelector("#view-content");
const globalMessage = document.querySelector("#global-message");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function showMessage(message, visible = true) {
  globalMessage.textContent = message;
  globalMessage.hidden = !visible;
}

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch { /* Keep the HTTP status only. */ }
    if (response.status === 401 && authToken) {
      authToken = null;
      sessionStorage.removeItem(AUTH_TOKEN_KEY);
      showLogin("Your session expired or was revoked. Sign in again.");
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function unavailableMarkup() {
  return `<section class="section"><div class="section-head"><h2>Current capability state</h2><span>REAL SYSTEM STATE</span></div><div class="section-body unavailable-grid">
    <div class="unavailable-item"><strong>AI model</strong>MODEL_NOT_CONFIGURED</div>
    <div class="unavailable-item"><strong>Detection pipeline</strong>BLOCKED_BY_MISSING_MODEL</div>
    <div class="unavailable-item"><strong>Alarm validation</strong>NOT_VALIDATED</div>
    <div class="unavailable-item"><strong>Live AI event generation</strong>WAITING_FOR_AUTHORIZED_MODEL</div>
    <div class="unavailable-item"><strong>External notifications</strong>NOT_CONFIGURED</div>
    <div class="unavailable-item"><strong>Camera preview</strong>LIVE_ONLY_WHEN_CAMERA_IS_AVAILABLE</div>
    <div class="unavailable-item"><strong>IGL validation</strong>NOT_VALIDATED</div>
    <div class="unavailable-item"><strong>Operational readiness</strong>NOT_PRODUCTION_READY</div>
  </div></section>`;
}

function metric(label, value, note = "From backend records") {
  return `<article class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-note">${escapeHtml(note)}</div></article>`;
}

function stateRows(items) {
  return `<div class="state-list">${items.map(([name, value]) => `<div class="state-row"><span>${escapeHtml(name)}</span><span class="state-value">${escapeHtml(value ?? "NOT_AVAILABLE")}</span></div>`).join("")}</div>`;
}

function statusBanner(health, summary) {
  const status = health?.overall_status || "NOT_AVAILABLE";
  const model = health?.model_state || "MODEL_NOT_CONFIGURED";
  const validation = health?.validation_status || "NOT_VALIDATED";
  const message = summary?.data_status === "NO_DATA" ? "No operational records are available yet." : "Values shown are current backend records only.";
  const dot = status === "OPERATIONAL" ? "dot-green" : status === "DEGRADED" ? "dot-amber" : "dot-muted";
  const reasons = Array.isArray(health?.degraded_reasons) && health.degraded_reasons.length
    ? ` Unavailable: ${health.degraded_reasons.join(", ")}.`
    : "";
  return `<div class="status-banner"><div><strong><span class="status-dot ${dot}"></span> ${escapeHtml(status)} · MODEL ${escapeHtml(model)}</strong><span>${escapeHtml(message + reasons)}</span></div><span class="status-pill">VALIDATION ${escapeHtml(validation)}</span></div>`;
}

async function renderOverview() {
  const [summary, health, cameras, events, incidents, notifications, channels] = await Promise.all([
    request("/analytics/summary"),
    request("/system/health"),
    request("/cameras?active_only=true"),
    request("/events?limit=100&offset=0"),
    request("/incidents?limit=5&offset=0"),
    request("/notifications?limit=5&offset=0"),
    request("/notifications/channels/status"),
  ]);
  eventsCache = events;
  const latest = [...events].slice(0, 5);
  content.innerHTML = `${statusBanner(health, summary)}
    <div class="metrics-grid">
      ${metric("Configured cameras", summary.camera_count)}
      ${metric("Online cameras", summary.online_camera_count)}
      ${metric("Recorded events", summary.event_count)}
      ${metric("Incidents", summary.incident_count)}
    </div>
    <div class="content-grid">
      <section class="section"><div class="section-head"><h2>Recent recorded events</h2><span>DATABASE RECORDS</span></div>
        ${latest.length ? `<div class="table-wrap"><table><thead><tr><th>Type</th><th>State</th><th>Workflow</th><th>Started</th></tr></thead><tbody>${latest.map(eventRow).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No events recorded.</strong>Nothing is generated to fill this view.</div></div>`}
      </section>
      <section class="section"><div class="section-head"><h2>Subsystems</h2><span>REPORTED STATE</span></div><div class="section-body">${stateRows([
        ["Application process", health.application], ["Database", health.database], ["Migrations", health.migrations],
        ["Cameras", health.camera_state], ["Camera health worker", health.camera_health_worker],
        ["Inference", health.inference_pipeline], ["Model", health.model_state],
        ["Evidence", health.evidence_subsystem], ["Notifications", health.notification_subsystem],
        ["Frontend connectivity", health.frontend_connectivity], ["Measured performance", health.measured_performance],
        ["Accuracy", summary.accuracy_metrics_status],
      ])}</div></section>
    </div>`;
  content.insertAdjacentHTML("beforeend", `<div class="content-grid operational-records">
    <section class="section"><div class="section-head"><h2>Recent incidents</h2><span>DATABASE RECORDS</span></div>${incidents.length ? `<div class="table-wrap"><table><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead><tbody>${incidents.map((incident) => `<tr><td>${escapeHtml(incident.title)}</td><td>${escapeHtml(incident.severity)}</td><td>${escapeHtml(incident.status)}</td><td>${escapeHtml(formatDate(incident.created_at))}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No incident records.</strong>Incidents require an existing event and operator action.</div></div>`}</section>
    <section class="section"><div class="section-head"><h2>Notification state</h2><span>PROVIDER ACCEPTANCE IS NOT READ RECEIPT</span></div><div class="section-body">${stateRows(channels.map((channel) => [channel.channel, `${channel.configuration_state} · QUEUED ${channel.queued} · SENT ${channel.sent} · FAILED ${channel.failed}`]))}</div>${notifications.length ? `<div class="table-wrap"><table><thead><tr><th>Channel</th><th>State</th><th>Recipient</th><th>Updated</th></tr></thead><tbody>${notifications.map((item) => `<tr><td>${escapeHtml(item.channel)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.recipient_role || item.recipient || "UNASSIGNED")}</td><td>${escapeHtml(formatDate(item.sent_at || item.created_at))}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No notifications queued.</strong>Event notification policies create rows when a supported event is confirmed.</div></div>`}</section>
  </div>`);
  setApiIndicator(health.overall_status, health.overall_status);
}

function eventRow(event) {
  return `<tr data-event-id="${escapeHtml(event.id)}"><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.observation_state)}</td><td>${escapeHtml(event.workflow_state)}</td><td>${escapeHtml(formatDate(event.started_at))}</td></tr>`;
}

function formatDate(value) {
  if (!value) return "NOT_AVAILABLE";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "NOT_AVAILABLE" : date.toLocaleString();
}

async function renderCameras() {
  stopWebcamPolling();
  webcamSourceStatus = null;
  webcamDiscovery = null;
  webcamSystemHealth = null;
  webcamCameraError = null;
  const [cameras, health] = await Promise.all([
    request("/cameras?active_only=false"),
    request("/system/health"),
  ]);
  webcamSystemHealth = health;
  webcamCamera = cameras.find((camera) => isWebcamCamera(camera)) || null;

  if (webcamCamera) {
    try {
      webcamSourceStatus = await request(`/cameras/${encodeURIComponent(webcamCamera.id)}/status`);
    } catch (error) {
      webcamCameraError = error.message;
    }
  }

  const webcamAlreadyObserved = webcamSourceStatus?.running && webcamSourceStatus?.observed_frames;
  if (!webcamAlreadyObserved) {
    try {
      webcamDiscovery = await request("/cameras/webcam/devices?max_index=0&read_frame=true");
    } catch (error) {
      webcamDiscovery = { devices: [], discovery_state: "UNAVAILABLE", error: error.message };
    }
  }

  const rows = cameras.map((camera) => `<tr>
    <td><strong>${escapeHtml(camera.name)}</strong><br><span class="muted mono">${escapeHtml(camera.code)}</span></td>
    <td>${escapeHtml(camera.health?.status || "CONFIGURED")}</td>
    <td>${escapeHtml(camera.health?.inference_status || "NOT_RUNNING")}</td>
    <td>${escapeHtml(camera.fps)} fps configured<br><span class="muted">${escapeHtml(camera.health?.measured_fps ?? "NOT_AVAILABLE")} fps measured</span></td>
    <td>${escapeHtml(camera.health?.observed_resolution ?? "NOT_AVAILABLE")}</td>
    <td>${camera.is_active ? "ACTIVE" : "DEACTIVATED"}</td>
  </tr>`).join("");
  content.innerHTML = `${renderWebcamPanel()}
    <div class="filter-bar camera-inventory-head"><p>Configured values and observed telemetry are shown separately.</p><span class="status-pill">${cameras.length} RECORDS</span></div>
    <section class="section"><div class="section-head"><h2>Camera inventory</h2><span>STREAM URL CREDENTIALS REDACTED</span></div>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>Camera</th><th>Connection</th><th>AI state</th><th>Frame rate</th><th>Observed resolution</th><th>Config</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No cameras configured.</strong>Register an authorized source through the camera API.</div></div>`}
    </section>`;

  document.querySelector("#webcam-discover").addEventListener("click", discoverLaptopWebcam);
  document.querySelector("#webcam-start").addEventListener("click", startLaptopWebcam);
  document.querySelector("#webcam-stop").addEventListener("click", stopLaptopWebcam);
  document.querySelector("#webcam-preview-image").addEventListener("error", handleWebcamPreviewError);
  updateWebcamPanel();
  webcamPollingTimer = window.setInterval(() => {
    refreshWebcamStatus().catch((error) => setWebcamError(error.message));
  }, 1000);
}

function isWebcamCamera(camera) {
  return camera.camera_type === "WEBCAM"
    || camera.code === "WEBCAM-0"
    || String(camera.stream_url || "").toLowerCase().startsWith("webcam://");
}

function stopWebcamPolling() {
  if (webcamPollingTimer !== null) {
    window.clearInterval(webcamPollingTimer);
    webcamPollingTimer = null;
  }
  const preview = document.querySelector("#webcam-preview-image");
  if (preview) {
    preview.removeAttribute("src");
    preview.hidden = true;
  }
}

function discoveredLaptopDevice() {
  return webcamDiscovery?.devices?.find((device) => device.index === 0) || null;
}

function webcamIsOnline() {
  return Boolean(webcamSourceStatus?.running && webcamSourceStatus?.observed_frames);
}

function renderWebcamPanel() {
  const discovery = discoveredLaptopDevice();
  const detected = Boolean(discovery?.available || webcamSourceStatus?.observed_frames);
  const stopped = Boolean(webcamCamera && ["NOT_STARTED", "STOPPED"].includes(webcamSourceStatus?.stream_state));
  const initialStatus = webcamIsOnline() ? "CAMERA: ONLINE" : stopped ? "CAMERA: STOPPED" : detected ? "CAMERA: AVAILABLE" : "CAMERA: NOT DETECTED";
  const discoveryState = webcamDiscovery?.discovery_state || (webcamSourceStatus?.observed_frames ? "AVAILABLE" : "CHECKING");
  return `<section class="webcam-panel" aria-labelledby="webcam-title">
    <div class="webcam-heading">
      <div><p class="eyebrow">LOCAL VIDEO SOURCE</p><h2 id="webcam-title">Laptop Webcam <span>— Device 0</span></h2></div>
      <div class="webcam-badges"><span id="webcam-status-badge" class="webcam-badge ${webcamIsOnline() ? "is-online" : "is-idle"}">${escapeHtml(initialStatus)}</span><span id="webcam-detected-badge" class="webcam-badge is-neutral">${escapeHtml(discoveryState)}</span></div>
    </div>
    <div class="webcam-preview" id="webcam-preview">
      <img id="webcam-preview-image" alt="Live preview from Laptop Webcam, device 0" hidden>
      <div class="webcam-preview-empty" id="webcam-preview-empty">${webcamIsOnline() ? "Connecting to the captured frame stream…" : "Live preview will appear after the webcam delivers a real frame."}</div>
      <span class="preview-source-label">REAL CAPTURE · DEVICE 0</span>
    </div>
    <div class="webcam-controls">
      <div class="webcam-actions"><button class="webcam-button webcam-button-primary" id="webcam-start" type="button">Start Camera</button><button class="webcam-button" id="webcam-stop" type="button">Stop Camera</button><button class="webcam-button webcam-button-quiet" id="webcam-discover" type="button">Refresh Detection</button></div>
      <span id="webcam-connection" class="webcam-connection">${webcamIsOnline() ? "Connected; frames observed" : "Not streaming"}</span>
    </div>
    <div class="webcam-telemetry">
      <article class="webcam-stat"><span>Resolution</span><strong id="webcam-resolution">${escapeHtml(webcamResolution())}</strong></article>
      <article class="webcam-stat"><span>Measured FPS</span><strong id="webcam-fps">${escapeHtml(webcamFps())}</strong></article>
      <article class="webcam-stat"><span>Connection</span><strong id="webcam-connection-state">${webcamIsOnline() ? "ONLINE" : "NOT STARTED"}</strong></article>
      <article class="webcam-stat webcam-model-stat"><span>AI model</span><strong id="webcam-model-state">MODEL: ${escapeHtml(webcamSourceStatus?.model_state || webcamSystemHealth?.model_state || "NOT_AVAILABLE")}</strong></article>
    </div>
    <div class="webcam-error-row"><strong>Error status</strong><span id="webcam-error" role="status">${escapeHtml(webcamErrorText())}</span></div>
  </section>`;
}

function webcamResolution() {
  const resolution = webcamSourceStatus?.resolution || discoveredLaptopDevice()?.resolution;
  return resolution?.length === 2 ? `${resolution[0]} × ${resolution[1]}` : "NOT_OBSERVED";
}

function webcamFps() {
  const fps = webcamSourceStatus?.measured_fps;
  return Number.isFinite(fps) && fps > 0 ? `${fps.toFixed(2)} fps` : "NOT_MEASURED";
}

function webcamErrorText() {
  return webcamSourceStatus?.last_error
    || webcamCameraError
    || discoveredLaptopDevice()?.error
    || webcamDiscovery?.error
    || (webcamDiscovery?.discovery_state === "NO_WEBCAM_DETECTED" ? "No real frame was read from device 0." : "No error reported by the backend.");
}

function updateWebcamPanel() {
  if (!document.querySelector("#webcam-status-badge")) return;
  const online = webcamIsOnline();
  const starting = Boolean(webcamSourceStatus && !online && (webcamSourceStatus.is_connected || ["STARTING", "RUNNING"].includes(webcamSourceStatus.stream_state)));
  const stopped = Boolean(webcamCamera && ["NOT_STARTED", "STOPPED"].includes(webcamSourceStatus?.stream_state));
  const detected = Boolean(discoveredLaptopDevice()?.available || webcamSourceStatus?.observed_frames);
  const statusBadge = document.querySelector("#webcam-status-badge");
  statusBadge.textContent = online ? "CAMERA: ONLINE" : starting ? "CAMERA: WAITING FOR FRAME" : webcamSourceStatus?.last_error ? "CAMERA: ERROR" : stopped ? "CAMERA: STOPPED" : detected ? "CAMERA: AVAILABLE" : "CAMERA: NOT DETECTED";
  statusBadge.className = `webcam-badge ${online ? "is-online" : webcamSourceStatus?.last_error ? "is-error" : "is-idle"}`;
  const detectedBadge = document.querySelector("#webcam-detected-badge");
  detectedBadge.textContent = detected ? "DEVICE 0 DETECTED" : webcamDiscovery?.discovery_state || "DETECTION UNAVAILABLE";
  detectedBadge.className = `webcam-badge ${detected ? "is-online" : "is-neutral"}`;
  document.querySelector("#webcam-resolution").textContent = webcamResolution();
  document.querySelector("#webcam-fps").textContent = webcamFps();
  document.querySelector("#webcam-connection-state").textContent = online ? "ONLINE" : starting ? "WAITING FOR FRAME" : stopped ? "STOPPED" : webcamSourceStatus?.stream_state || "NOT STARTED";
  document.querySelector("#webcam-connection").textContent = online
    ? `Connected via ${webcamSourceStatus.source_backend || "camera backend"}; real frames observed`
    : starting ? "Capture opened; waiting for the first real frame" : stopped ? "Stopped; source released" : "Not streaming";
  document.querySelector("#webcam-model-state").textContent = `MODEL: ${webcamSourceStatus?.model_state || webcamSystemHealth?.model_state || "NOT_AVAILABLE"}`;
  document.querySelector("#webcam-error").textContent = webcamErrorText();
  document.querySelector("#webcam-start").disabled = webcamBusy || online || starting || !detected;
  document.querySelector("#webcam-stop").disabled = webcamBusy || !(online || starting);
  document.querySelector("#webcam-discover").disabled = webcamBusy;

  const image = document.querySelector("#webcam-preview-image");
  const empty = document.querySelector("#webcam-preview-empty");
  if (online && image && !image.getAttribute("src")) {
    image.src = `${apiBase}/cameras/${encodeURIComponent(webcamCamera.id)}/preview.mjpg?started=${Date.now()}`;
    image.hidden = false;
    empty.hidden = true;
  } else if (!online && image) {
    image.removeAttribute("src");
    image.hidden = true;
    empty.hidden = false;
    empty.textContent = starting ? "Waiting for the first real webcam frame…" : "Live preview will appear after the webcam delivers a real frame.";
  }
}

function setWebcamError(message) {
  const error = document.querySelector("#webcam-error");
  if (error) error.textContent = message;
}

function handleWebcamPreviewError() {
  const image = document.querySelector("#webcam-preview-image");
  const empty = document.querySelector("#webcam-preview-empty");
  if (image) image.hidden = true;
  if (empty) {
    empty.hidden = false;
    empty.textContent = "The real-frame preview stream could not be opened.";
  }
  setWebcamError("Preview stream request failed; camera capture status is reported separately.");
}

async function discoverLaptopWebcam() {
  webcamBusy = true;
  updateWebcamPanel();
  try {
    webcamDiscovery = await request("/cameras/webcam/devices?max_index=0&read_frame=true");
    if (webcamDiscovery.devices?.some((device) => device.index === 0 && device.available)) {
      webcamCameraError = null;
    }
    updateWebcamPanel();
  } catch (error) {
    webcamDiscovery = { devices: [], discovery_state: "UNAVAILABLE", error: error.message };
    updateWebcamPanel();
  } finally {
    webcamBusy = false;
    updateWebcamPanel();
  }
}

async function startLaptopWebcam() {
  webcamBusy = true;
  webcamCameraError = null;
  webcamSourceStatus = { stream_state: "STARTING", is_connected: false, observed_frames: false };
  updateWebcamPanel();
  try {
    if (webcamCamera) {
      await request(`/cameras/${encodeURIComponent(webcamCamera.id)}/start`, { method: "POST" });
    } else {
      const device = discoveredLaptopDevice();
      const [width, height] = device?.resolution || [640, 480];
      webcamCamera = await request("/cameras/webcam/register", {
        method: "POST",
        body: JSON.stringify({ device_index: 0, width, height, fps: 15, name: "Laptop Webcam", code: "WEBCAM-0" }),
      });
    }
    await waitForWebcamFrame();
  } catch (error) {
    webcamSourceStatus = { stream_state: "SOURCE_UNAVAILABLE", is_connected: false, observed_frames: false, last_error: error.message };
    setWebcamError(error.message);
  } finally {
    webcamBusy = false;
    updateWebcamPanel();
  }
}

async function waitForWebcamFrame() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await refreshWebcamStatus();
    if (webcamSourceStatus?.observed_frames) return;
    if (["SOURCE_UNAVAILABLE", "RECONNECT_EXHAUSTED"].includes(webcamSourceStatus?.stream_state)) return;
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  setWebcamError("No real frame was observed within 20 seconds. Check camera privacy settings and device access.");
}

async function refreshWebcamStatus() {
  if (!webcamCamera) return;
  webcamSourceStatus = await request(`/cameras/${encodeURIComponent(webcamCamera.id)}/status`);
  webcamCameraError = null;
  if (!webcamIsOnline() && webcamDiscovery === null) {
    webcamDiscovery = { devices: [], discovery_state: "CHECKING" };
    try {
      webcamDiscovery = await request("/cameras/webcam/devices?max_index=0&read_frame=true");
    } catch (error) {
      webcamDiscovery = { devices: [], discovery_state: "UNAVAILABLE", error: error.message };
    }
  }
  updateWebcamPanel();
}

async function stopLaptopWebcam() {
  if (!webcamCamera) return;
  webcamBusy = true;
  const image = document.querySelector("#webcam-preview-image");
  if (image) {
    image.removeAttribute("src");
    image.hidden = true;
  }
  webcamSourceStatus = { ...(webcamSourceStatus || {}), stream_state: "STOPPING", running: false, is_connected: false, observed_frames: false };
  updateWebcamPanel();
  try {
    webcamSourceStatus = await request(`/cameras/${encodeURIComponent(webcamCamera.id)}/stop`, { method: "POST" });
    webcamSourceStatus.running = false;
    webcamSourceStatus.is_connected = false;
    webcamSourceStatus.observed_frames = false;
  } catch (error) {
    webcamCameraError = error.message;
    setWebcamError(error.message);
  } finally {
    webcamBusy = false;
    updateWebcamPanel();
  }
}

async function renderEvents() {
  const events = await request("/events?limit=200&offset=0");
  eventsCache = events;
  const rows = events.map((event) => `<tr><td class="mono">${escapeHtml(event.id.slice(0, 12))}</td><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.observation_state)}</td><td>${escapeHtml(event.workflow_state)}</td><td>${escapeHtml(event.severity)}</td><td>${escapeHtml(formatDate(event.started_at))}</td><td>${transitionButton(event)}</td></tr>`).join("");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Persisted events</h2><span>SAFETY EVENTS REQUIRE REAL MODEL OUTPUT AND CONFIGURATION</span></div>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Event ID</th><th>Type</th><th>Observation</th><th>Workflow</th><th>Severity</th><th>Timestamp</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No events recorded.</strong>Events are generated only from observed camera-health failures or supported configured detector output.</div></div>`}
    </section>`;
  content.querySelectorAll("[data-transition]").forEach((button) => button.addEventListener("click", () => applyTransition(button.dataset.event, button.dataset.transition)));
}

function transitionButton(event) {
  const choices = {
    NEW: "UNACKNOWLEDGED", UNACKNOWLEDGED: "ACKNOWLEDGED", ACKNOWLEDGED: "ASSIGNED",
    ASSIGNED: "UNDER_INVESTIGATION", UNDER_INVESTIGATION: "ACTION_REQUIRED", ACTION_REQUIRED: "RESOLVED", RESOLVED: "CLOSED",
  };
  const next = choices[event.workflow_state];
  return next ? `<button class="inline-button" data-event="${escapeHtml(event.id)}" data-transition="${next}">Move to ${escapeHtml(next)}</button>` : "-";
}

async function applyTransition(eventId, newState) {
  const reason = window.prompt(`Reason for transition to ${newState}:`);
  if (!reason || !reason.trim()) return;
  try {
    await request(`/events/${encodeURIComponent(eventId)}/transitions`, { method: "POST", body: JSON.stringify({ new_state: newState, reason }) });
    showMessage("Workflow transition recorded.");
    await renderEvents();
  } catch (error) {
    showMessage(error.message);
  }
}

function renderAlarmState(events, error = null) {
  const region = document.querySelector("#alarm-region");
  if (!region) return;
  if (error) {
    region.innerHTML = `<div class="alarm-banner alarm-unavailable"><div><span class="alarm-label">ALARM STATE</span><strong>UNAVAILABLE</strong></div><span>${escapeHtml(error)}</span></div>`;
    return;
  }
  activeAlarmEvents = events.filter((event) => (
    ALARM_EVENT_TYPES.has(event.event_type)
    && event.observation_state === "CONFIRMED"
    && ["NEW", "UNACKNOWLEDGED"].includes(event.workflow_state)
  ));
  if (!activeAlarmEvents.length) {
    region.innerHTML = `<div class="alarm-banner alarm-clear"><div class="alarm-copy"><span class="alarm-label">ALARM STATE</span><strong>NO ACTIVE CONFIRMED SAFETY EVENTS</strong><span>Camera health and unconfirmed observations are not safety alarms.</span></div><button class="alarm-sound-button" id="alarm-sound-toggle" type="button" aria-pressed="${alarmSoundEnabled}">${alarmSoundEnabled ? "Disable browser sound" : "Enable browser sound"}</button></div>`;
    region.querySelector("#alarm-sound-toggle").addEventListener("click", toggleAlarmSound);
    synchronizeAlarmSound();
    return;
  }

  region.innerHTML = `<div class="alarm-banner alarm-active" role="alert">
    <div class="alarm-summary"><span class="alarm-label">ACTIVE SAFETY ALARM</span><strong>${activeAlarmEvents.length} CONFIRMED EVENT${activeAlarmEvents.length === 1 ? "" : "S"} AWAITING ACKNOWLEDGEMENT</strong></div>
    <div class="alarm-event-list">${activeAlarmEvents.map((event) => `<div class="alarm-event-row"><span><strong>${escapeHtml(event.event_type)}</strong><span>${escapeHtml(event.severity)} · ${escapeHtml(formatDate(event.started_at))}</span></span><button class="alarm-ack-button" data-alarm-ack="${escapeHtml(event.id)}" type="button">Acknowledge</button></div>`).join("")}</div>
    <button class="alarm-sound-button" id="alarm-sound-toggle" type="button" aria-pressed="${alarmSoundEnabled}">${alarmSoundEnabled ? "Disable browser sound" : "Enable browser sound"}</button>
  </div>`;
  region.querySelectorAll("[data-alarm-ack]").forEach((button) => {
    button.addEventListener("click", () => acknowledgeAlarm(button.dataset.alarmAck));
  });
  region.querySelector("#alarm-sound-toggle").addEventListener("click", toggleAlarmSound);
  synchronizeAlarmSound();
}

async function refreshAlarmState() {
  if (alarmRequestInFlight) return;
  alarmRequestInFlight = true;
  try {
    const events = await request("/events?limit=500&offset=0");
    renderAlarmState(events);
  } catch (error) {
    activeAlarmEvents = [];
    stopAlarmSound();
    renderAlarmState([], error.message);
  } finally {
    alarmRequestInFlight = false;
  }
}

function startAlarmPolling() {
  if (alarmPollingTimer !== null) return;
  refreshAlarmState();
  alarmPollingTimer = window.setInterval(refreshAlarmState, 5000);
}

async function acknowledgeAlarm(eventId) {
  const button = document.querySelector(`[data-alarm-ack="${CSS.escape(eventId)}"]`);
  if (button) button.disabled = true;
  try {
    await request(`/events/${encodeURIComponent(eventId)}/acknowledgements`, {
      method: "POST",
      body: JSON.stringify({ notes: "Acknowledged from the local dashboard alarm" }),
    });
    showMessage("Acknowledgement recorded. This anonymous deployment cannot attribute the action to a named operator.");
    await refreshAlarmState();
  } catch (error) {
    showMessage(error.message);
    if (button) button.disabled = false;
  }
}

async function toggleAlarmSound() {
  if (alarmSoundEnabled) {
    alarmSoundEnabled = false;
    stopAlarmSound();
    renderAlarmState(activeAlarmEvents);
    return;
  }
  const AudioContextType = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextType) {
    showMessage("Browser audio is unavailable; visible alarm state remains active.");
    return;
  }
  try {
    alarmAudioContext ||= new AudioContextType();
    await alarmAudioContext.resume();
    alarmSoundEnabled = true;
    renderAlarmState(activeAlarmEvents);
    synchronizeAlarmSound();
  } catch {
    showMessage("Browser audio could not be enabled; visible alarm state remains active.");
  }
}

function synchronizeAlarmSound() {
  if (!alarmSoundEnabled || !activeAlarmEvents.length) {
    stopAlarmSound();
    return;
  }
  if (alarmSoundTimer !== null) return;
  playAlarmTone();
  alarmSoundTimer = window.setInterval(playAlarmTone, 1800);
}

function playAlarmTone() {
  if (!alarmAudioContext || alarmAudioContext.state !== "running") return;
  const oscillator = alarmAudioContext.createOscillator();
  const gain = alarmAudioContext.createGain();
  const now = alarmAudioContext.currentTime;
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(880, now);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.08, now + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
  oscillator.connect(gain);
  gain.connect(alarmAudioContext.destination);
  oscillator.start(now);
  oscillator.stop(now + 0.3);
}

function stopAlarmSound() {
  if (alarmSoundTimer !== null) {
    window.clearInterval(alarmSoundTimer);
    alarmSoundTimer = null;
  }
}

async function renderEvidence() {
  const events = eventsCache.length ? eventsCache : await request("/events?limit=200&offset=0");
  eventsCache = events;
  if (!events.length) {
    content.innerHTML = `<section class="section"><div class="section-head"><h2>Event evidence</h2><span>LINKED MATERIAL ONLY</span></div><div class="empty-state"><div><strong>No events recorded.</strong>Evidence is not created without an event and captured frame.</div></div></section>`;
    return;
  }
  content.innerHTML = `<div class="filter-bar"><p>Evidence files are served only for persisted events.</p><select id="evidence-event" class="select-input" aria-label="Select event">${events.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.event_type)} · ${escapeHtml(item.id.slice(0, 10))}</option>`).join("")}</select></div><section id="evidence-list" class="section"></section>`;
  const select = content.querySelector("#evidence-event");
  const load = async () => {
    const data = await request(`/events/${encodeURIComponent(select.value)}/evidence`);
    const section = content.querySelector("#evidence-list");
    section.innerHTML = `<div class="section-head"><h2>Evidence records</h2><span>${data.length} LINKED RECORDS</span></div>${data.length ? `<div class="table-wrap"><table><thead><tr><th>Type</th><th>Captured</th><th>SHA-256</th><th>File</th></tr></thead><tbody>${data.map((item) => `<tr><td>${escapeHtml(item.evidence_type)}</td><td>${escapeHtml(formatDate(item.created_at))}</td><td class="mono">${escapeHtml(item.file_hash || "NOT_AVAILABLE")}</td><td>${item.status === "AVAILABLE" ? `<button class="inline-button" data-evidence="${escapeHtml(item.id)}" data-event="${escapeHtml(item.event_id)}">View snapshot</button>` : "EVIDENCE_NOT_AVAILABLE"}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No evidence linked to this event.</strong>EVIDENCE_NOT_AVAILABLE</div></div>`}`;
    section.querySelectorAll("[data-evidence]").forEach((button) => button.addEventListener("click", () => openEvidence(button.dataset.event, button.dataset.evidence)));
  };
  select.addEventListener("change", () => load().catch((error) => showMessage(error.message)));
  await load();
}

async function openEvidence(eventId, evidenceId) {
  try {
    const response = await fetch(`${apiBase}/events/${encodeURIComponent(eventId)}/evidence/${encodeURIComponent(evidenceId)}/content`);
    if (!response.ok) throw new Error(`Evidence request failed (${response.status})`);
    const objectUrl = URL.createObjectURL(await response.blob());
    const image = window.open(objectUrl, "_blank", "noopener");
    if (!image) URL.revokeObjectURL(objectUrl);
  } catch (error) { showMessage(error.message); }
}

async function renderAnalytics() {
  const data = await request("/analytics/summary");
  content.innerHTML = `<div class="status-banner"><div><strong>${escapeHtml(data.data_status)}</strong><span>All values are queried from current database records.</span></div><span class="status-pill">ACCURACY ${escapeHtml(data.accuracy_metrics_status)}</span></div>
    <div class="metrics-grid">${metric("Cameras", data.camera_count)}${metric("Online cameras", data.online_camera_count)}${metric("Events", data.event_count)}${metric("Verified events", data.verified_event_count)}${metric("Incidents", data.incident_count)}${metric("Near-misses", data.near_miss_count)}${metric("Corrective actions", data.corrective_action_count)}${metric("IGL validated", data.igl_validated ? "TRUE" : "FALSE")}</div>
    <div class="section"><div class="section-head"><h2>Measurement boundary</h2><span>NO SYNTHETIC CHARTS</span></div><div class="section-body">${stateRows([["Detection accuracy", "NOT_MEASURED"], ["Tracking accuracy", "NOT_MEASURED"], ["IGL validation", "NOT_VALIDATED"]])}</div></div>`;
}

async function renderConfiguration() {
  const [plants, areas, zones, rules, cameras, detectorConfigs, safetyRules, thresholds, escalationPolicies, notificationPolicies, notificationChannels] = await Promise.all([
    request("/configuration/plants"),
    request("/configuration/areas"),
    request("/configuration/zones"),
    request("/configuration/ppe-rules"),
    request("/cameras?active_only=true"),
    request("/configuration/detector-configs"),
    request("/configuration/safety-rules"),
    request("/configuration/operating-thresholds"),
    request("/configuration/escalation-policies"),
    request("/configuration/notification-policies"),
    request("/notifications/channels/status"),
  ]);
  const options = (items, label) => items.length
    ? items.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(label(item))}</option>`).join("")
    : "";
  const scopedOptions = (items, label) => `<option value="">No scope</option>${options(items, label)}`;
  const currentState = (title, items, renderer) => `<section class="section"><div class="section-head"><h2>${escapeHtml(title)}</h2><span>${items.length} RECORDS</span></div>${items.length ? `<div class="table-wrap">${renderer(items)}</div>` : `<div class="empty-state"><div><strong>No ${escapeHtml(title.toLowerCase())} configured.</strong>Nothing is pre-populated for IGL.</div></div>`}</section>`;
  const iglConfigurationStatus = plants.length ? "CONFIGURED_NOT_VALIDATED" : "NOT_CONFIGURED";
  content.innerHTML = `<div class="status-banner"><div><strong>IGL_CONFIGURATION_STATUS = ${escapeHtml(iglConfigurationStatus)}</strong><span>These forms store operator-supplied configuration only. No IGL layout or SOP is assumed.</span></div></div>
    <div class="content-grid configuration-grid">
      <form class="section config-form" id="plant-config-form"><div class="section-head"><h2>Plant</h2><span>AUTHORIZED DATA</span></div><div class="section-body">
        <label>Plant name<input name="name" required maxlength="100"></label><label>Plant code<input name="code" required maxlength="50"></label><label>Location<input name="location" maxlength="255"></label><label>Description<input name="description"></label><button class="inline-button" type="submit">Add plant</button>
      </div></form>
      <form class="section config-form" id="area-config-form"><div class="section-head"><h2>Area</h2><span>AUTHORIZED DATA</span></div><div class="section-body">
        <label>Parent plant<select name="plant_id" required>${options(plants, (item) => `${item.name} (${item.code})`)}</select></label><label>Area name<input name="name" required maxlength="100"></label><label>Area code<input name="code" required maxlength="50"></label><label>Description<input name="description"></label><button class="inline-button" type="submit" ${plants.length ? "" : "disabled"}>Add area</button>
      </div></form>
      <form class="section config-form" id="zone-config-form"><div class="section-head"><h2>Zone</h2><span>POLYGON OPTIONAL</span></div><div class="section-body">
        <label>Parent area<select name="area_id" required>${options(areas, (item) => `${item.name} (${item.code})`)}</select></label><label>Zone name<input name="name" required maxlength="100"></label><label>Zone code<input name="code" required maxlength="50"></label><label>Zone type<select name="zone_type" required><option value="WORK_AREA">WORK_AREA</option><option value="RESTRICTED">RESTRICTED</option><option value="HAZARDOUS">HAZARDOUS</option><option value="PPE_MANDATORY">PPE_MANDATORY</option><option value="VEHICLE_ZONE">VEHICLE_ZONE</option><option value="EXCLUSION_ZONE">EXCLUSION_ZONE</option></select></label><label>Configured polygon JSON<textarea name="geometry_json" rows="3" placeholder="[[x1,y1],[x2,y2],[x3,y3]]"></textarea></label><button class="inline-button" type="submit" ${areas.length ? "" : "disabled"}>Add zone</button>
      </div></form>
      <form class="section config-form" id="ppe-config-form"><div class="section-head"><h2>PPE rule</h2><span>NOT_IMPLEMENTED</span></div><div class="section-body">
        <p class="config-note">This stores an operator rule only; no PPE detector is implemented or evaluated by the live model path.</p>
        <label>Zone<select name="zone_id" required>${options(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>PPE type from authorized SOP<input name="ppe_type" required maxlength="50"></label><label>Threshold source<select name="threshold_source"><option value="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION">ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION</option><option value="CONFIGURED">CONFIGURED</option></select></label><label>Confidence threshold<input name="min_confidence" type="number" min="0" max="1" step="0.01"></label><label>Source reference<input name="source_reference" maxlength="500" placeholder="Required when source is CONFIGURED"></label><label class="checkbox-label"><input name="is_mandatory" type="checkbox"> Mandatory</label><button class="inline-button" type="submit" ${zones.length ? "" : "disabled"}>Add PPE rule</button>
      </div></form>
    </div>
    <details class="config-disclosure"><summary>Detector, threshold, and response policies</summary><div class="content-grid configuration-grid config-advanced-grid">
      <form class="section config-form" data-endpoint="/configuration/detector-configs" data-json-fields="parameters_json,required_classes_json" data-checkbox-fields="is_enabled"><div class="section-head"><h2>Detector configuration</h2><span>NOT VALIDATED</span></div><div class="section-body">
        <label>Detector<select name="detector_key" required>${["PPE", "HELMET", "SAFETY_VEST", "RESTRICTED_ZONE", "PROXIMITY", "FALL", "FIRE", "SMOKE", "LEAKAGE", "UNSAFE_BEHAVIOR", "PERSON"].map((key) => `<option value="${key}">${key}</option>`).join("")}</select></label>
        <label>Camera<select name="camera_id">${scopedOptions(cameras, (item) => `${item.name} (${item.code})`)}</select></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label>
        <label>Parameters JSON<textarea name="parameters_json" rows="2" placeholder='{"minimum_confidence":0.5}'></textarea></label><label>Required classes JSON<textarea name="required_classes_json" rows="2" placeholder='["person"]'></textarea></label>
        <label>Threshold source<select name="threshold_source"><option value="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION">ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION</option><option value="CONFIGURED">CONFIGURED</option></select></label><label>Source reference<input name="source_reference" maxlength="500"></label><label class="checkbox-label"><input name="is_enabled" type="checkbox"> Enable configuration (does not validate a detector)</label><button class="inline-button" type="submit">Save detector config</button>
      </div></form>
      <form class="section config-form" data-endpoint="/configuration/safety-rules"><div class="section-head"><h2>Safety rule</h2><span>REGISTRY ONLY</span></div><div class="section-body">
        <p class="config-note">SafetyRule records do not currently drive live evaluation. Runtime uses enabled detector configurations, supported classes, and configured zone geometry.</p>
        <label>Rule code<input name="code" required maxlength="50" pattern="[A-Z0-9_-]+"></label><label>Name<input name="name" required maxlength="150"></label><label>Category<input name="category" maxlength="50"></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Description<input name="description"></label><label>Source reference<input name="source_reference" maxlength="500"></label><button class="inline-button" type="submit">Save unvalidated rule</button>
      </div></form>
      <form class="section config-form" data-endpoint="/configuration/operating-thresholds" data-number-fields="value"><div class="section-head"><h2>Operating threshold</h2><span>NOT VALIDATED</span></div><div class="section-body">
        <label>Code<input name="code" required maxlength="80"></label><label>Metric<input name="metric" required maxlength="80"></label><label>Value<input name="value" type="number" step="any" required></label><label>Unit<input name="unit" maxlength="30"></label><label>Comparison<select name="comparison"><option>GT</option><option>GTE</option><option>LT</option><option>LTE</option><option>EQ</option></select></label><label>Camera<select name="camera_id">${scopedOptions(cameras, (item) => `${item.name} (${item.code})`)}</select></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Threshold source<select name="threshold_source"><option value="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION">ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION</option><option value="CONFIGURED">CONFIGURED</option></select></label><label>Source reference<input name="source_reference" maxlength="500"></label><button class="inline-button" type="submit">Save threshold</button>
      </div></form>
      <form class="section config-form" data-endpoint="/configuration/escalation-policies" data-number-fields="escalate_after_seconds,escalation_level"><div class="section-head"><h2>Escalation policy</h2><span>NO DELIVERY GUARANTEE</span></div><div class="section-body">
        <label>Name<input name="name" required maxlength="150"></label><label>Event type<input name="event_type" maxlength="50"></label><label>Severity<input name="severity" maxlength="20"></label><label>Camera<select name="camera_id">${scopedOptions(cameras, (item) => `${item.name} (${item.code})`)}</select></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Delay seconds<input name="escalate_after_seconds" type="number" min="1" value="900" required></label><label>From role<input name="from_role" maxlength="50"></label><label>To role<select name="to_role" required><option>ADMIN</option><option>SAFETY_OFFICER</option><option>PLANT_MANAGER</option><option>OPERATOR</option></select></label><label>Level<input name="escalation_level" type="number" min="1" max="10" value="1" required></label><label>Source reference<input name="source_reference" maxlength="500"></label><button class="inline-button" type="submit">Save escalation policy</button>
      </div></form>
      <form class="section config-form" data-endpoint="/configuration/notification-policies" data-number-fields="dedup_window_seconds" data-checkbox-fields="is_enabled"><div class="section-head"><h2>Notification policy</h2><span>DELIVERY STATUS BELOW</span></div><div class="section-body">
        <label>Name<input name="name" required maxlength="150"></label><label>Event type<input name="event_type" maxlength="50"></label><label>Severity<input name="severity" maxlength="20"></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Channel<select name="channel"><option>DASHBOARD</option><option>EMAIL</option><option>WHATSAPP</option><option>WEBHOOK</option><option>SMS</option><option>TEAMS</option><option>BUZZER</option></select></label><label>Recipient role<select name="recipient_role" required><option>ADMIN</option><option>SAFETY_OFFICER</option><option>PLANT_MANAGER</option><option>OPERATOR</option></select></label><label>Deduplication window seconds<input name="dedup_window_seconds" type="number" min="0" max="86400" value="300" required></label><label>Source reference<input name="source_reference" maxlength="500"></label><label class="checkbox-label"><input name="is_enabled" type="checkbox" checked> Enable policy</label><button class="inline-button" type="submit">Save notification policy</button>
      </div></form>
    </div></details>
    <section class="section notification-transport"><div class="section-head"><h2>Delivery transports</h2><span>TEST SENDS CONTACT REAL RECIPIENTS</span></div><div class="section-body">${stateRows(notificationChannels.filter((item) => ["EMAIL", "WHATSAPP"].includes(item.channel)).map((item) => [item.channel, `${item.configuration_state} · QUEUED ${item.queued} · SENT ${item.sent} · FAILED ${item.failed}`]))}<p class="config-note">Configure transport credentials in the backend environment, restart the API, then send only to an operator-approved recipient. A provider acceptance is not proof that a person read the message.</p><div class="notification-test-grid"><form id="email-test-form"><label>Test email recipient<input name="recipient" type="email" required autocomplete="email"></label><button class="inline-button" type="submit">Send test email</button></form><form id="whatsapp-test-form"><label>WhatsApp recipient in E.164 format<input name="recipient" type="tel" required placeholder="+15551234567" autocomplete="tel"></label><button class="inline-button" type="submit">Send test WhatsApp</button></form></div></div></section>
    <div class="content-grid configuration-records">
      ${currentState("Plants", plants, (items) => `<table><thead><tr><th>Name</th><th>Code</th><th>Location</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.code)}</td><td>${escapeHtml(item.location || "NOT_AVAILABLE")}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Zones", zones, (items) => `<table><thead><tr><th>Name</th><th>Type</th><th>Geometry</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.zone_type)}</td><td>${item.geometry_json ? "CONFIGURED" : "NOT_CONFIGURED"}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("PPE rules", rules, (items) => `<table><thead><tr><th>PPE type</th><th>Threshold source</th><th>Status</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.ppe_type)}</td><td>${escapeHtml(item.threshold_source)}</td><td>${escapeHtml(item.validation_status)}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Detector configs", detectorConfigs, (items) => `<table><thead><tr><th>Detector</th><th>Camera</th><th>Zone</th><th>Enabled</th><th>Validation</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.detector_key)}</td><td>${escapeHtml(item.camera_id || "SITE/ZONE")}</td><td>${escapeHtml(item.zone_id || "NOT_SCOPED")}</td><td>${item.is_enabled ? "ENABLED" : "DISABLED"}</td><td>${escapeHtml(item.validation_status)}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Safety rules", safetyRules, (items) => `<table><thead><tr><th>Code</th><th>Name</th><th>Zone</th><th>State</th><th>Validation</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.code)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.zone_id || "SITE")}</td><td>${item.is_active ? "ACTIVE" : "INACTIVE"}</td><td>${escapeHtml(item.validation_status)}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Operating thresholds", thresholds, (items) => `<table><thead><tr><th>Code</th><th>Value</th><th>Scope</th><th>Source</th><th>Validation</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.code)}</td><td>${escapeHtml(item.value)} ${escapeHtml(item.unit || "")}</td><td>${escapeHtml(item.camera_id || item.zone_id || "NOT_SCOPED")}</td><td>${escapeHtml(item.threshold_source)}</td><td>${escapeHtml(item.validation_status)}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Escalation policies", escalationPolicies, (items) => `<table><thead><tr><th>Name</th><th>Event</th><th>Target</th><th>Delay</th><th>Active</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.event_type || "ALL")}</td><td>${escapeHtml(item.to_role)}</td><td>${escapeHtml(item.escalate_after_seconds)}s</td><td>${item.is_active ? "ACTIVE" : "INACTIVE"}</td></tr>`).join("")}</tbody></table>`)}
      ${currentState("Notification policies", notificationPolicies, (items) => `<table><thead><tr><th>Name</th><th>Channel</th><th>Role</th><th>Enabled</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.channel)}</td><td>${escapeHtml(item.recipient_role)}</td><td>${item.is_enabled ? "ENABLED" : "DISABLED"}</td></tr>`).join("")}</tbody></table>`)}
    </div>`;
  content.querySelectorAll(".config-form").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    const endpoint = form.dataset.endpoint || ({
      "plant-config-form": "/configuration/plants",
      "area-config-form": "/configuration/areas",
      "zone-config-form": "/configuration/zones",
      "ppe-config-form": "/configuration/ppe-rules",
    })[form.id];
    for (const field of (form.dataset.jsonFields || "").split(",").filter(Boolean)) {
      try { values[field] = values[field]?.trim() ? JSON.parse(values[field]) : null; }
      catch { showMessage(`${field} JSON is invalid.`); return; }
    }
    if (form.id === "zone-config-form") {
      try { values.geometry_json = values.geometry_json?.trim() ? JSON.parse(values.geometry_json) : null; }
      catch { showMessage("geometry_json must be valid JSON."); return; }
    }
    for (const field of (form.dataset.numberFields || "").split(",").filter(Boolean)) {
      values[field] = values[field] === "" ? null : Number(values[field]);
    }
    if (form.id === "ppe-config-form") {
      values.is_mandatory = form.elements.is_mandatory.checked;
      values.min_confidence = values.min_confidence === "" ? null : Number(values.min_confidence);
    }
    for (const field of (form.dataset.checkboxFields || "").split(",").filter(Boolean)) {
      values[field] = form.elements[field].checked;
    }
    Object.keys(values).forEach((key) => { if (values[key] === "") values[key] = null; });
    try {
      await request(endpoint, { method: "POST", body: JSON.stringify(values) });
      showMessage("Configuration saved. Validation state remains NOT_VALIDATED; anonymous actor attribution is unavailable.");
      await renderConfiguration();
    } catch (error) { showMessage(error.message); }
  }));
  content.querySelectorAll("#email-test-form, #whatsapp-test-form").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const recipient = new FormData(form).get("recipient");
    const channel = form.id === "email-test-form" ? "email" : "whatsapp";
    try {
      const result = await request(`/notifications/test/${channel}`, { method: "POST", body: JSON.stringify({ recipient }) });
      showMessage(`${result.status}: ${result.error || `accepted by ${result.provider}`}`);
    } catch (error) { showMessage(error.message); }
  }));
}

async function renderSystem() {
  const health = await request("/system/health");
  const model = health.model || {};
  content.innerHTML = `<div class="status-banner"><div><strong>${escapeHtml(health.overall_status)}</strong><span>Derived from available subsystem checks. This does not imply real-input validation.</span></div><span class="status-pill">${escapeHtml(health.validation_status)}</span></div>
    <div class="content-grid"><section class="section"><div class="section-head"><h2>Subsystem status</h2></div><div class="section-body">${stateRows([
      ["Application process", health.application], ["Database", health.database], ["Migrations", health.migrations],
      ["Cameras", health.camera_state], ["Camera monitor", health.camera_health_worker],
      ["Inference pipeline", health.inference_pipeline], ["Model", health.model_state], ["Evidence", health.evidence_subsystem],
      ["Notifications", health.notification_subsystem], ["Frontend connectivity", health.frontend_connectivity],
      ["Measured performance", health.measured_performance], ["IGL validated", health.igl_validated],
    ])}</div></section><section class="section"><div class="section-head"><h2>Model configuration</h2></div><div class="section-body">${stateRows([
      ["Name", model.model_name], ["Version", model.model_version], ["Classes", model.classes?.length ?? "NOT_AVAILABLE"],
      ["Confidence threshold", model.confidence_threshold], ["Inference count", model.inference_count], ["Average latency", model.average_inference_latency_ms ?? "NOT_MEASURED"],
    ])}</div></section></div>`;
}

async function renderAudit() {
  const rows = await request("/identity/audit-logs?limit=100");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Audit activity</h2><span>LAST 100 RECORDS</span></div>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Resource</th><th>Actor</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(formatDate(row.timestamp))}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.resource_type)} ${escapeHtml(row.resource_id || "")}</td><td>${escapeHtml(row.user_id || "SYSTEM")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No audit records available.</strong></div></div>`}</section>`;
}

const renderers = { overview: renderOverview, cameras: renderCameras, events: renderEvents, evidence: renderEvidence, configuration: renderConfiguration, analytics: renderAnalytics, system: renderSystem, audit: renderAudit, unavailable: async () => { content.innerHTML = unavailableMarkup(); } };

async function navigate(view) {
  if (view !== "cameras") stopWebcamPolling();
  currentView = view;
  const [kicker, title] = labels[view] || labels.overview;
  document.querySelector("#view-kicker").textContent = kicker;
  document.querySelector("#view-title").textContent = title;
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  showMessage("", false);
  content.innerHTML = `<div class="empty-state"><div><strong>Loading current API data</strong>Please wait.</div></div>`;
  try {
    await (renderers[view] || renderers.unavailable)();
    document.querySelector("#last-updated").textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    content.innerHTML = `<section class="section"><div class="empty-state"><div><strong>NOT_AVAILABLE</strong>${escapeHtml(error.message)}</div></div></section>`;
    setApiIndicator(false, "API data unavailable");
  }
}

function setApiIndicator(status, text) {
  const dot = document.querySelector("#api-dot");
  // Only an explicitly OPERATIONAL backend may show the online indicator.
  const color = status === "OPERATIONAL" ? "dot-green" : status === "DEGRADED" ? "dot-amber" : "dot-red";
  dot.className = `status-dot ${color}`;
  document.querySelector("#api-state-text").textContent = text;
}

function showApplication() {
  loginShell.hidden = true;
  appShell.hidden = false;
  document.querySelector("#logout-button").hidden = !authToken;
  startAlarmPolling();
  navigate(currentView);
}

function showLogin(message = "") {
  stopWebcamPolling();
  if (alarmPollingTimer) clearInterval(alarmPollingTimer);
  alarmPollingTimer = null;
  appShell.hidden = true;
  loginShell.hidden = false;
  document.querySelector("#login-message").textContent = message;
}

async function initializeApplication() {
  try {
    const response = await fetch(`${apiBase}/system/health`);
    if (!response.ok) throw new Error(`API health unavailable (${response.status})`);
    const health = await response.json();
    if (String(health.access_control || "").startsWith("ANONYMOUS_ACCESS_ENABLED")) {
      showApplication();
      return;
    }
    if (authToken) {
      await request("/auth/me");
      showApplication();
      return;
    }
    showLogin("Sign in to access the operations dashboard.");
  } catch (error) {
    showLogin(error.message);
  }
}

document.querySelector("#api-base").value = apiBase;
document.querySelector("#login-api-base").value = apiBase;
document.querySelector("#login-api-base").addEventListener("change", (event) => {
  authToken = null;
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  apiBase = event.target.value.trim().replace(/\/$/, "");
  sessionStorage.setItem(API_STORAGE_KEY, apiBase);
  document.querySelector("#api-base").value = apiBase;
  initializeApplication();
});
document.querySelector("#api-base").addEventListener("change", (event) => {
  authToken = null;
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  apiBase = event.target.value.trim().replace(/\/$/, "");
  sessionStorage.setItem(API_STORAGE_KEY, apiBase);
  document.querySelector("#login-api-base").value = apiBase;
  navigate(currentView);
});

document.querySelector("#navigation").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) navigate(button.dataset.view);
});
document.querySelector("#refresh-button").addEventListener("click", () => navigate(currentView));
document.querySelector("#logout-button").addEventListener("click", () => {
  authToken = null;
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  showLogin("You have signed out.");
});
document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form).entries());
  document.querySelector("#login-message").textContent = "Signing in…";
  try {
    const response = await fetch(`${apiBase}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Sign in failed (${response.status})`);
    authToken = payload.access_token;
    sessionStorage.setItem(AUTH_TOKEN_KEY, authToken);
    form.reset();
    showApplication();
  } catch (error) {
    document.querySelector("#login-message").textContent = error.message;
  }
});

initializeApplication();
