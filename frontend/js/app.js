"use strict";

const API_STORAGE_KEY = "iglSafetyApiBase";
const TOKEN_STORAGE_KEY = "iglSafetyAccessToken";
const USER_STORAGE_KEY = "iglSafetyUsername";
const DEFAULT_API = "http://localhost:8000/api/v1";
const labels = {
  overview: ["OPERATIONS / CURRENT STATE", "Overview"],
  cameras: ["MONITOR / INPUTS", "Cameras"],
  events: ["REVIEW / WORKFLOW", "Events"],
  evidence: ["REVIEW / EVENT MATERIAL", "Evidence"],
  analytics: ["RECORDS / SUMMARY", "Analytics"],
  system: ["SUBSYSTEMS / STATUS", "System health"],
  audit: ["SECURITY / ACTIVITY", "Audit log"],
  unavailable: ["CAPABILITY / NOT IMPLEMENTED", "Unavailable modules"],
};

let apiBase = sessionStorage.getItem(API_STORAGE_KEY) || DEFAULT_API;
let token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
let username = sessionStorage.getItem(USER_STORAGE_KEY) || "Authenticated";
let eventsCache = [];
let currentView = "overview";

const loginScreen = document.querySelector("#login-screen");
const appShell = document.querySelector("#app-shell");
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

function logout() {
  token = null;
  username = "Authenticated";
  sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(USER_STORAGE_KEY);
  loginScreen.hidden = false;
  appShell.hidden = true;
}

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (response.status === 401) {
    logout();
    throw new Error("Session expired. Sign in again.");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch { /* Keep the HTTP status only. */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function unavailableMarkup() {
  return `<section class="section"><div class="section-head"><h2>Modules not available</h2><span>NO BACKEND IMPLEMENTATION</span></div><div class="section-body unavailable-grid">
    <div class="unavailable-item"><strong>PPE safety rules</strong>NOT_IMPLEMENTED</div>
    <div class="unavailable-item"><strong>Configured zones</strong>NOT_CONFIGURED</div>
    <div class="unavailable-item"><strong>Incidents / near-misses</strong>NOT_AVAILABLE</div>
    <div class="unavailable-item"><strong>Corrective actions</strong>NOT_IMPLEMENTED</div>
    <div class="unavailable-item"><strong>Fire / fall / proximity</strong>NOT_VALIDATED</div>
    <div class="unavailable-item"><strong>Live video display</strong>NOT_IMPLEMENTED</div>
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
  const model = health?.model?.status || "MODEL_NOT_CONFIGURED";
  const validation = health?.validation_status || "NOT_VALIDATED";
  const message = summary?.data_status === "NO_DATA" ? "No operational records are available yet." : "Values shown are current backend records only.";
  const dot = status === "ONLINE" ? "dot-green" : status === "DEGRADED" ? "dot-amber" : "dot-muted";
  return `<div class="status-banner"><div><strong><span class="status-dot ${dot}"></span> ${escapeHtml(status)} · AI ${escapeHtml(model)}</strong><span>${escapeHtml(message)}</span></div><span class="status-pill">VALIDATION ${escapeHtml(validation)}</span></div>`;
}

async function renderOverview() {
  const [summary, health, cameras, events] = await Promise.all([
    request("/analytics/summary"),
    request("/system/health"),
    request("/cameras?active_only=true"),
    request("/events?limit=100&offset=0"),
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
      <section class="section"><div class="section-head"><h2>Subsystems</h2><span>LIVE API STATE</span></div><div class="section-body">${stateRows([
        ["API", health.api], ["Database", health.database], ["Migrations", health.migrations],
        ["Camera monitor", health.camera_health_worker], ["Inference", health.inference_pipeline],
        ["Evidence", health.evidence_subsystem], ["Notifications", health.notification_subsystem],
        ["Frontend connectivity", health.frontend_connectivity], ["Accuracy", summary.accuracy_metrics_status],
      ])}</div></section>
    </div>`;
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
  const cameras = await request("/cameras?active_only=false");
  const rows = cameras.map((camera) => `<tr>
    <td><strong>${escapeHtml(camera.name)}</strong><br><span class="muted mono">${escapeHtml(camera.code)}</span></td>
    <td>${escapeHtml(camera.health?.status || "CONFIGURED")}</td>
    <td>${escapeHtml(camera.health?.inference_status || "NOT_RUNNING")}</td>
    <td>${escapeHtml(camera.fps)} fps configured<br><span class="muted">${escapeHtml(camera.health?.measured_fps ?? "NOT_AVAILABLE")} fps measured</span></td>
    <td>${escapeHtml(camera.health?.observed_resolution ?? "NOT_AVAILABLE")}</td>
    <td>${camera.is_active ? "ACTIVE" : "DEACTIVATED"}</td>
  </tr>`).join("");
  content.innerHTML = `<div class="filter-bar"><p>Configured values and observed telemetry are shown separately.</p><span class="status-pill">${cameras.length} RECORDS</span></div>
    <section class="section"><div class="section-head"><h2>Camera inventory</h2><span>STREAM URL CREDENTIALS REDACTED</span></div>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>Camera</th><th>Connection</th><th>AI state</th><th>Frame rate</th><th>Observed resolution</th><th>Config</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No cameras configured.</strong>Register an authorized source through the authenticated camera API.</div></div>`}
    </section>`;
}

async function renderEvents() {
  const events = await request("/events?limit=200&offset=0");
  eventsCache = events;
  const rows = events.map((event) => `<tr><td class="mono">${escapeHtml(event.id.slice(0, 12))}</td><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.observation_state)}</td><td>${escapeHtml(event.workflow_state)}</td><td>${escapeHtml(event.severity)}</td><td>${escapeHtml(formatDate(event.started_at))}</td><td>${transitionButton(event)}</td></tr>`).join("");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Persisted events</h2><span>NO AUTOMATIC EVENT GENERATION CONNECTED</span></div>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Event ID</th><th>Type</th><th>Observation</th><th>Workflow</th><th>Severity</th><th>Timestamp</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div><strong>No events recorded.</strong>The platform does not create synthetic event history.</div></div>`}
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

async function renderEvidence() {
  const events = eventsCache.length ? eventsCache : await request("/events?limit=200&offset=0");
  eventsCache = events;
  if (!events.length) {
    content.innerHTML = `<section class="section"><div class="section-head"><h2>Event evidence</h2><span>LINKED MATERIAL ONLY</span></div><div class="empty-state"><div><strong>No events recorded.</strong>Evidence is not created without an event and captured frame.</div></div></section>`;
    return;
  }
  content.innerHTML = `<div class="filter-bar"><p>Evidence files are served only for persisted events and authorized users.</p><select id="evidence-event" class="select-input" aria-label="Select event">${events.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.event_type)} · ${escapeHtml(item.id.slice(0, 10))}</option>`).join("")}</select></div><section id="evidence-list" class="section"></section>`;
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
    const response = await fetch(`${apiBase}/events/${encodeURIComponent(eventId)}/evidence/${encodeURIComponent(evidenceId)}/content`, { headers: { Authorization: `Bearer ${token}` } });
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

async function renderSystem() {
  const health = await request("/system/health");
  const model = health.model || {};
  content.innerHTML = `<div class="status-banner"><div><strong>${escapeHtml(health.overall_status)}</strong><span>Derived from available subsystem checks. This does not imply real-input validation.</span></div><span class="status-pill">${escapeHtml(health.validation_status)}</span></div>
    <div class="content-grid"><section class="section"><div class="section-head"><h2>Subsystem status</h2></div><div class="section-body">${stateRows([
      ["API", health.api], ["Database", health.database], ["Migrations", health.migrations], ["Camera monitor", health.camera_health_worker],
      ["Inference pipeline", health.inference_pipeline], ["Model", model.status], ["Evidence", health.evidence_subsystem],
      ["Notifications", health.notification_subsystem], ["Frontend connectivity", health.frontend_connectivity], ["IGL validated", health.igl_validated],
    ])}</div></section><section class="section"><div class="section-head"><h2>Model configuration</h2></div><div class="section-body">${stateRows([
      ["Name", model.model_name], ["Version", model.model_version], ["Classes", model.classes?.length ?? "NOT_AVAILABLE"],
      ["Confidence threshold", model.confidence_threshold], ["Inference count", model.inference_count], ["Average latency", model.average_inference_latency_ms ?? "NOT_MEASURED"],
    ])}</div></section></div>`;
}

async function renderAudit() {
  const rows = await request("/auth/audit-logs?limit=100");
  content.innerHTML = `<section class="section"><div class="section-head"><h2>Audit activity</h2><span>LAST 100 RECORDS</span></div>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Resource</th><th>Actor</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(formatDate(row.timestamp))}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.resource_type)} ${escapeHtml(row.resource_id || "")}</td><td>${escapeHtml(row.user_id || "SYSTEM")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div><strong>No audit records available.</strong></div></div>`}</section>`;
}

const renderers = { overview: renderOverview, cameras: renderCameras, events: renderEvents, evidence: renderEvidence, analytics: renderAnalytics, system: renderSystem, audit: renderAudit, unavailable: async () => { content.innerHTML = unavailableMarkup(); } };

async function navigate(view) {
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
  const color = status === "ONLINE" ? "dot-green" : status === "UNAVAILABLE" ? "dot-red" : "dot-amber";
  dot.className = `status-dot ${color}`;
  document.querySelector("#api-state-text").textContent = text;
}

function showApplication() {
  loginScreen.hidden = true;
  appShell.hidden = false;
  document.querySelector("#user-chip").textContent = username;
  navigate(currentView);
}

document.querySelector("#api-base").value = apiBase;
document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const apiInput = document.querySelector("#api-base").value.trim().replace(/\/$/, "");
  const credentials = {
    username: document.querySelector("#username").value,
    password: document.querySelector("#password").value,
  };
  document.querySelector("#login-error").textContent = "";
  try {
    const response = await fetch(`${apiInput}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials),
    });
    if (!response.ok) throw new Error("Sign-in failed. Check credentials and API URL.");
    const payload = await response.json();
    apiBase = apiInput;
    token = payload.access_token;
    username = payload.username;
    sessionStorage.setItem(API_STORAGE_KEY, apiBase);
    sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    sessionStorage.setItem(USER_STORAGE_KEY, username);
    document.querySelector("#password").value = "";
    showApplication();
  } catch (error) {
    document.querySelector("#login-error").textContent = error.message;
  }
});

document.querySelector("#navigation").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) navigate(button.dataset.view);
});
document.querySelector("#refresh-button").addEventListener("click", () => navigate(currentView));
document.querySelector("#logout-button").addEventListener("click", logout);

if (token) showApplication();
