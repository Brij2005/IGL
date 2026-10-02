"use strict";

const API_STORAGE_KEY = "iglSafetyApiBase";
const AUTH_TOKEN_KEY = "iglSafetyAccessToken";
const DEFAULT_API = "http://127.0.0.1:8000/api/v1";
const WORKER_UNSUPPORTED_DETECTORS = [["helmet_status", "HELMET"], ["ppe_status", "PPE"], ["phone_status", "PHONE"]];
const labels = {
  overview: ["OPERATIONS / CURRENT STATE", "Overview"],
  cameras: ["MONITOR / INPUTS", "Cameras"],
  workers: ["MODEL OUTPUT / TRACK SESSIONS", "Workers"],
  events: ["REVIEW / WORKFLOW", "Events"],
  incidents: ["RESPONSE / CASE MANAGEMENT", "Incidents"],
  alarm: ["RESPONSE / SOFTWARE ALARM", "Alarm center"],
  rules: ["CONFIGURATION / CAPABILITIES", "Rules"],
  notifications: ["DELIVERY / PROVIDER STATUS", "Notifications"],
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
let activeAlarmsState = [];
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
let webcamPreviewTimer = null;
let webcamPreviewInFlight = false;
let webcamPreviewObjectUrl = null;
let webcamPreviewController = null;
let webcamBusy = false;
let webcamCameraError = null;
let currentIdentity = null;
let browserCameraStream = null;
let browserCameraFrames = 0;
let browserCameraFrameStartedAt = 0;
let browserCameraFrameHandle = null;
let workersPollingTimer = null;
let workersRequestInFlight = false;
let workersRequestController = null;

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

function userCan(permission) {
  const permissions = currentIdentity?.role?.permissions_json;
  return !currentIdentity || permissions?.includes("*") || permissions?.includes(permission);
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
      currentIdentity = null;
      sessionStorage.removeItem(AUTH_TOKEN_KEY);
      showLogin("Your session expired or was revoked. Sign in again.");
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function optionalRequest(path, fallback) {
  try {
    return await request(path);
  } catch {
    return fallback;
  }
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

const BAD_STATE_PATTERN = /(_FAILED|_FAILURE|_ERROR|UNAVAILABLE|UNREACHABLE|NOT_CONNECTED|NOT_IMPLEMENTED|INSUFFICIENT|DEGRADED|UNSUPPORTED|NOT_PERMITTED|NO_PERMISSION)/;
const WARN_STATE_PATTERN = /(NOT_[A-Z_]+|UNKNOWN|UNVERIFIED|UNCONFIRMED|PENDING|SUPPRESSED|EXPIRED|STALE|PAUSED|DISABLED|IDLE|OFFLINE|WAITING|CHECKING|QUEUED|RETRYING|STOPPED|NOT_STARTED|LOW$|^LOW|ATTEMPTED|CONFIGURED_NOT|SIMULAT)/;
const POSITIVE_STATE_VALUES = new Set([
  "APPLICATION_UP", "DATABASE_OK", "MIGRATIONS_CURRENT", "MODEL_CONFIGURED", "CAMERA_AVAILABLE",
  "DISK_OK", "EMAIL_CONFIGURED", "WHATSAPP_CONFIGURED", "ALARM_READY_EVENT_DRIVEN", "READY",
  "ENABLED", "CONFIGURED", "SENT", "DELIVERED", "OPERATIONAL", "MEASURED", "RUNNING",
  "ACTIVATED", "MEASURED_FROM_OBSERVED_FRAMES",
]);

function stateTone(state) {
  const value = String(state ?? "").trim().toUpperCase();
  if (!value) return "unknown";
  if (BAD_STATE_PATTERN.test(value)) return "bad";
  if (POSITIVE_STATE_VALUES.has(value)) return "good";
  if (WARN_STATE_PATTERN.test(value)) return "warn";
  return "neutral";
}

function stateText(value, fallback = "NOT_AVAILABLE") {
  if (value === null || value === undefined || value === "" || value === false) return fallback;
  return String(value);
}

function stateBadge(value, fallback = "NOT_AVAILABLE") {
  const text = stateText(value, fallback);
  return `<span class="state-badge tone-${stateTone(text)}">${escapeHtml(text)}</span>`;
}

function badgeRow(label, value, fallback = "NOT_AVAILABLE") {
  return `<div class="state-row"><span>${escapeHtml(label)}</span><span class="state-value">${stateBadge(value, fallback)}</span></div>`;
}

function formatCount(value) {
  return Number.isFinite(Number(value)) && value !== null && value !== undefined ? Number(value).toLocaleString() : "NOT_AVAILABLE";
}

function formatFps(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)} fps` : "NOT_MEASURED";
}

function formatUptime(health) {
  const seconds = Number(health?.uptime_seconds);
  if (health?.uptime_human) return String(health.uptime_human);
  return Number.isFinite(seconds) ? `${Math.round(seconds)}s` : "NOT_AVAILABLE";
}

function formatTransportList(value) {
  if (Array.isArray(value) && value.length) return value.join(", ");
  return "NONE_CONFIGURED";
}

function detectorMap(detectorHealth) {
  const map = new Map();
  for (const item of detectorHealth?.detectors || []) map.set(item.detector_key, item);
  return map;
}

function detectorState(detectors, key) {
  return detectors.get(key)?.availability_state || "NOT_AVAILABLE";
}

function detectorImplementation(detectors, key) {
  return detectors.get(key)?.implementation_state || "NOT_AVAILABLE";
}

function unsupportedVerdict(value, detectorKey, detectors) {
  const reported = String(value ?? "").trim().toUpperCase();
  if (!reported || reported === "SAFE" || reported === "OK" || reported === "COMPLIANT" || reported === "NO_VIOLATION" || reported === "NONE" || reported === "CLEAR") {
    return { label: "UNKNOWN", note: `${detectorKey} is ${detectorImplementation(detectors, detectorKey)}; absence and compliance verdicts are not produced` };
  }
  if (reported.startsWith("NOT_") || reported === "MODEL_NOT_CONFIGURED") {
    return { label: reported, note: "Backend reported capability state" };
  }
  return { label: reported, note: "Verdict reported by the backend record" };
}

function metricBadge(label, value, badgeValue, note = "From backend records", fallback = "NOT_AVAILABLE") {
  return `<article class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-note">${stateBadge(badgeValue, fallback)}</div><div class="metric-note-text">${escapeHtml(note)}</div></article>`;
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
  const [summary, health, events, incidents, notifications, channels, tracks, detectorHealth, alarmPolicy, activeAlarms] = await Promise.all([
    request("/analytics/summary"),
    request("/system/health"),
    request("/events?limit=500&offset=0"),
    request("/incidents?limit=200&offset=0"),
    request("/notifications?limit=5&offset=0"),
    request("/notifications/channels/status"),
    request("/workers/tracks?recent_only=true&recent_within_seconds=30&limit=500"),
    request("/system/detectors"),
    request("/alarms/policy"),
    request("/alarms?active_only=true&limit=100"),
  ]);
  eventsCache = events;
  const detectors = detectorMap(detectorHealth);
  const latest = events.slice(0, 5);
  const activeAlarmRows = activeAlarms;
  const confirmedEventIds = new Set(events.filter((event) => event.observation_state === "CONFIRMED").map((event) => event.id));
  const confirmedIncidents = incidents.filter((incident) => incident.event_id && confirmedEventIds.has(incident.event_id));
  const activeIncidents = incidents.filter((incident) => !["CLOSED", "RESOLVED", "CANCELLED"].includes(incident.status));
  const lastIncident = incidents[0] || null;
  const lastNotification = notifications[0] || null;
  const confirmedByType = (types) => events.filter((event) => types.includes(event.event_type) && event.observation_state === "CONFIRMED").length;
  const notificationDelivery = (item) => item?.delivery_status || item?.last_delivery_status || item?.status || "NOT_AVAILABLE";
  content.innerHTML = `${statusBanner(health, summary)}
    <div class="metrics-grid">
      ${metric("Configured cameras", formatCount(summary.camera_count))}
      ${metric("Online cameras", formatCount(summary.online_camera_count))}
      ${metric("Active incidents", formatCount(activeIncidents.length), "Backend records · latest 200")}
      ${metric("Confirmed incidents", formatCount(confirmedIncidents.length), "Incidents linked to confirmed events · latest 500 events")}
      ${metricBadge("Active software alarms", formatCount(alarmPolicy.active_alarm_count), alarmPolicy.alarm_policy_enabled ? "ALARM_READY_EVENT_DRIVEN" : "ALARM_DISABLED_BY_POLICY", "Live alarm board · GET /alarms/policy")}
      ${metricBadge("System uptime", formatUptime(health), health.uptime_state || "UPTIME_UNAVAILABLE", "Measured process uptime reported by the backend")}
      ${metricBadge("Camera FPS", formatFps(health.camera_fps), health.measured_performance || "NOT_MEASURED", "Only measured from observed frames", "NOT_MEASURED")}
      ${metricBadge("Inference FPS", formatFps(health.inference_fps), health.measured_performance || "NOT_MEASURED", "Only measured from completed inferences", "NOT_MEASURED")}
      ${metricBadge("Disk", health.disk_free_percent === null || health.disk_free_percent === undefined ? "NOT_AVAILABLE" : `${health.disk_free_percent}% free`, health.disk_state || "DISK_UNAVAILABLE", "Free space on the evidence/database volume")}
      ${metricBadge("Email transport", health.email_transport || "EMAIL_NOT_CONFIGURED", health.email_transport || "EMAIL_NOT_CONFIGURED", "Configuration state only; delivery is reported per notification", "EMAIL_NOT_CONFIGURED")}
      ${metricBadge("WhatsApp transport", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", "Configuration state only; delivery is reported per notification", "WHATSAPP_NOT_CONFIGURED")}
      ${metricBadge("Physical alarm", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", `Configured transports: ${formatTransportList(health.physical_alarm_transports)}`, "PHYSICAL_ALARM_NOT_CONFIGURED")}
      ${metric("Recently observed tracks", formatCount(tracks.length), "Persisted tracks · last 30 seconds")}
      ${metricBadge("Helmet detection", detectorImplementation(detectors, "HELMET"), detectorState(detectors, "HELMET"), "Backend detector implementation and availability state")}
      ${metricBadge("Phone-use detection", detectorImplementation(detectors, "PHONE"), detectorState(detectors, "PHONE"), "Backend detector implementation and availability state")}
      ${metric("PPE event records", formatCount(confirmedByType(["PPE_NON_COMPLIANCE"])), "Confirmed records among latest 500 events")}
      ${metric("Fire / smoke events", formatCount(confirmedByType(["FIRE", "SMOKE"])), "Confirmed persisted events")}
      ${metric("Recorded events", formatCount(summary.event_count))}
    </div>
    <div class="metrics-grid alarm-count-grid">
      ${metric("Total alarms raised", formatCount(alarmPolicy.total_alarms), "Persisted alarm records · GET /alarms/policy")}
      ${metric("Active alarms", formatCount(alarmPolicy.active_alarm_count), "Live board · GET /alarms/policy")}
      ${metric("Escalated alarms", formatCount(alarmPolicy.escalated_alarm_count), "Escalation worker state · GET /alarms/policy")}
      ${metric("Acknowledged alarms", formatCount(alarmPolicy.acknowledged_alarm_count), "GET /alarms/policy")}
      ${metric("Suppressed alarms", formatCount(alarmPolicy.suppressed_alarm_count), "Cooldown and repeat limit · GET /alarms/policy")}
      ${metric("Expired alarms", formatCount(alarmPolicy.expired_alarm_count), "Auto-expire policy · GET /alarms/policy")}
      ${metric("Cleared alarms", formatCount(alarmPolicy.cleared_alarm_count), "Operator or policy cleared · GET /alarms/policy")}
    </div>
    <div class="content-grid">
      <section class="section"><div class="section-head"><h2>Recent recorded events</h2><span>DATABASE RECORDS</span></div>
        ${latest.length ? `<div class="table-wrap"><table><thead><tr><th>Type</th><th>State</th><th>Workflow</th><th>Started</th></tr></thead><tbody>${latest.map(eventRow).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No events recorded.</strong>Nothing is generated to fill this view.</div></div>`}
      </section>
      <section class="section"><div class="section-head"><h2>Latest incident and notification</h2><span>LAST RECORDED VALUE</span></div><div class="section-body"><div class="state-list">
        ${badgeRow("Last incident", lastIncident ? `${lastIncident.status} · ${lastIncident.id.slice(0, 8)}` : "NO_INCIDENT_RECORDED", "NO_INCIDENT_RECORDED")}
        ${badgeRow("Last incident time", lastIncident ? formatDate(lastIncident.created_at) : "NOT_AVAILABLE", "NOT_AVAILABLE")}
        ${badgeRow("Last notification channel", lastNotification ? lastNotification.channel : "NO_NOTIFICATION_RECORDED", "NO_NOTIFICATION_RECORDED")}
        ${badgeRow("Last notification time", lastNotification ? formatDate(lastNotification.sent_at || lastNotification.created_at) : "NOT_AVAILABLE", "NOT_AVAILABLE")}
        ${badgeRow("Last delivery status", lastNotification ? notificationDelivery(lastNotification) : "NOT_AVAILABLE", "NOT_AVAILABLE")}
      </div></div></section>
    </div>
    <div class="content-grid">
      <section class="section"><div class="section-head"><h2>Active alarm board</h2><span>GET /alarms?active_only=true</span></div>${activeAlarmRows.length ? `<div class="table-wrap"><table><thead><tr><th>Alarm</th><th>Severity</th><th>State</th><th>Raised</th><th>Physical</th></tr></thead><tbody>${activeAlarmRows.map((alarm) => `<tr><td class="mono">${escapeHtml(alarm.id.slice(0, 10))}</td><td>${escapeHtml(alarm.severity)}</td><td>${stateBadge(alarm.state)}</td><td>${escapeHtml(formatDate(alarm.raised_at))}</td><td>${stateBadge(alarm.physical_state, "NOT_ATTEMPTED")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>NO_ACTIVE_ALARMS</strong>The live board returned ${activeAlarmRows.length} active or escalated alarms.</div></div>`}</section>
      <section class="section"><div class="section-head"><h2>Subsystems</h2><span>REPORTED STATE</span></div><div class="section-body"><div class="state-list">
        ${badgeRow("Application process", health.application)}
        ${badgeRow("Uptime", health.uptime_state || "UPTIME_UNAVAILABLE")}
        ${badgeRow("Database", health.database)}
        ${badgeRow("Migrations", health.migrations)}
        ${badgeRow("Cameras", health.camera_state)}
        ${badgeRow("Camera health worker", health.camera_health_worker)}
        ${badgeRow("Safety / escalation worker", health.safety_event_worker || "UNKNOWN")}
        ${badgeRow("Notification delivery worker", health.notification_delivery_worker || "UNKNOWN")}
        ${badgeRow("Alarm subsystem", health.alarm_subsystem || "UNKNOWN")}
        ${badgeRow("Physical alarm state", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", "PHYSICAL_ALARM_NOT_CONFIGURED")}
        ${badgeRow("Physical alarm transports", formatTransportList(health.physical_alarm_transports), "NONE_CONFIGURED")}
        ${badgeRow("Inference", health.inference_pipeline)}
        ${badgeRow("Model", health.model_state)}
        ${badgeRow("Evidence", health.evidence_subsystem)}
        ${badgeRow("Notifications", health.notification_subsystem)}
        ${badgeRow("Disk", health.disk_state || "DISK_UNAVAILABLE")}
        ${badgeRow("Email transport", health.email_transport || "EMAIL_NOT_CONFIGURED", "EMAIL_NOT_CONFIGURED")}
        ${badgeRow("WhatsApp transport", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", "WHATSAPP_NOT_CONFIGURED")}
        ${badgeRow("Measured performance", health.measured_performance || "NOT_MEASURED", "NOT_MEASURED")}
        ${badgeRow("Frontend connectivity", health.frontend_connectivity)}
        ${badgeRow("Accuracy", summary.accuracy_metrics_status)}
      </div></div></section>
    </div>`;
  content.insertAdjacentHTML("beforeend", `<div class="content-grid operational-records">
    <section class="section"><div class="section-head"><h2>Recent incidents</h2><span>DATABASE RECORDS</span></div>${incidents.length ? `<div class="table-wrap"><table><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead><tbody>${incidents.slice(0, 25).map((incident) => `<tr><td>${escapeHtml(incident.title)}</td><td>${escapeHtml(incident.severity)}</td><td>${stateBadge(incident.status)}</td><td>${escapeHtml(formatDate(incident.created_at))}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No incident records.</strong>Incidents require an existing event and operator action.</div></div>`}</section>
    <section class="section"><div class="section-head"><h2>Notification state</h2><span>PROVIDER ACCEPTANCE IS NOT READ RECEIPT</span></div><div class="section-body"><div class="state-list">${channels.map((channel) => `<div class="state-row"><span>${escapeHtml(channel.channel)}</span><span class="state-value">${stateBadge(channel.configuration_state)}${channel.delivery_status || channel.last_delivery_status ? ` ${stateBadge(channel.delivery_status || channel.last_delivery_status)}` : ""} · QUEUED ${escapeHtml(formatCount(channel.queued))} · SENT ${escapeHtml(formatCount(channel.sent))} · FAILED ${escapeHtml(formatCount(channel.failed))}</span></div>`).join("")}</div></div>${notifications.length ? `<div class="table-wrap"><table><thead><tr><th>Channel</th><th>Delivery status</th><th>Recipient</th><th>Updated</th></tr></thead><tbody>${notifications.map((item) => `<tr><td>${escapeHtml(item.channel)}</td><td>${stateBadge(notificationDelivery(item))}</td><td>${escapeHtml(maskedRecipient(item.recipient_role || item.recipient))}</td><td>${escapeHtml(formatDate(item.sent_at || item.created_at))}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No notifications queued.</strong>Event notification policies create rows when a supported event is confirmed.</div></div>`}</section>
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
    ${renderBrowserWebcamPanel()}
    <div class="filter-bar camera-inventory-head"><p>Configured values and observed telemetry are shown separately.</p><span class="status-pill">${cameras.length} RECORDS</span></div>
    <section class="section"><div class="section-head"><h2>Camera inventory</h2><span>STREAM URL CREDENTIALS REDACTED</span></div>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>Camera</th><th>Connection</th><th>AI state</th><th>Frame rate</th><th>Observed resolution</th><th>Config</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No cameras configured.</strong>Register an authorized source through the camera API.</div></div>`}
    </section>`;

  document.querySelector("#webcam-discover").addEventListener("click", discoverLaptopWebcam);
  document.querySelector("#webcam-start").addEventListener("click", startLaptopWebcam);
  document.querySelector("#webcam-stop").addEventListener("click", stopLaptopWebcam);
  document.querySelector("#webcam-preview-image").addEventListener("error", handleWebcamPreviewError);
  document.querySelector("#browser-camera-start").addEventListener("click", startBrowserCamera);
  document.querySelector("#browser-camera-stop").addEventListener("click", stopBrowserCamera);
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
  stopWebcamPreview();
}

function stopWebcamPreview() {
  if (webcamPreviewTimer !== null) {
    window.clearTimeout(webcamPreviewTimer);
    webcamPreviewTimer = null;
  }
  webcamPreviewController?.abort();
  webcamPreviewController = null;
  const image = document.querySelector("#webcam-preview-image");
  if (image) {
    image.removeAttribute("src");
    image.hidden = true;
  }
  if (webcamPreviewObjectUrl) URL.revokeObjectURL(webcamPreviewObjectUrl);
  webcamPreviewObjectUrl = null;
}

async function refreshWebcamPreview() {
  if (currentView !== "cameras" || !document.querySelector("#webcam-preview-image") || !canViewBackendPreview() || !webcamIsOnline() || webcamBusy || webcamPreviewInFlight) return;
  webcamPreviewInFlight = true;
  const controller = new AbortController();
  webcamPreviewController = controller;
  try {
    const response = await fetch(`${apiBase}/cameras/${encodeURIComponent(webcamCamera.id)}/snapshot.jpg`, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `Authenticated camera snapshot failed (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch { /* Keep HTTP status. */ }
      throw new Error(detail);
    }
    const nextObjectUrl = URL.createObjectURL(await response.blob());
    const image = document.querySelector("#webcam-preview-image");
    const empty = document.querySelector("#webcam-preview-empty");
    if (!image || !webcamIsOnline()) {
      URL.revokeObjectURL(nextObjectUrl);
      return;
    }
    if (webcamPreviewObjectUrl) URL.revokeObjectURL(webcamPreviewObjectUrl);
    webcamPreviewObjectUrl = nextObjectUrl;
    image.src = nextObjectUrl;
    image.hidden = false;
    empty.hidden = true;
  } catch (error) {
    if (error.name !== "AbortError") handleWebcamPreviewError(error.message);
  } finally {
    if (webcamPreviewController === controller) webcamPreviewController = null;
    webcamPreviewInFlight = false;
    if (currentView === "cameras" && document.querySelector("#webcam-preview-image") && canViewBackendPreview() && webcamIsOnline()) webcamPreviewTimer = window.setTimeout(refreshWebcamPreview, 250);
  }
}

function canViewBackendPreview() {
  return userCan("cameras:view_live");
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
  const canManageSource = !currentIdentity || ["ADMIN", "SAFETY_OFFICER"].includes(currentIdentity.role?.name);
  document.querySelector("#webcam-start").disabled = !canManageSource || webcamBusy || online || starting || !detected;
  document.querySelector("#webcam-stop").disabled = !canManageSource || webcamBusy || !(online || starting);
  document.querySelector("#webcam-discover").disabled = !canManageSource || webcamBusy;

  const image = document.querySelector("#webcam-preview-image");
  const empty = document.querySelector("#webcam-preview-empty");
  if (online && image) {
    if (!canViewBackendPreview()) {
      stopWebcamPreview();
      empty.hidden = false;
      empty.textContent = "LIVE_PREVIEW_PERMISSION_REQUIRED";
    } else if (!webcamPreviewTimer && !webcamPreviewInFlight && !image.getAttribute("src")) refreshWebcamPreview();
  } else if (!online && image) {
    stopWebcamPreview();
    empty.hidden = false;
    empty.textContent = starting ? "Waiting for the first real webcam frame…" : "Live preview will appear after the webcam delivers a real frame.";
  }
}

function setWebcamError(message) {
  const error = document.querySelector("#webcam-error");
  if (error) error.textContent = message;
}

function handleWebcamPreviewError(message = "The real-frame preview stream could not be opened.") {
  const image = document.querySelector("#webcam-preview-image");
  const empty = document.querySelector("#webcam-preview-empty");
  if (image) image.hidden = true;
  if (empty) {
    empty.hidden = false;
    empty.textContent = message;
  }
  setWebcamError(`Authenticated snapshot preview unavailable: ${message}`);
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
  if (currentIdentity && !["ADMIN", "SAFETY_OFFICER"].includes(currentIdentity.role?.name)) return "VIEW ONLY";
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

function maskedRecipient(value) {
  if (!value) return "UNASSIGNED";
  const text = String(value);
  const at = text.indexOf("@");
  if (at > 0) return `${text[0]}***${text.slice(at)}`;
  return `${text.slice(0, 3)}***${text.slice(-2)}`;
}

async function renderWorkers() {
  if (workersRequestInFlight) return false;
  workersRequestInFlight = true;
  const controller = new AbortController();
  workersRequestController = controller;
  try {
    const [tracks, health, detectorHealth, events] = await Promise.all([
      request("/workers/tracks?recent_only=true&recent_within_seconds=30&limit=100", { signal: controller.signal }),
      request("/system/ai-health", { signal: controller.signal }),
      request("/system/detectors", { signal: controller.signal }),
      optionalRequest("/events?limit=200&offset=0", []),
    ]);
    if (currentView !== "workers") return true;
    const model = health.model || {};
    const detectors = detectorMap(detectorHealth);
    const incidentsByTrack = new Map();
    for (const event of events) {
      if (!event.track_uuid) continue;
      const linked = Array.isArray(event.incident_ids) && event.incident_ids.length ? event.incident_ids : null;
      if (!linked) continue;
      const existing = incidentsByTrack.get(event.track_uuid) || new Set();
      for (const incidentId of linked) existing.add(incidentId);
      incidentsByTrack.set(event.track_uuid, existing);
    }
    const cards = tracks.map((track) => {
      const active = track.active_events || [];
      const last = track.latest_detection;
      const trackIncidents = Array.from(incidentsByTrack.get(track.track_id) || []);
      const unsupported = WORKER_UNSUPPORTED_DETECTORS.map(([field, key]) => {
        const verdict = unsupportedVerdict(track[field], key, detectors);
        return `<div class="state-row"><span>${escapeHtml(key)}</span><span class="state-value">${stateBadge(verdict.label, "UNKNOWN")}</span></div><div class="state-note">${escapeHtml(verdict.note)}</div>`;
      }).join("");
      return `<article class="section worker-card"><div class="section-head"><h2>TRACK ${escapeHtml(track.track_id)}</h2>${stateBadge(track.freshness || "NOT_AVAILABLE")}</div><div class="section-body"><div class="state-list">
        ${badgeRow("Class", track.object_class)}
        ${badgeRow("Camera", track.camera_name)}
        ${badgeRow("Zone", track.zone_name || "NOT_SCOPED", "NOT_SCOPED")}
        ${badgeRow("Last seen", track.last_seen_at ? formatDate(track.last_seen_at) : "NOT_AVAILABLE")}
        ${badgeRow("First seen", track.first_seen_at ? formatDate(track.first_seen_at) : "NOT_AVAILABLE")}
        ${badgeRow("Latest detection", last?.object_class || "NO_DETECTION_RECORDED", "NO_DETECTION_RECORDED")}
        ${badgeRow("Latest confidence", last?.confidence ?? "NOT_RECORDED", "NOT_RECORDED")}
        ${badgeRow("Latest observation state", last?.observation_state || "NO_DETECTION_RECORDED", "NO_DETECTION_RECORDED")}
        ${badgeRow("Safety state", active.length ? "ACTIVE_EVENT_RECORDED" : "NO_ACTIVE_EVENT_RECORDED", "NO_ACTIVE_EVENT_RECORDED")}
        ${badgeRow("Active event records", active.map((event) => `${event.event_type} · ${event.severity} · ${event.workflow_state}`).join("; ") || "NONE_RECORDED", "NONE_RECORDED")}
        ${badgeRow("Linked incidents", trackIncidents.length ? trackIncidents.map((incidentId) => incidentId.slice(0, 12)).join(", ") : "NONE_RECORDED", "NONE_RECORDED")}
        ${unsupported}
      </div></div></article>`;
    }).join("");
    content.innerHTML = `<section class="section"><div class="section-head"><h2>Recently observed visual tracks</h2><span>${tracks.length} REAL DATABASE RECORDS · 30 SECOND WINDOW</span></div>${cards ? `<div class="worker-grid">${cards}</div>` : `<div class="empty-state"><div><strong>NO_LIVE_TRACK_DATA</strong>No persisted track records were observed in the last 30 seconds. This does not indicate that the area is safe.</div></div>`}</section>
      <section class="section"><div class="section-head"><h2>Detector capability behind these tracks</h2><span>BACKEND MODEL HEALTH</span></div><div class="section-body"><div class="state-list">
        ${badgeRow("Model", model.status || "MODEL_NOT_CONFIGURED")}
        ${badgeRow("Weights", model.weights_loaded ? "WEIGHTS_LOADED" : "MODEL_NOT_CONFIGURED")}
        ${badgeRow("Supported classes", (model.classes || []).join(", ") || "NOT_AVAILABLE")}
        ${WORKER_UNSUPPORTED_DETECTORS.map(([, key]) => badgeRow(`${key} detector`, `${detectorImplementation(detectors, key)} · ${detectorState(detectors, key)}`)).join("")}
      </div><p class="config-note">Helmet, PPE, and phone compliance are absence claims. No implemented detector in this build can establish them, so those fields are reported as UNKNOWN and never as a compliance or safety verdict.</p></div></section>`;
    return true;
  } catch (error) {
    if (error.name !== "AbortError" && currentView === "workers") content.innerHTML = `<section class="section"><div class="section-head"><h2>Worker monitoring</h2><span>DATA UNAVAILABLE</span></div><div class="empty-state"><div><strong>NO_LIVE_TRACK_DATA</strong>${escapeHtml(error.message)}</div></div></section>`;
    return false;
  } finally {
    if (workersRequestController === controller) workersRequestController = null;
    workersRequestInFlight = false;
  }
}

function startWorkersPolling(delay = 5000) {
  if (workersPollingTimer !== null || currentView !== "workers") return;
  workersPollingTimer = window.setTimeout(async () => {
    workersPollingTimer = null;
    if (currentView !== "workers") return;
    const healthy = await renderWorkers();
    startWorkersPolling(healthy ? 5000 : Math.min(delay * 2, 60000));
  }, delay);
}

function stopWorkersPolling() {
  if (workersPollingTimer !== null) window.clearTimeout(workersPollingTimer);
  workersPollingTimer = null;
  workersRequestController?.abort();
}

async function renderIncidents() {
  const incidents = await request("/incidents?limit=200&offset=0");
  const rows = incidents.map((item) => `<tr><td class="mono">${escapeHtml(item.id.slice(0, 12))}</td><td>${escapeHtml(item.title || "UNTITLED")}</td><td>${escapeHtml(item.severity || "UNKNOWN")}</td><td>${escapeHtml(item.status || "UNKNOWN")}</td><td class="mono">${escapeHtml(item.event_id || "UNLINKED")}</td><td>${escapeHtml(formatDate(item.created_at))}</td></tr>`).join("");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Persisted incidents</h2><span>BACKEND RECORDS · ${incidents.length}</span></div>${rows ? `<div class="table-wrap"><table><thead><tr><th>Incident</th><th>Title</th><th>Severity</th><th>Status</th><th>Event</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No incidents recorded.</strong>Incidents are created only against persisted events.</div></div>`}</section>`;
}

const ALARM_ACKNOWLEDGE_ROLES = ["ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "SUPERVISOR"];
const ALARM_ESCALATE_ROLES = ["ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER"];
const ALARM_CLEAR_ROLES = ["ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "SUPERVISOR"];
const ALARM_PHYSICAL_TEST_ROLES = ["ADMIN", "SAFETY_OFFICER"];
const PHYSICAL_UNAVAILABLE_STATES = new Set(["PHYSICAL_ALARM_NOT_CONFIGURED", "ACTUATOR_NOT_CONNECTED"]);

function alarmActionAllowed(roles) {
  if (!currentIdentity) return true;
  return roles.includes(currentIdentity.role?.name);
}

function physicalAlarmBanner(physical, health) {
  const state = physical?.state || health?.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED";
  const transports = formatTransportList(physical?.configured_transports ?? health?.physical_alarm_transports);
  const unavailable = PHYSICAL_UNAVAILABLE_STATES.has(state);
  const verified = physical?.hardware_verified === true;
  return `<div class="status-banner ${unavailable ? "physical-blocked" : ""}">
    <div><strong><span class="status-dot ${unavailable ? "dot-amber" : verified ? "dot-green" : "dot-muted"}"></span> PHYSICAL ACTUATOR · ${escapeHtml(state)}</strong>
      <span>Configured transports: ${escapeHtml(transports)}. ${escapeHtml(physical?.reason || "No reason reported by the backend.")} No siren, relay, or industrial controller output is claimed by this page; only the backend actuator state is reported.</span></div>
    <span class="status-pill">${stateBadge(physical?.enabled === true ? "ACTUATION_ENABLED" : "ACTUATION_DISABLED")} ${stateBadge(verified ? "HARDWARE_VERIFIED" : "HARDWARE_NOT_VERIFIED")}</span>
  </div>`;
}

function alarmPolicyPanel(policy) {
  return `<section class="section"><div class="section-head"><h2>Alarm policy</h2><span>GET /alarms/policy</span></div><div class="section-body"><div class="state-list">
    ${badgeRow("Policy enabled", policy.alarm_policy_enabled ? "ALARM_POLICY_ENABLED" : "ALARM_POLICY_DISABLED")}
    ${badgeRow("Minimum severity", policy.min_severity || "NOT_AVAILABLE")}
    ${badgeRow("Cooldown seconds", formatCount(policy.cooldown_seconds))}
    ${badgeRow("Max repeats per window", formatCount(policy.max_repeats_per_window))}
    ${badgeRow("Repeat window seconds", formatCount(policy.repeat_window_seconds))}
    ${badgeRow("Auto expire seconds", formatCount(policy.auto_expire_seconds))}
    ${badgeRow("Audible browser alarm", policy.audible_browser_alarm ? "AUDIBLE_BROWSER_ALARM_ENABLED" : "AUDIBLE_BROWSER_ALARM_DISABLED")}
    ${badgeRow("Total alarms", formatCount(policy.total_alarms))}
    ${badgeRow("Active alarms", formatCount(policy.active_alarm_count))}
    ${badgeRow("Escalated alarms", formatCount(policy.escalated_alarm_count))}
    ${badgeRow("Acknowledged alarms", formatCount(policy.acknowledged_alarm_count))}
    ${badgeRow("Suppressed alarms", formatCount(policy.suppressed_alarm_count))}
    ${badgeRow("Expired alarms", formatCount(policy.expired_alarm_count))}
    ${badgeRow("Cleared alarms", formatCount(policy.cleared_alarm_count))}
    ${badgeRow("Last alarm", policy.last_alarm_id ? `${policy.last_alarm_id.slice(0, 10)} · ${formatDate(policy.last_alarm_raised_at)}` : "NO_ALARM_RECORDED", "NO_ALARM_RECORDED")}
    ${badgeRow("Physical actuator", policy.physical_alarm?.state || "PHYSICAL_ALARM_NOT_CONFIGURED", "PHYSICAL_ALARM_NOT_CONFIGURED")}
    ${badgeRow("Physical transports", formatTransportList(policy.physical_alarm?.configured_transports), "NONE_CONFIGURED")}
  </div></div></section>`;
}

function alarmActionBar(alarm) {
  const transitions = Array.isArray(alarm.allowed_transitions) ? alarm.allowed_transitions : [];
  const ackAllowed = alarmActionAllowed(ALARM_ACKNOWLEDGE_ROLES) && transitions.includes("ACKNOWLEDGED");
  const escalateAllowed = alarmActionAllowed(ALARM_ESCALATE_ROLES) && transitions.includes("ESCALATED");
  const clearAllowed = alarmActionAllowed(ALARM_CLEAR_ROLES) && transitions.includes("CLEARED");
  return `<div class="alarm-actions">
    ${ackAllowed ? `<button class="inline-button" data-alarm-action="acknowledge" data-alarm-id="${escapeHtml(alarm.id)}" type="button">Acknowledge</button>` : `<span class="status-pill">${escapeHtml(alarmActionAllowed(ALARM_ACKNOWLEDGE_ROLES) ? "ACKNOWLEDGE_NOT_ALLOWED_FROM_STATE" : "ROLE_NOT_PERMITTED_TO_ACKNOWLEDGE")}</span>`}
    ${escalateAllowed ? `<button class="inline-button" data-alarm-action="escalate" data-alarm-id="${escapeHtml(alarm.id)}" type="button">Escalate</button>` : `<span class="status-pill">${escapeHtml(alarmActionAllowed(ALARM_ESCALATE_ROLES) ? "ESCALATE_NOT_ALLOWED_FROM_STATE" : "ROLE_NOT_PERMITTED_TO_ESCALATE")}</span>`}
    ${clearAllowed ? `<button class="inline-button" data-alarm-action="clear" data-alarm-id="${escapeHtml(alarm.id)}" type="button">Clear</button>` : `<span class="status-pill">${escapeHtml(alarmActionAllowed(ALARM_CLEAR_ROLES) ? "CLEAR_NOT_ALLOWED_FROM_STATE" : "ROLE_NOT_PERMITTED_TO_CLEAR")}</span>`}
    <button class="inline-button" data-alarm-action="history" data-alarm-id="${escapeHtml(alarm.id)}" type="button">Transition history</button>
  </div><div class="alarm-history" id="alarm-history-${escapeHtml(alarm.id)}" hidden></div>`;
}

function alarmCard(alarm) {
  return `<article class="section alarm-card"><div class="section-head"><h2>ALARM ${escapeHtml(alarm.id.slice(0, 12))}</h2>${stateBadge(alarm.severity)}</div><div class="section-body"><div class="state-list">
    ${badgeRow("State", alarm.state)}
    ${badgeRow("Event", alarm.event_id ? alarm.event_id.slice(0, 12) : "NOT_RECORDED", "NOT_RECORDED")}
    ${badgeRow("Detector", alarm.detector_key || "NOT_RECORDED", "NOT_RECORDED")}
    ${badgeRow("Model", alarm.model_name ? `${alarm.model_name} ${alarm.model_version || ""}`.trim() : "MODEL_NOT_CONFIGURED", "MODEL_NOT_CONFIGURED")}
    ${badgeRow("Confidence", alarm.confidence === null || alarm.confidence === undefined ? "NOT_RECORDED" : String(alarm.confidence), "NOT_RECORDED")}
    ${badgeRow("Camera / zone / track", [alarm.camera_id || "NOT_RECORDED", alarm.zone_id || "NOT_SCOPED", alarm.track_id || "NOT_RECORDED"].join(" · "))}
    ${badgeRow("Linked incident", alarm.incident_id || "NONE_RECORDED", "NONE_RECORDED")}
    ${badgeRow("Raised at", alarm.raised_at ? formatDate(alarm.raised_at) : "NOT_AVAILABLE")}
    ${badgeRow("Raised count", formatCount(alarm.raised_count))}
    ${badgeRow("Suppression count", formatCount(alarm.suppression_count))}
    ${badgeRow("Cooldown until", alarm.cooldown_until ? formatDate(alarm.cooldown_until) : "NO_COOLDOWN_RECORDED", "NO_COOLDOWN_RECORDED")}
    ${badgeRow("Expires at", alarm.expires_at ? formatDate(alarm.expires_at) : "NO_AUTO_EXPIRY_RECORDED", "NO_AUTO_EXPIRY_RECORDED")}
    ${badgeRow("Acknowledged", alarm.acknowledged_at ? `${formatDate(alarm.acknowledged_at)} · ${alarm.acknowledged_by_user_id || "UNATTRIBUTED"}` : "NOT_ACKNOWLEDGED", "NOT_ACKNOWLEDGED")}
    ${badgeRow("Cleared at", alarm.cleared_at ? formatDate(alarm.cleared_at) : "NOT_CLEARED", "NOT_CLEARED")}
    ${badgeRow("Reason", alarm.reason || "NO_REASON_RECORDED", "NO_REASON_RECORDED")}
    ${badgeRow("Physical state", alarm.physical_state || "NOT_ATTEMPTED", "NOT_ATTEMPTED")}
    ${badgeRow("Physical result", alarm.physical_result || "NO_PHYSICAL_RESULT_RECORDED", "NO_PHYSICAL_RESULT_RECORDED")}
    ${badgeRow("Physical activated", alarm.physical_activated_at ? formatDate(alarm.physical_activated_at) : "NEVER_ACTIVATED", "NEVER_ACTIVATED")}
    ${badgeRow("Physical cleared", alarm.physical_cleared_at ? formatDate(alarm.physical_cleared_at) : "NEVER_DEACTIVATED", "NEVER_DEACTIVATED")}
  </div>${alarmActionBar(alarm)}</div></article>`;
}

function alarmHistoryRow(alarm) {
  return `<tr>
    <td class="mono">${escapeHtml(alarm.id.slice(0, 10))}</td>
    <td>${escapeHtml(alarm.severity)}</td>
    <td>${stateBadge(alarm.state)}</td>
    <td>${escapeHtml(alarm.detector_key || "NOT_RECORDED")}</td>
    <td>${escapeHtml(formatDate(alarm.raised_at))}</td>
    <td>${escapeHtml(formatCount(alarm.raised_count))} / ${escapeHtml(formatCount(alarm.suppression_count))}</td>
    <td>${alarm.cooldown_until ? escapeHtml(formatDate(alarm.cooldown_until)) : "NO_COOLDOWN_RECORDED"}</td>
    <td>${stateBadge(alarm.physical_state, "NOT_ATTEMPTED")}</td>
    <td>${escapeHtml(alarm.reason || "NO_REASON_RECORDED")}</td>
    <td><button class="inline-button" data-alarm-action="history" data-alarm-id="${escapeHtml(alarm.id)}" type="button">History</button></td>
  </tr>`;
}

async function renderAlarmCenter() {
  const [board, history, policy, physical, health] = await Promise.all([
    request("/alarms?active_only=true&limit=100"),
    request("/alarms?limit=200&offset=0"),
    request("/alarms/policy"),
    request("/alarms/physical/status"),
    request("/system/health"),
  ]);
  content.innerHTML = `${physicalAlarmBanner(physical, health)}
    <div class="status-banner"><div><strong><span class="status-dot ${board.length ? "dot-red" : "dot-muted"}"></span> ALARM SUBSYSTEM · ${escapeHtml(policy.alarm_policy_enabled ? "ALARM_READY_EVENT_DRIVEN" : "ALARM_DISABLED_BY_POLICY")}</strong><span>The live board returned ${board.length} active or escalated alarm${board.length === 1 ? "" : "s"}. Alarms exist only where the backend persisted them from a confirmed event. Browser audio is an operator-controlled sound, not a physical output.</span></div><span class="status-pill">${stateBadge(health.validation_status || "NOT_VALIDATED")}</span></div>
    <section class="section"><div class="section-head"><h2>Live board</h2><span>GET /alarms?active_only=true</span></div>${board.length ? `<div class="alarm-grid">${board.map(alarmCard).join("")}</div>` : `<div class="empty-state"><div><strong>NO_ACTIVE_ALARMS</strong>The API returned no ACTIVE or ESCALATED alarm. Nothing is synthesised to fill the board.</div></div>`}</section>
    <div class="content-grid alarm-lower-grid">
      <section class="section"><div class="section-head"><h2>Alarm history</h2><span>${history.length} PERSISTED ALARM RECORDS</span></div>${history.length ? `<div class="table-wrap"><table><thead><tr><th>Alarm</th><th>Severity</th><th>State</th><th>Detector</th><th>Raised</th><th>Raised / suppressed</th><th>Cooldown until</th><th>Physical</th><th>Reason</th><th>Transitions</th></tr></thead><tbody>${history.map(alarmHistoryRow).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>NO_ALARM_HISTORY</strong>No alarm has been persisted by the alarm engine.</div></div>`}</section>
      <div class="alarm-side-stack">
        ${alarmPolicyPanel(policy)}
        <section class="section"><div class="section-head"><h2>Physical alarm test</h2><span>REAL ACTUATION ATTEMPT</span></div><div class="section-body">
          <p class="config-note">This is not a simulation. With no transport configured the API answers PHYSICAL_ALARM_NOT_CONFIGURED; with a transport but no hardware it answers ACTUATOR_NOT_CONNECTED.</p>
          <div class="alarm-actions">${alarmActionAllowed(ALARM_PHYSICAL_TEST_ROLES) ? `<button class="inline-button" data-alarm-action="physical-test" type="button">Attempt physical alarm test</button>` : `<span class="status-pill">ROLE_NOT_PERMITTED_PHYSICAL_TEST</span>`}</div>
          <p id="physical-test-result" class="test-result" role="status">No physical test has been attempted from this page.</p>
        </div></section>
      </div>
    </div>
    <div class="alarm-history-host" id="alarm-history-host"></div>`;
  content.querySelectorAll("[data-alarm-action]").forEach((button) => button.addEventListener("click", () => handleAlarmAction(button.dataset.alarmAction, button.dataset.alarmId)));
}

async function handleAlarmAction(action, alarmId) {
  if (action === "physical-test") {
    try {
      const result = await request("/alarms/physical/test", { method: "POST" });
      const output = document.querySelector("#physical-test-result");
      if (output) output.textContent = `PHYSICAL TEST RESULT · state ${result.state} · transport ${result.transport} · reason ${result.reason || "NO_REASON_REPORTED"} · hardware_verified ${result.hardware_verified ? "TRUE" : "FALSE"}`;
    } catch (error) { showMessage(error.message); }
    return;
  }
  if (action === "history") {
    await showAlarmTransitionHistory(alarmId);
    return;
  }
  try {
    if (action === "acknowledge") {
      await request(`/alarms/${encodeURIComponent(alarmId)}/acknowledge`, { method: "POST", body: JSON.stringify({ notes: "Acknowledged from the alarm centre" }) });
      showMessage(`Alarm ${alarmId.slice(0, 10)} acknowledged.`);
    } else if (action === "escalate") {
      const reason = window.prompt("Escalation reason (stored on the alarm):");
      if (!reason || !reason.trim()) return;
      await request(`/alarms/${encodeURIComponent(alarmId)}/escalate?reason=${encodeURIComponent(reason.trim())}`, { method: "POST" });
      showMessage(`Alarm ${alarmId.slice(0, 10)} escalated.`);
    } else if (action === "clear") {
      const reason = window.prompt("Clear reason (stored on the alarm):");
      if (!reason || !reason.trim()) return;
      await request(`/alarms/${encodeURIComponent(alarmId)}/clear`, { method: "POST", body: JSON.stringify({ reason: reason.trim(), deactivate_physical: true }) });
      showMessage(`Alarm ${alarmId.slice(0, 10)} cleared.`);
    }
    await refreshAlarmState();
    if (currentView === "alarm") await renderAlarmCenter();
  } catch (error) {
    showMessage(error.message);
  }
}

async function showAlarmTransitionHistory(alarmId) {
  let target = document.querySelector(`#alarm-history-${CSS.escape(alarmId)}`);
  if (!target) {
    target = document.createElement("div");
    target.className = "detail-panel alarm-history";
    target.id = `alarm-history-${alarmId}`;
    document.querySelector("#alarm-history-host")?.append(target);
  }
  if (!target.isConnected) return;
  if (!target.hidden && target.dataset.loaded === "true") { target.hidden = true; return; }
  target.hidden = false;
  target.innerHTML = `<div class="empty-state"><div><strong>LOADING_TRANSITION_HISTORY</strong>Reading the audited alarm history.</div></div>`;
  try {
    const rows = await request(`/alarms/${encodeURIComponent(alarmId)}/history`);
    target.innerHTML = rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>From</th><th>To</th><th>Reason</th><th>Actor</th><th>Time</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${stateBadge(row.previous_state || "INITIAL_STATE")}</td><td>${stateBadge(row.new_state)}</td><td>${escapeHtml(row.reason || "NO_REASON_RECORDED")}</td><td>${escapeHtml(row.user_id || "UNATTRIBUTED")}</td><td>${escapeHtml(formatDate(row.transitioned_at))}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty-state"><div><strong>NO_TRANSITIONS_RECORDED</strong>The backend returned no audited state transitions for this alarm.</div></div>`;
    target.dataset.loaded = "true";
  } catch (error) {
    target.innerHTML = `<div class="empty-state"><div><strong>NOT_AVAILABLE</strong>${escapeHtml(error.message)}</div></div>`;
  }
}

function detectorCapabilityRow(detector) {
  const configuration = detector.configuration || {};
  const health = detector.health || {};
  return `<tr>
    <td><strong>${escapeHtml(detector.detector_key)}</strong></td>
    <td>${stateBadge(detector.implementation_state)}</td>
    <td>${stateBadge(detector.availability_state)}</td>
    <td>${detector.operational ? stateBadge("OPERATIONAL") : stateBadge("NOT_OPERATIONAL")}</td>
    <td>${detector.enabled ? stateBadge("ENABLED") : stateBadge("DISABLED")}</td>
    <td>${detector.configured ? stateBadge("CONFIGURED") : stateBadge("NOT_CONFIGURED")}</td>
    <td>${stateBadge(detector.detection_capability || "NONE", "NONE")}</td>
    <td>${stateBadge(detector.validation_state || "NOT_CONFIGURED", "NOT_CONFIGURED")}</td>
    <td>${stateBadge(health.model_status || "MODEL_NOT_CONFIGURED", "MODEL_NOT_CONFIGURED")}</td>
    <td>${escapeHtml(configuration.threshold_source || "NOT_CONFIGURED")}</td>
    <td>${escapeHtml(configuration.scope_camera_id || configuration.scope_zone_id || "SITE")}</td>
    <td>${escapeHtml(detector.reason || "NO_REASON_REPORTED")}</td>
  </tr>`;
}

async function renderRules() {
  const [ppe, detectorConfigs, rules, thresholds, escalation, policies, capability, detectorHealth] = await Promise.all([
    request("/configuration/ppe-rules"), request("/configuration/detector-configs"), request("/configuration/safety-rules"),
    request("/configuration/operating-thresholds"), request("/configuration/escalation-policies"), request("/configuration/notification-policies"),
    request("/system/ai-health"), request("/system/detectors"),
  ]);
  const model = capability.model || {};
  const detectors = detectorHealth.detectors || [];
  const detectorsByKey = detectorMap(detectorHealth);
  const rows = [...ppe.map((x) => ({ type: `PPE · ${x.ppe_type}`, enabled: x.is_mandatory ? "MANDATORY" : "CONFIGURED", scope: x.zone_id, capability: model.weights_loaded ? "MODEL AVAILABLE · RULE NOT VALIDATED" : "MODEL_NOT_CONFIGURED" })), ...detectorConfigs.map((x) => ({ type: x.detector_key, enabled: x.is_enabled ? "ENABLED" : "DISABLED", scope: x.camera_id || x.zone_id || "GLOBAL", capability: model.weights_loaded ? x.validation_status : "MODEL_NOT_CONFIGURED" })), ...rules.map((x) => ({ type: `${x.code} · ${x.name}`, enabled: x.is_active ? "ENABLED" : "DISABLED", scope: x.zone_id || "GLOBAL", capability: x.validation_status }))];
  const body = rows.map((x) => `<tr><td>${escapeHtml(x.type)}</td><td>${stateBadge(x.enabled)}</td><td>${escapeHtml(x.scope || "NOT_SCOPED")}</td><td>${stateBadge(x.capability || "NOT_CONFIGURED", "NOT_CONFIGURED")}</td></tr>`).join("");
  const detectorBody = detectors.map(detectorCapabilityRow).join("");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Detector capability and configuration</h2><span>GET /system/detectors · NO MODEL OUTPUT IS IMPLIED</span></div>${detectorBody ? `<div class="table-wrap"><table><thead><tr><th>Detector</th><th>Implementation</th><th>Availability</th><th>Operational</th><th>Enabled</th><th>Configured</th><th>Detection capability</th><th>Validation</th><th>Model health</th><th>Threshold source</th><th>Scope</th><th>Reason</th></tr></thead><tbody>${detectorBody}</tbody></table></div>` : `<div class="empty-state"><div><strong>NO_DETECTOR_STATE</strong>The detector capability endpoint returned no rows.</div></div>`}<div class="section-body"><div class="state-list">
    ${badgeRow("Model status", detectorHealth.model_status || "MODEL_NOT_CONFIGURED")}
    ${badgeRow("Model classes available", formatCount(detectorHealth.model_classes_available))}
    ${badgeRow("Operational detectors", formatCount(detectorHealth.operational_detector_count))}
    ${badgeRow("IGL validation", detectorHealth.igl_validation_status || "NOT_VALIDATED")}
    ${badgeRow("Helmet detector", `${detectorImplementation(detectorsByKey, "HELMET")} · ${detectorState(detectorsByKey, "HELMET")}`)}
    ${badgeRow("PPE detector", `${detectorImplementation(detectorsByKey, "PPE")} · ${detectorState(detectorsByKey, "PPE")}`)}
    ${badgeRow("Safety vest detector", `${detectorImplementation(detectorsByKey, "SAFETY_VEST")} · ${detectorState(detectorsByKey, "SAFETY_VEST")}`)}
    ${badgeRow("Proximity detector", `${detectorImplementation(detectorsByKey, "PROXIMITY")} · ${detectorState(detectorsByKey, "PROXIMITY")}`)}
    ${badgeRow("Fall detector", `${detectorImplementation(detectorsByKey, "FALL")} · ${detectorState(detectorsByKey, "FALL")}`)}
    ${badgeRow("Leakage detector", `${detectorImplementation(detectorsByKey, "LEAKAGE")} · ${detectorState(detectorsByKey, "LEAKAGE")}`)}
    ${badgeRow("Unsafe behaviour detector", `${detectorImplementation(detectorsByKey, "UNSAFE_BEHAVIOR")} · ${detectorState(detectorsByKey, "UNSAFE_BEHAVIOR")}`)}
    ${badgeRow("Person detector", `${detectorImplementation(detectorsByKey, "PERSON")} · ${detectorState(detectorsByKey, "PERSON")}`)}
    ${badgeRow("Phone detector", `${detectorImplementation(detectorsByKey, "PHONE")} · ${detectorState(detectorsByKey, "PHONE")}`)}
  </div><p class="config-note">Implementation state and availability state are reported separately: NOT_IMPLEMENTED means this build cannot evaluate the detector at all, while MODEL_NOT_CONFIGURED means the code exists but no model is loaded. Neither state is ever presented as a passing compliance check.</p></div></section>
    <section class="section"><div class="section-head"><h2>Configured rules</h2><span>OPERATOR-SUPPLIED CONFIGURATION ONLY</span></div>${body ? `<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Configuration</th><th>Scope</th><th>Capability / validation</th></tr></thead><tbody>${body}</tbody></table></div>` : `<div class="empty-state"><div><strong>No rules configured.</strong>Rules appear when returned by configuration APIs.</div></div>`}</section>
    <div class="metrics-grid">
      ${metric("Operating thresholds", formatCount(thresholds.length))}
      ${metric("Escalation policies", formatCount(escalation.length))}
      ${metric("Notification policies", formatCount(policies.length))}
      ${metricBadge("Phone detection", detectorImplementation(detectorsByKey, "PHONE"), detectorState(detectorsByKey, "PHONE"), "Backend detector state")}
    </div>`;
}

function deliveryStatusOf(record) {
  return record?.delivery_status || record?.last_delivery_status || record?.status || "DELIVERY_STATUS_NOT_REPORTED";
}

function deliveryTestSummary(label, result) {
  const parts = [`${label} · delivery_status ${deliveryStatusOf(result)}`, `provider ${result?.provider || "NOT_REPORTED"}`];
  if (result?.message_id) parts.push(`message_id ${result.message_id}`);
  if (result?.error) parts.push(`error ${result.error}`);
  return parts.join(" · ");
}

async function renderNotifications() {
  const [items, channels] = await Promise.all([request("/notifications?limit=200&offset=0"), request("/notifications/channels/status")]);
  const rows = items.map((x) => `<tr><td class="mono">${escapeHtml((x.event_id || x.id).slice(0, 12))}</td><td>${escapeHtml(x.channel)}</td><td>${escapeHtml(maskedRecipient(x.recipient || x.recipient_role))}</td><td>${stateBadge(x.status)}</td><td>${stateBadge(deliveryStatusOf(x))}</td><td>${escapeHtml(x.retry_count)} / ${escapeHtml(x.max_attempts)}</td><td>${escapeHtml(formatDate(x.last_attempt_at || x.created_at))}</td><td>${escapeHtml(x.error_message || x.provider || "NOT_REPORTED")}</td></tr>`).join("");
  const channelRows = channels.map((channel) => `<tr><td>${escapeHtml(channel.channel)}</td><td>${stateBadge(channel.configuration_state)}</td><td>${stateBadge(channel.delivery_status || channel.last_delivery_status, "DELIVERY_STATUS_NOT_REPORTED")}</td><td>${escapeHtml(formatCount(channel.queued))}</td><td>${escapeHtml(formatCount(channel.sent))}</td><td>${escapeHtml(formatCount(channel.failed))}</td><td>${escapeHtml(formatCount(channel.not_configured))}</td><td>${escapeHtml(formatCount(channel.retrying))}</td></tr>`).join("");
  const canTest = ["ADMIN", "SAFETY_OFFICER"].includes(currentIdentity?.role?.name);
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Provider configuration and delivery state</h2><span>SECRETS REMAIN ON BACKEND</span></div>${channelRows ? `<div class="table-wrap"><table><thead><tr><th>Channel</th><th>Configuration state</th><th>Delivery status</th><th>Queued</th><th>Sent</th><th>Failed</th><th>Not configured</th><th>Retrying</th></tr></thead><tbody>${channelRows}</tbody></table></div>` : `<div class="empty-state"><div><strong>NO_CHANNEL_STATUS</strong>The channel status endpoint returned no rows.</div></div>`}<div class="section-body"><p class="config-note">A queued or sent notification is a backend record. Configuration state never implies that a message was delivered, and a provider acceptance is not proof that a person read it.</p></div></section>
    ${canTest ? `<section class="section"><div class="section-head"><h2>Explicit provider test</h2><span>REAL DELIVERY ATTEMPT WHEN SUBMITTED</span></div><div class="section-body"><div class="notification-test-grid">
      <form id="email-test-form"><label>Test email recipient<input name="recipient" type="email" required autocomplete="email"></label><button class="inline-button" type="submit">TEST EMAIL</button></form>
      <form id="whatsapp-test-form"><label>WhatsApp recipient in E.164 format<input name="recipient" type="tel" required placeholder="+15551234567" autocomplete="tel"></label><button class="inline-button" type="submit">TEST WHATSAPP</button></form>
    </div><p id="email-test-result" class="test-result" role="status">No email test has been sent from this page.</p><p id="whatsapp-test-result" class="test-result" role="status">No WhatsApp test has been sent from this page.</p></div></section>` : `<section class="section"><div class="section-head"><h2>Explicit provider test</h2><span>ROLE REQUIRED</span></div><div class="section-body"><p class="config-note">Sending a provider test requires the ADMIN or SAFETY_OFFICER role. No test is attempted for this account.</p></div></section>`}
    <section class="section"><div class="section-head"><h2>Delivery records</h2><span>PROVIDER ACCEPTANCE DOES NOT CONFIRM HUMAN RECEIPT</span></div>${rows ? `<div class="table-wrap"><table><thead><tr><th>Event</th><th>Channel</th><th>Recipient</th><th>State</th><th>Delivery status</th><th>Retries</th><th>Last attempt</th><th>Provider / error</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No delivery records.</strong>No notification rows were returned.</div></div>`}</section>`;
  content.querySelectorAll("#email-test-form, #whatsapp-test-form").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const recipient = new FormData(form).get("recipient");
    const channel = form.id === "email-test-form" ? "email" : "whatsapp";
    const output = document.querySelector(`#${channel}-test-result`);
    if (output) output.textContent = `REQUEST_SENT_TO_BACKEND · ${channel.toUpperCase()}`;
    try {
      const result = await request(`/notifications/test/${channel}`, { method: "POST", body: JSON.stringify({ recipient }) });
      if (output) output.textContent = deliveryTestSummary(`${channel.toUpperCase()} TEST RESULT`, result);
    } catch (error) {
      if (output) output.textContent = `${channel.toUpperCase()} TEST FAILED · ${error.message}`;
    }
  }));
}

function renderAlarmRegion(alarms, error = null) {
  const region = document.querySelector("#alarm-region");
  if (!region) return;
  if (error) {
    region.innerHTML = `<div class="alarm-banner alarm-unavailable"><div><span class="alarm-label">ALARM STATE</span><strong>UNAVAILABLE</strong></div><span>${escapeHtml(error)}</span></div>`;
    stopAlarmSound();
    return;
  }
  activeAlarmsState = Array.isArray(alarms) ? alarms : [];
  const soundToggle = `<button class="alarm-sound-button" id="alarm-sound-toggle" type="button" aria-pressed="${alarmSoundEnabled}">${alarmSoundEnabled ? "Mute browser alarm" : "Enable browser alarm"}</button>`;
  if (!activeAlarmsState.length) {
    region.innerHTML = `<div class="alarm-banner alarm-clear"><div class="alarm-copy"><span class="alarm-label">ALARM STATE</span><strong>NO ACTIVE PERSISTED ALARMS</strong><span>The alarm API returned no ACTIVE or ESCALATED alarm. Camera health records and unconfirmed observations are not safety alarms.</span></div>${soundToggle}</div>`;
    region.querySelector("#alarm-sound-toggle").addEventListener("click", toggleAlarmSound);
    synchronizeAlarmSound();
    return;
  }
  const canAcknowledge = userCan("events:acknowledge") && alarmActionAllowed(ALARM_ACKNOWLEDGE_ROLES);
  region.innerHTML = `<div class="alarm-banner alarm-active" role="alert">
    <div class="alarm-summary"><span class="alarm-label">ACTIVE SAFETY ALARM</span><strong>${activeAlarmsState.length} PERSISTED ALARM${activeAlarmsState.length === 1 ? "" : "S"} ON THE LIVE BOARD</strong></div>
    <div class="alarm-event-list">${activeAlarmsState.map((alarm) => `<div class="alarm-event-row"><span><strong>${escapeHtml(alarm.severity)} · ${escapeHtml(alarm.state)}</strong><span>${escapeHtml(alarm.id.slice(0, 10))} · raised ${escapeHtml(formatDate(alarm.raised_at))} · physical ${escapeHtml(alarm.physical_state || "NOT_ATTEMPTED")}</span></span>${canAcknowledge ? `<button class="alarm-ack-button" data-alarm-ack="${escapeHtml(alarm.id)}" type="button">Acknowledge</button>` : `<span class="status-pill">ACKNOWLEDGEMENT PERMISSION REQUIRED</span>`}</div>`).join("")}</div>
    <p class="browser-camera-note">Browser audio only. No siren, relay, or GPIO output is claimed here; the physical actuator state is reported separately by the backend.</p>
    ${soundToggle}
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
    const alarms = await request("/alarms?active_only=true&limit=100");
    renderAlarmRegion(alarms);
  } catch (error) {
    activeAlarmsState = [];
    stopAlarmSound();
    renderAlarmRegion([], error.message);
  } finally {
    alarmRequestInFlight = false;
  }
}

function startAlarmPolling() {
  if (alarmPollingTimer !== null) return;
  refreshAlarmState();
  alarmPollingTimer = window.setInterval(refreshAlarmState, 5000);
}

async function acknowledgeAlarm(alarmId) {
  const button = document.querySelector(`[data-alarm-ack="${CSS.escape(alarmId)}"]`);
  if (button) button.disabled = true;
  try {
    await request(`/alarms/${encodeURIComponent(alarmId)}/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ notes: "Acknowledged from the local dashboard alarm" }),
    });
    showMessage(`Alarm ${alarmId.slice(0, 10)} acknowledged.`);
    await refreshAlarmState();
    if (currentView === "alarm") await renderAlarmCenter();
  } catch (error) {
    showMessage(error.message);
    if (button) button.disabled = false;
  }
}

async function toggleAlarmSound() {
  if (alarmSoundEnabled) {
    alarmSoundEnabled = false;
    stopAlarmSound();
    renderAlarmRegion(activeAlarmsState);
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
    renderAlarmRegion(activeAlarmsState);
    synchronizeAlarmSound();
  } catch {
    showMessage("Browser audio could not be enabled; visible alarm state remains active.");
  }
}

function synchronizeAlarmSound() {
  if (!alarmSoundEnabled || !activeAlarmsState.length) {
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
        <label>Name<input name="name" required maxlength="150"></label><label>Event type<input name="event_type" maxlength="50"></label><label>Severity<input name="severity" maxlength="20"></label><label>Camera<select name="camera_id">${scopedOptions(cameras, (item) => `${item.name} (${item.code})`)}</select></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Delay seconds<input name="escalate_after_seconds" type="number" min="1" value="900" required></label><label>From role<input name="from_role" maxlength="50"></label><label>To role<select name="to_role" required><option>ADMIN</option><option>SAFETY_OFFICER</option><option>PLANT_MANAGER</option><option>OPERATOR</option><option>SUPERVISOR</option><option>VIEWER</option></select></label><label>Level<input name="escalation_level" type="number" min="1" max="10" value="1" required></label><label>Source reference<input name="source_reference" maxlength="500"></label><button class="inline-button" type="submit">Save escalation policy</button>
      </div></form>
      <form class="section config-form" data-endpoint="/configuration/notification-policies" data-number-fields="dedup_window_seconds" data-checkbox-fields="is_enabled"><div class="section-head"><h2>Notification policy</h2><span>DELIVERY STATUS BELOW</span></div><div class="section-body">
        <label>Name<input name="name" required maxlength="150"></label><label>Event type<input name="event_type" maxlength="50"></label><label>Severity<input name="severity" maxlength="20"></label><label>Zone<select name="zone_id">${scopedOptions(zones, (item) => `${item.name} (${item.code})`)}</select></label><label>Channel<select name="channel"><option>DASHBOARD</option><option>EMAIL</option><option>WHATSAPP</option><option>WEBHOOK</option><option>SMS</option><option>TEAMS</option><option>BUZZER</option></select></label><label>Recipient role<select name="recipient_role" required><option>ADMIN</option><option>SAFETY_OFFICER</option><option>PLANT_MANAGER</option><option>OPERATOR</option><option>SUPERVISOR</option><option>VIEWER</option></select></label><label>Deduplication window seconds<input name="dedup_window_seconds" type="number" min="0" max="86400" value="300" required></label><label>Source reference<input name="source_reference" maxlength="500"></label><label class="checkbox-label"><input name="is_enabled" type="checkbox" checked> Enable policy</label><button class="inline-button" type="submit">Save notification policy</button>
      </div></form>
    </div></details>
    <section class="section notification-transport"><div class="section-head"><h2>Delivery transports</h2><span>TEST SENDS CONTACT REAL RECIPIENTS</span></div><div class="section-body">${stateRows(notificationChannels.filter((item) => ["EMAIL", "WHATSAPP"].includes(item.channel)).map((item) => [item.channel, `${item.configuration_state}${item.delivery_status || item.last_delivery_status ? ` · ${deliveryStatusOf(item)}` : ""} · QUEUED ${formatCount(item.queued)} · SENT ${formatCount(item.sent)} · FAILED ${formatCount(item.failed)}`]))}<p class="config-note">Configure transport credentials in the backend environment, restart the API, then send only to an operator-approved recipient. A provider acceptance is not proof that a person read the message.</p><div class="notification-test-grid"><form id="email-test-form"><label>Test email recipient<input name="recipient" type="email" required autocomplete="email"></label><button class="inline-button" type="submit">TEST EMAIL</button></form><form id="whatsapp-test-form"><label>WhatsApp recipient in E.164 format<input name="recipient" type="tel" required placeholder="+15551234567" autocomplete="tel"></label><button class="inline-button" type="submit">TEST WHATSAPP</button></form></div><p id="config-test-result" class="test-result" role="status">No provider test has been sent from this page.</p></div></section>
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
    const output = document.querySelector("#config-test-result");
    if (output) output.textContent = `REQUEST_SENT_TO_BACKEND · ${channel.toUpperCase()}`;
    try {
      const result = await request(`/notifications/test/${channel}`, { method: "POST", body: JSON.stringify({ recipient }) });
      if (output) output.textContent = deliveryTestSummary(`${channel.toUpperCase()} TEST RESULT`, result);
    } catch (error) {
      if (output) output.textContent = `${channel.toUpperCase()} TEST FAILED · ${error.message}`;
    }
  }));
}

function renderBrowserWebcamPanel() {
  return `<section class="webcam-panel browser-webcam-panel" aria-labelledby="browser-webcam-title">
    <div class="webcam-heading"><div><p class="eyebrow">BROWSER PERMISSION · LOCAL PREVIEW</p><h2 id="browser-webcam-title">Browser Webcam</h2></div><span id="browser-camera-state" class="webcam-badge is-neutral">NOT STARTED</span></div>
    <p class="browser-camera-note">This preview uses this browserâ€™s camera permission and stays in the browser. It is not sent to backend AI inference and does not create detections or events. Use localhost or HTTPS.</p>
    <div class="webcam-preview"><video id="browser-camera-video" autoplay muted playsinline hidden></video><div id="browser-camera-empty" class="webcam-preview-empty">Start to request explicit browser camera permission.</div><span class="preview-source-label">BROWSER ONLY · NOT AI CONNECTED</span></div>
    <div class="webcam-controls"><div class="webcam-actions"><select id="browser-camera-devices" class="select-input" aria-label="Browser camera device"><option value="">Default camera</option></select><button class="webcam-button webcam-button-primary" id="browser-camera-start" type="button">Allow &amp; Start Preview</button><button class="webcam-button" id="browser-camera-stop" type="button" disabled>Stop Preview</button></div></div>
    <div class="webcam-telemetry"><article class="webcam-stat"><span>Resolution</span><strong id="browser-camera-resolution">NOT_OBSERVED</strong></article><article class="webcam-stat"><span>Measured FPS</span><strong id="browser-camera-fps">NOT_MEASURED</strong></article><article class="webcam-stat"><span>Frames</span><strong id="browser-camera-frames">0</strong></article><article class="webcam-stat"><span>AI connection</span><strong>NOT_CONNECTED</strong></article></div>
    <div class="webcam-error-row"><strong>Permission / device status</strong><span id="browser-camera-error" role="status">Camera access has not been requested.</span></div>
  </section>`;
}

async function startBrowserCamera() {
  const state = document.querySelector("#browser-camera-state");
  const message = document.querySelector("#browser-camera-error");
  const select = document.querySelector("#browser-camera-devices");
  if (!navigator.mediaDevices?.getUserMedia) {
    message.textContent = "Browser camera API unavailable. Open on localhost or HTTPS in a supported browser.";
    state.textContent = "UNAVAILABLE";
    return;
  }
  stopBrowserCamera();
  state.textContent = "REQUESTING_PERMISSION";
  message.textContent = "Waiting for explicit browser permission…";
  try {
    const video = select.value ? { deviceId: { exact: select.value }, width: { ideal: 1280 }, height: { ideal: 720 } } : { width: { ideal: 1280 }, height: { ideal: 720 } };
    browserCameraStream = await navigator.mediaDevices.getUserMedia({ audio: false, video });
    const element = document.querySelector("#browser-camera-video");
    element.srcObject = browserCameraStream;
    element.hidden = false;
    await element.play();
    const track = browserCameraStream.getVideoTracks()[0];
    const settings = track.getSettings();
    document.querySelector("#browser-camera-resolution").textContent = settings.width && settings.height ? `${settings.width} × ${settings.height}` : "NOT_REPORTED";
    state.textContent = "LIVE_BROWSER_PREVIEW";
    message.textContent = `Browser permission granted; ${track.label || "camera"}. Preview remains local to this browser.`;
    document.querySelector("#browser-camera-start").disabled = true;
    document.querySelector("#browser-camera-stop").disabled = false;
    if (navigator.mediaDevices.enumerateDevices) {
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput");
      select.replaceChildren(...devices.map((device, index) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || `Camera ${index + 1}`;
        return option;
      }));
      select.value = settings.deviceId || track.getSettings().deviceId || "";
    }
    browserCameraFrames = 0;
    browserCameraFrameStartedAt = performance.now();
    document.querySelector("#browser-camera-frames").textContent = "0";
    measureBrowserCameraFrames(element);
  } catch (error) {
    stopBrowserCamera();
    state.textContent = "CAMERA_ERROR";
    message.textContent = `${error.name || "CameraError"}: ${error.message || "Browser camera permission or device unavailable."}`;
  }
}

function measureBrowserCameraFrames(video) {
  if (!browserCameraStream) return;
  const onFrame = (_now, metadata) => {
    if (!browserCameraStream) return;
    browserCameraFrames += 1;
    document.querySelector("#browser-camera-frames").textContent = String(browserCameraFrames);
    const elapsed = (performance.now() - browserCameraFrameStartedAt) / 1000;
    if (elapsed >= 2) document.querySelector("#browser-camera-fps").textContent = `${(browserCameraFrames / elapsed).toFixed(2)} fps`;
    if (metadata?.width && metadata?.height) document.querySelector("#browser-camera-resolution").textContent = `${metadata.width} × ${metadata.height}`;
    browserCameraFrameHandle = video.requestVideoFrameCallback(onFrame);
  };
  if (video.requestVideoFrameCallback) browserCameraFrameHandle = video.requestVideoFrameCallback(onFrame);
  else browserCameraFrameHandle = window.setInterval(() => {
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      browserCameraFrames += 1;
      document.querySelector("#browser-camera-frames").textContent = String(browserCameraFrames);
      const elapsed = (performance.now() - browserCameraFrameStartedAt) / 1000;
      if (elapsed >= 2) document.querySelector("#browser-camera-fps").textContent = `${(browserCameraFrames / elapsed).toFixed(2)} fps (sampled)`;
    }
  }, 100);
}

function stopBrowserCamera() {
  if (browserCameraFrameHandle !== null) {
    const video = document.querySelector("#browser-camera-video");
    if (video?.cancelVideoFrameCallback) video.cancelVideoFrameCallback(browserCameraFrameHandle);
    else window.clearInterval(browserCameraFrameHandle);
    browserCameraFrameHandle = null;
  }
  browserCameraStream?.getTracks().forEach((track) => track.stop());
  browserCameraStream = null;
  const video = document.querySelector("#browser-camera-video");
  if (video) { video.srcObject = null; video.hidden = true; }
  const state = document.querySelector("#browser-camera-state");
  if (state) state.textContent = "STOPPED · DEVICE RELEASED";
  const start = document.querySelector("#browser-camera-start");
  if (start) start.disabled = false;
  const stop = document.querySelector("#browser-camera-stop");
  if (stop) stop.disabled = true;
  const empty = document.querySelector("#browser-camera-empty");
  if (empty) { empty.hidden = false; empty.textContent = "Browser preview stopped; media tracks released."; }
}

async function renderSystem() {
  const health = await request("/system/health");
  const model = health.model || {};
  content.innerHTML = `<div class="status-banner"><div><strong><span class="status-dot ${health.overall_status === "OPERATIONAL" ? "dot-green" : "dot-amber"}"></span> ${escapeHtml(health.overall_status || "NOT_AVAILABLE")}</strong><span>Derived from available subsystem checks. This does not imply real-input validation.</span></div><span class="status-pill">${stateBadge(health.validation_status || "NOT_VALIDATED")}</span></div>
    <div class="metrics-grid">
      ${metricBadge("Process uptime", formatUptime(health), health.uptime_state || "UPTIME_UNAVAILABLE", health.uptime_seconds === null || health.uptime_seconds === undefined ? "No uptime measurement is available" : `Measured ${formatCount(health.uptime_seconds)} seconds`)}
      ${metricBadge("Disk state", health.disk_state || "DISK_UNAVAILABLE", health.disk_state || "DISK_UNAVAILABLE", health.disk_free_percent === null || health.disk_free_percent === undefined ? "Free space is not measured" : `${health.disk_free_percent}% free on ${health.disk_path || "NOT_REPORTED"}`)}
      ${metricBadge("Camera FPS", formatFps(health.camera_fps), health.measured_performance || "NOT_MEASURED", "Measured from observed frames only", "NOT_MEASURED")}
      ${metricBadge("Inference FPS", formatFps(health.inference_fps), health.measured_performance || "NOT_MEASURED", "Measured from completed inferences only", "NOT_MEASURED")}
      ${metricBadge("Email transport", health.email_transport || "EMAIL_NOT_CONFIGURED", health.email_transport || "EMAIL_NOT_CONFIGURED", "Configuration state only", "EMAIL_NOT_CONFIGURED")}
      ${metricBadge("WhatsApp transport", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", "Configuration state only", "WHATSAPP_NOT_CONFIGURED")}
      ${metricBadge("Alarm subsystem", health.alarm_subsystem || "UNKNOWN", health.alarm_subsystem || "UNKNOWN", "Software alarm policy and engine")}
      ${metricBadge("Physical alarm", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", `Transports: ${formatTransportList(health.physical_alarm_transports)}`, "PHYSICAL_ALARM_NOT_CONFIGURED")}
      ${metricBadge("Notification worker", health.notification_delivery_worker || "UNKNOWN", health.notification_delivery_worker || "UNKNOWN", health.notification_delivery_last_error ? `Last error: ${health.notification_delivery_last_error}` : "No worker error reported")}
      ${metricBadge("Safety event worker", health.safety_event_worker || "UNKNOWN", health.safety_event_worker || "UNKNOWN", health.safety_event_worker_last_error ? `Last error: ${health.safety_event_worker_last_error}` : "No worker error reported")}
    </div>
    <div class="content-grid"><section class="section"><div class="section-head"><h2>Subsystem status</h2><span>REPORTED STATE</span></div><div class="section-body"><div class="state-list">
      ${badgeRow("Application process", health.application)}
      ${badgeRow("Uptime", health.uptime_state || "UPTIME_UNAVAILABLE")}
      ${badgeRow("Database", health.database)}
      ${badgeRow("Migrations", health.migrations)}
      ${badgeRow("Cameras", health.camera_state)}
      ${badgeRow("Camera health worker", health.camera_health_worker)}
      ${badgeRow("Safety / escalation worker", health.safety_event_worker || "UNKNOWN")}
      ${badgeRow("Alarm subsystem", health.alarm_subsystem || "UNKNOWN")}
      ${badgeRow("Physical alarm output", health.physical_alarm_state || "PHYSICAL_ALARM_NOT_CONFIGURED", "PHYSICAL_ALARM_NOT_CONFIGURED")}
      ${badgeRow("Physical alarm transports", formatTransportList(health.physical_alarm_transports), "NONE_CONFIGURED")}
      ${badgeRow("Inference pipeline", health.inference_pipeline)}
      ${badgeRow("Running pipelines", formatCount(health.running_pipeline_count))}
      ${badgeRow("Model", health.model_state)}
      ${badgeRow("Evidence", health.evidence_subsystem)}
      ${badgeRow("Notifications", health.notification_subsystem)}
      ${badgeRow("Notification delivery worker", health.notification_delivery_worker || "UNKNOWN")}
      ${badgeRow("Email transport", health.email_transport || "EMAIL_NOT_CONFIGURED", "EMAIL_NOT_CONFIGURED")}
      ${badgeRow("WhatsApp transport", health.whatsapp_transport || "WHATSAPP_NOT_CONFIGURED", "WHATSAPP_NOT_CONFIGURED")}
      ${badgeRow("Disk state", health.disk_state || "DISK_UNAVAILABLE")}
      ${badgeRow("Disk free percent", health.disk_free_percent ?? "NOT_MEASURED", "NOT_MEASURED")}
      ${badgeRow("Measured performance", health.measured_performance || "NOT_MEASURED", "NOT_MEASURED")}
      ${badgeRow("Camera FPS", health.camera_fps ?? "NOT_MEASURED", "NOT_MEASURED")}
      ${badgeRow("Inference FPS", health.inference_fps ?? "NOT_MEASURED", "NOT_MEASURED")}
      ${badgeRow("Frontend connectivity", health.frontend_connectivity)}
      ${badgeRow("IGL validated", health.igl_validated ? "IGL_VALIDATED" : "NOT_VALIDATED")}
      ${badgeRow("IGL configuration", health.igl_configuration_status || "NOT_AVAILABLE")}
      ${badgeRow("Access control mode", health.access_control || "NOT_REPORTED")}
    </div></div></section>
    <section class="section"><div class="section-head"><h2>Model configuration</h2><span>BACKEND REPORT</span></div><div class="section-body"><div class="state-list">
      ${badgeRow("Name", model.model_name || "MODEL_NOT_CONFIGURED", "MODEL_NOT_CONFIGURED")}
      ${badgeRow("Version", model.model_version || "MODEL_NOT_CONFIGURED", "MODEL_NOT_CONFIGURED")}
      ${badgeRow("Weights loaded", model.weights_loaded ? "WEIGHTS_LOADED" : "MODEL_NOT_CONFIGURED")}
      ${badgeRow("Classes", model.classes?.length ?? "NOT_AVAILABLE")}
      ${badgeRow("Confidence threshold", model.confidence_threshold ?? "NOT_CONFIGURED", "NOT_CONFIGURED")}
      ${badgeRow("Inference count", formatCount(model.inference_count))}
      ${badgeRow("Inference failures", formatCount(model.inference_failure_count))}
      ${badgeRow("Average latency", model.average_inference_latency_ms ?? "NOT_MEASURED", "NOT_MEASURED")}
    </div>${health.degraded_reasons?.length ? `<p class="config-note">Degraded because: ${escapeHtml(health.degraded_reasons.join(", "))}</p>` : `<p class="config-note">No degraded reason is currently reported.</p>`}</div></section></div>`;
}

async function renderAudit() {
  const rows = await request("/identity/audit-logs?limit=100");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Audit activity</h2><span>LAST 100 RECORDS</span></div>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Resource</th><th>Actor</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(formatDate(row.timestamp))}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.resource_type)} ${escapeHtml(row.resource_id || "")}</td><td>${escapeHtml(row.user_id || "SYSTEM")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No audit records available.</strong></div></div>`}</section>`;
}

const renderers = { overview: renderOverview, cameras: renderCameras, workers: renderWorkers, events: renderEvents, incidents: renderIncidents, alarm: renderAlarmCenter, rules: renderRules, notifications: renderNotifications, evidence: renderEvidence, configuration: renderConfiguration, analytics: renderAnalytics, system: renderSystem, audit: renderAudit, unavailable: async () => { content.innerHTML = unavailableMarkup(); } };

async function navigate(view) {
  if (view !== "workers") stopWorkersPolling();
  if (view !== "cameras") stopBrowserCamera();
  if (view !== "cameras") stopWebcamPolling();
  currentView = view;
  const [kicker, title] = labels[view] || labels.overview;
  document.querySelector("#view-kicker").textContent = kicker;
  document.querySelector("#view-title").textContent = title;
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  showMessage("", false);
  content.innerHTML = `<div class="empty-state"><div><strong>Loading current API data</strong>Please wait.</div></div>`;
  try {
    const renderSucceeded = await (renderers[view] || renderers.unavailable)();
    if (view === "workers") startWorkersPolling(renderSucceeded === false ? 10000 : 5000);
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

function applyNavigationPermissions() {
  document.querySelectorAll("#navigation [data-view]").forEach((button) => {
    const allowedByPermission = userCan(button.dataset.permission);
    const roles = button.dataset.roles?.split(",") || [];
    const allowedByRole = !roles.length || roles.includes(currentIdentity?.role?.name);
    button.hidden = !(allowedByPermission && allowedByRole);
  });
}

function showApplication() {
  applyNavigationPermissions();
  loginShell.hidden = true;
  appShell.hidden = false;
  document.querySelector("#logout-button").hidden = !authToken;
  startAlarmPolling();
  navigate(currentView);
}

function showLogin(message = "") {
  stopBrowserCamera();
  stopWebcamPolling();
  if (alarmPollingTimer) clearInterval(alarmPollingTimer);
  alarmPollingTimer = null;
  appShell.hidden = true;
  loginShell.hidden = false;
  document.querySelector("#login-message").textContent = message;
}

window.addEventListener("pagehide", stopBrowserCamera);

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
      currentIdentity = await request("/auth/me");
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
  currentIdentity = null;
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  apiBase = event.target.value.trim().replace(/\/$/, "");
  sessionStorage.setItem(API_STORAGE_KEY, apiBase);
  document.querySelector("#api-base").value = apiBase;
  initializeApplication();
});
document.querySelector("#api-base").addEventListener("change", (event) => {
  authToken = null;
  currentIdentity = null;
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
  currentIdentity = null;
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
    currentIdentity = payload.user;
    sessionStorage.setItem(AUTH_TOKEN_KEY, authToken);
    form.reset();
    showApplication();
  } catch (error) {
    document.querySelector("#login-message").textContent = error.message;
  }
});

initializeApplication();
