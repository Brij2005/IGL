const API_BASE = (import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000/api").replace(/\/+$/, "");

const TOKEN_KEY = "igl_auth_token";
const AUTH_EVENT = "igl-auth-expired";

export function getToken() {
  try {
    return globalThis.localStorage?.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) globalThis.localStorage?.setItem(TOKEN_KEY, token);
    else globalThis.localStorage?.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export function clearToken() {
  setToken("");
}

export function isAuthenticated() {
  return Boolean(getToken());
}

function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return globalThis.fetch(url, { ...options, headers }).then((res) => {
    if (res.status === 401 && token) {
      // Session expired or token revoked: drop it and notify the app.
      clearToken();
      try {
        globalThis.dispatchEvent?.(new CustomEvent(AUTH_EVENT));
      } catch {
        /* ignore */
      }
    }
    return res;
  });
}

export async function loginApi(username, password) {
  const res = await authFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
  const data = await res.json().catch(() => null);
  if (!res.ok || !data?.access_token) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : "Login failed");
  }
  setToken(data.access_token);
  return data;
}

export async function logoutApi() {
  try {
    await authFetch(`${API_BASE}/auth/logout`, { method: "POST" });
  } catch {
    /* even if the call fails, clear locally */
  } finally {
    clearToken();
  }
  return { status: "LOGGED_OUT" };
}

export async function fetchSessionApi() {
  if (!isAuthenticated()) return null;
  const res = await authFetch(`${API_BASE}/auth/me`);
  if (!res.ok) return null;
  const data = await res.json().catch(() => null);
  return data?.user || null;
}

function withToken(url) {
  const token = getToken();
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

export function cameraStreamUrl(cameraId) {
  return withToken(`${API_BASE}/cameras/${cameraId}/stream.mjpg`);
}

export function cameraFrameUrl(cameraId) {
  return withToken(`${API_BASE}/cameras/${cameraId}/frame.jpg`);
}

export function evidenceUrl(evidenceId) {
  return withToken(`${API_BASE}/evidence/${evidenceId}`);
}

export function reportDownloadUrl(reportId) {
  return withToken(`${API_BASE}/reports/${reportId}/download`);
}

export async function fetchStats() {
  try {
    const res = await authFetch(`${API_BASE}/dashboard/stats`);
    if (!res.ok) throw new Error("Failed to fetch dashboard stats");
    return await res.json();
  } catch (err) {
    console.warn("Backend offline or unreachable:", err);
    return null;
  }
}

export async function detectYoloObjectsApi(imageBase64, conf = 0.10, imgsz = 1280) {
  try {
    const res = await authFetch(`${API_BASE}/yolo/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_base64: imageBase64, conf, imgsz })
    });
    if (!res.ok) throw new Error("YOLO API error");
    return await res.json();
  } catch (err) {
    console.warn("Backend YOLO API unreachable:", err);
    return null;
  }
}

export async function fetchCameras() {
  try {
    const res = await authFetch(`${API_BASE}/cameras`);
    if (!res.ok) throw new Error("Failed to fetch cameras");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function registerCameraApi(cameraData) {
  try {
    const res = await authFetch(`${API_BASE}/cameras`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cameraData)
    });
    return await res.json();
  } catch (err) {
    console.error("Camera registration failed:", err);
    return null;
  }
}

export async function updateCameraStatusApi(cameraId, statusData) {
  try {
    const res = await authFetch(`${API_BASE}/cameras/${cameraId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(statusData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchAlerts() {
  try {
    const res = await authFetch(`${API_BASE}/alerts`);
    if (!res.ok) throw new Error("Failed to fetch alerts");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function postAlertApi(alertData) {
  try {
    const res = await authFetch(`${API_BASE}/alerts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(alertData)
    });
    return await res.json();
  } catch (err) {
    console.error("Failed to store alert in backend:", err);
    return null;
  }
}

export async function acknowledgeAlertApi(alertId, user = "Controller") {
  try {
    const res = await authFetch(`${API_BASE}/alerts/${alertId}/acknowledge`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user })
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function resolveAlertApi(alertId, notes, user = "Safety Manager") {
  try {
    const res = await authFetch(`${API_BASE}/alerts/${alertId}/resolve`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes, user })
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchObservations() {
  try {
    const res = await authFetch(`${API_BASE}/observations`);
    if (!res.ok) throw new Error("Failed to fetch observations");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function recordObservationApi(observationData) {
  try {
    const res = await authFetch(`${API_BASE}/observations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(observationData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function checkPersonnelAnomalyApi(featureVector) {
  try {
    const res = await authFetch(`${API_BASE}/personnel/check-anomaly`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feature_vector: featureVector })
    });
    return await res.json();
  } catch (err) {
    return { status: "INSUFFICIENT_HISTORICAL_DATA", reason: "Backend connection error" };
  }
}

export async function fetchRestrictedZones(cameraId = null) {
  try {
    const url = cameraId ? `${API_BASE}/restricted-zones?camera_id=${cameraId}` : `${API_BASE}/restricted-zones`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error("Failed to fetch zones");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function postRestrictedZoneApi(zoneData) {
  try {
    const res = await authFetch(`${API_BASE}/restricted-zones`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(zoneData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchAnalyticsApi() {
  try {
    const res = await authFetch(`${API_BASE}/analytics`);
    if (!res.ok) throw new Error("Failed to fetch analytics");
    return await res.json();
  } catch (err) {
    return { has_data: false, message: "No data available for analytics" };
  }
}

export async function processFrameQualityApi(imageBase64) {
  try {
    const res = await authFetch(`${API_BASE}/process-frame`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_base64: imageBase64 })
    });
    return await res.json();
  } catch (err) {
    return { is_assessable: false, reason: "Unable to inspect frame via server" };
  }
}

// ----------------------------------------------------
// PERSONNEL MONITORING API HELPERS
// ----------------------------------------------------
export async function fetchPersonnelRulesApi() {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/rules`);
    if (!res.ok) throw new Error("Failed to fetch rules");
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function createPersonnelRuleApi(ruleData) {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ruleData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function updatePersonnelRuleApi(ruleId, ruleData) {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/rules/${ruleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ruleData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function deletePersonnelRuleApi(ruleId) {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/rules/${ruleId}`, {
      method: "DELETE"
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchPersonnelEventsApi() {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/events`);
    if (!res.ok) throw new Error("Failed to fetch events");
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function postPersonnelEventApi(eventData) {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eventData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function acknowledgePersonnelEventApi(eventId, user = "Controller") {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/events/${eventId}/acknowledge`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user })
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function resolvePersonnelEventApi(eventId, notes = "Verified", user = "Safety Manager") {
  try {
    const res = await authFetch(`${API_BASE}/personnel-monitoring/events/${eventId}/resolve`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes, user })
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

// ----------------------------------------------------
// EXPECTED PERSONNEL & WHATSAPP NOTIFICATION API HELPERS
// ----------------------------------------------------
export async function fetchExpectedPersonnelRulesApi() {
  try {
    const res = await authFetch(`${API_BASE}/expected-personnel/rules`);
    if (!res.ok) throw new Error("Failed to fetch expected personnel rules");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function createExpectedPersonnelRuleApi(ruleData) {
  try {
    const res = await authFetch(`${API_BASE}/expected-personnel/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ruleData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function updateExpectedPersonnelRuleApi(ruleId, ruleData) {
  try {
    const res = await authFetch(`${API_BASE}/expected-personnel/rules/${ruleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ruleData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function deleteExpectedPersonnelRuleApi(ruleId) {
  try {
    const res = await authFetch(`${API_BASE}/expected-personnel/rules/${ruleId}`, {
      method: "DELETE"
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchWhatsAppStatusApi() {
  try {
    const res = await authFetch(`${API_BASE}/notifications/whatsapp/status`);
    if (!res.ok) throw new Error("Failed to fetch WhatsApp status");
    return await res.json();
  } catch (err) {
    return { status: "MOCK MODE", mode: "MOCK", is_configured: false, controller_number: "" };
  }
}

export async function testWhatsAppAlertApi(controllerWhatsapp = null) {
  try {
    const res = await authFetch(`${API_BASE}/notifications/whatsapp/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controller_whatsapp: controllerWhatsapp })
    });
    return await res.json();
  } catch (err) {
    return { success: false, status: "FAILED", message: "Failed to connect to backend service" };
  }
}

export async function sendUnknownPersonAlertApi(eventData) {
  try {
    const res = await authFetch(`${API_BASE}/notifications/whatsapp/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eventData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchNotificationLogsApi() {
  try {
    const res = await authFetch(`${API_BASE}/notifications/logs`);
    if (!res.ok) throw new Error("Failed to fetch notification logs");
    return await res.json();
  } catch (err) {
    return [];
  }
}

// ----------------------------------------------------
// REGISTERED PERSONNEL IDENTITY API HELPERS
// ----------------------------------------------------
export async function fetchRegisteredPersonnelApi() {
  try {
    const res = await authFetch(`${API_BASE}/registered-personnel`);
    if (!res.ok) throw new Error("Failed to fetch registered personnel");
    return await res.json();
  } catch (err) {
    return [];
  }
}

export async function createRegisteredPersonnelApi(personData) {
  const res = await authFetch(`${API_BASE}/registered-personnel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(personData)
  });
  const result = await res.json();
  if (!res.ok) {
    const detail = result.detail;
    const message = Array.isArray(detail)
      ? detail.map((issue) => {
        const location = Array.isArray(issue.loc)
          ? issue.loc.filter((part) => part !== "body").join(".")
          : "";
        const reason = issue.msg || "Invalid value";
        return location ? `${location}: ${reason}` : reason;
      }).join("; ")
      : typeof detail === "string"
        ? detail
        : "Failed to save registered personnel.";
    throw new Error(message);
  }
  return result;
}

export async function updateRegisteredPersonnelApi(personId, personData) {
  try {
    const res = await authFetch(`${API_BASE}/registered-personnel/${personId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(personData)
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function deleteRegisteredPersonnelApi(personId) {
  try {
    const res = await authFetch(`${API_BASE}/registered-personnel/${personId}`, {
      method: "DELETE"
    });
    return await res.json();
  } catch (err) {
    return null;
  }
}

// ----------------------------------------------------
// IGL VISIONGUARD OPERATIONS API
// ----------------------------------------------------
async function requestJson(url, options = {}) {
  try {
    const res = await authFetch(url, options);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) return { ok: false, status: res.status, data };
    return { ok: true, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: { detail: err.message } };
  }
}

export async function startCameraApi(cameraId) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}/start`, { method: "POST" })).data;
}
export async function stopCameraApi(cameraId) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}/stop`, { method: "POST" })).data;
}
export async function deleteCameraApi(cameraId) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}`, { method: "DELETE" })).data;
}
export async function testCameraApi(cameraId) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}/test`, { method: "POST" })).data;
}
export async function updateCameraConfigApi(cameraId, payload) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}/config`, { method: "PUT", body: JSON.stringify(payload) })).data;
}
export async function fetchCameraApi(cameraId) {
  return (await requestJson(`${API_BASE}/cameras/${cameraId}`)).data;
}
export async function fetchZonesApi(cameraId = null) {
  const res = await requestJson(`${API_BASE}/zones${cameraId ? `?camera_id=${cameraId}` : ""}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createZoneApi(payload) {
  return (await requestJson(`${API_BASE}/zones`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function deleteZoneApi(zoneId) {
  return (await requestJson(`${API_BASE}/zones/${zoneId}`, { method: "DELETE" })).data;
}
export async function fetchPlantLocationsApi() {
  const res = await requestJson(`${API_BASE}/plant-locations`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createPlantLocationApi(payload) {
  return (await requestJson(`${API_BASE}/plant-locations`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function fetchPipelinesApi(plantLocationId = null) {
  const res = await requestJson(`${API_BASE}/pipelines${plantLocationId ? `?plant_location_id=${plantLocationId}` : ""}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createPipelineApi(payload) {
  return (await requestJson(`${API_BASE}/pipelines`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function fetchModelsApi() {
  const res = await requestJson(`${API_BASE}/models`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchModelHealthApi() {
  return (await requestJson(`${API_BASE}/models/health`)).data;
}
export async function detectModelApi(modelId, imageBase64, conf = 0.25, imgsz = 1280) {
  return (await requestJson(`${API_BASE}/models/${modelId}/detect`, {
    method: "POST",
    body: JSON.stringify({ image_base64: imageBase64, conf, imgsz })
  })).data;
}
export async function reloadModelApi(modelId) {
  return (await requestJson(`${API_BASE}/models/${modelId}/reload`, { method: "POST" })).data;
}
export async function activateModelVersionApi(modelId, version) {
  return (await requestJson(`${API_BASE}/models/${modelId}/activate-version`, {
    method: "POST", body: JSON.stringify({ version })
  })).data;
}
export async function prepareDatasetApi(payload) {
  return (await requestJson(`${API_BASE}/datasets/prepare`, {
    method: "POST",
    body: JSON.stringify(payload)
  })).data;
}
export async function fetchDatasetsApi() {
  const res = await requestJson(`${API_BASE}/datasets`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchEventsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/events?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function processEventApi(payload) {
  return (await requestJson(`${API_BASE}/events/process`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function fetchLeakageIncidentsApi(status = "") {
  const res = await requestJson(`${API_BASE}/incidents/leakage${status ? `?status=${status}` : ""}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function reviewLeakageIncidentApi(incidentId, decision, notes = "") {
  return (await requestJson(`${API_BASE}/incidents/leakage/${incidentId}/review`, {
    method: "PUT", body: JSON.stringify({ decision, notes })
  })).data;
}
export async function fetchSafetyIncidentsApi(status = "") {
  const res = await requestJson(`${API_BASE}/incidents/safety${status ? `?status=${status}` : ""}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function reviewSafetyIncidentApi(incidentId, decision, notes = "") {
  return (await requestJson(`${API_BASE}/incidents/safety/${incidentId}/review`, {
    method: "PUT", body: JSON.stringify({ decision, notes })
  })).data;
}
export async function fetchEvidenceApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/evidence?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchReportsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/reports?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function generateReportApi(reportType, format = "json") {
  return (await requestJson(`${API_BASE}/reports`, { method: "POST", body: JSON.stringify({ report_type: reportType, format }) })).data;
}
export async function fetchAuditLogsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/audit-logs?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchUsersApi() {
  const res = await requestJson(`${API_BASE}/users`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createUserApi(payload) {
  return (await requestJson(`${API_BASE}/users`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function updateUserApi(userId, payload) {
  return (await requestJson(`${API_BASE}/users/${userId}`, { method: "PUT", body: JSON.stringify(payload) })).data;
}
export async function deleteUserApi(userId) {
  return (await requestJson(`${API_BASE}/users/${userId}`, { method: "DELETE" })).data;
}
export async function fetchNotificationStatusApi() {
  return (await requestJson(`${API_BASE}/notifications/status`)).data;
}
export async function testNotificationsApi(channels = ["IN_APP"]) {
  return (await requestJson(`${API_BASE}/notifications/test`, { method: "POST", body: JSON.stringify({ channels }) })).data;
}
export async function fetchNotificationDeliveriesApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/notifications/deliveries?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchSystemHealthApi() {
  return (await requestJson(`${API_BASE}/system/health`)).data;
}
export async function fetchSettingsApi() {
  return (await requestJson(`${API_BASE}/settings`)).data;
}
export async function updateSettingsApi(payload) {
  return (await requestJson(`${API_BASE}/settings`, { method: "PUT", body: JSON.stringify(payload) })).data;
}

export async function uploadEvidenceApi(file) {
  const formData = new FormData();
  formData.append("file", file);
  return (await requestJson(`${API_BASE}/uploads/evidence`, { method: "POST", body: formData })).data;
}

export async function fetchModelVersionsApi(modelId) {
  const res = await requestJson(`${API_BASE}/models/${modelId}/versions`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchModelEvaluationApi(modelId) {
  return (await requestJson(`${API_BASE}/models/${modelId}/evaluation`)).data;
}

// ----------------------------------------------------
// IGL SMART ENTRY & EXIT SECURITY API
// ----------------------------------------------------
export async function fetchSecurityDashboardApi() {
  return (await requestJson(`${API_BASE}/security/dashboard`)).data;
}
export async function fetchAccessRecordsApi({ direction, status, limit = 100 } = {}) {
  const params = new URLSearchParams();
  if (direction) params.set("direction", direction);
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  const res = await requestJson(`${API_BASE}/security/access-records?${params.toString()}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createAccessRecordApi(payload) {
  return (await requestJson(`${API_BASE}/security/access-records`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function reviewAccessRecordApi(recordId, decision, notes = "") {
  return (await requestJson(`${API_BASE}/security/access-records/${recordId}/review`, {
    method: "PUT", body: JSON.stringify({ decision, notes })
  })).data;
}
export async function fetchAssetsApi(status = "") {
  const res = await requestJson(`${API_BASE}/security/assets${status ? `?status=${status}` : ""}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createAssetApi(payload) {
  return (await requestJson(`${API_BASE}/security/assets`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function verifyAssetApi({ rfid, barcode, asset_id: assetId } = {}) {
  const params = new URLSearchParams();
  if (rfid) params.set("rfid", rfid);
  if (barcode) params.set("barcode", barcode);
  if (assetId) params.set("asset_id", assetId);
  return (await requestJson(`${API_BASE}/security/assets/verify?${params.toString()}`)).data;
}
export async function recordAssetMovementApi(assetId, payload) {
  return (await requestJson(`${API_BASE}/security/assets/${assetId}/movements`, {
    method: "POST", body: JSON.stringify(payload)
  })).data;
}
export async function fetchAssetMovementsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/security/assets/movements?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchGatePassesApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/security/gate-passes?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createGatePassApi(payload) {
  return (await requestJson(`${API_BASE}/security/gate-passes`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function fetchThermalReadingsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/security/thermal/readings?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function createThermalReadingApi(payload) {
  return (await requestJson(`${API_BASE}/security/thermal/readings`, { method: "POST", body: JSON.stringify(payload) })).data;
}
export async function analyzeRecordedVideoApi(file, { sourceKind = "cctv", modelId, conf = 0.25, sampleFps = 0, maxFrames = 300, cameraId, gateId } = {}) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("source_kind", sourceKind);
  if (modelId) formData.append("model_id", modelId);
  formData.append("conf", String(conf));
  formData.append("sample_fps", String(sampleFps));
  formData.append("max_frames", String(maxFrames));
  if (cameraId) formData.append("camera_id", cameraId);
  if (gateId) formData.append("gate_id", gateId);
  return (await requestJson(`${API_BASE}/security/analyze-video`, { method: "POST", body: formData })).data;
}
export async function fetchVideoAnalysesApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/security/video-analyses?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchHardwareStatusApi() {
  return (await requestJson(`${API_BASE}/security/hardware/status`)).data;
}
export async function connectHardwareApi(deviceType, config = {}) {
  return (await requestJson(`${API_BASE}/security/hardware/${deviceType}/connect`, {
    method: "POST", body: JSON.stringify({ config })
  })).data;
}
export async function fetchSecurityEventsApi(limit = 100) {
  const res = await requestJson(`${API_BASE}/security/events?limit=${limit}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchInferenceWorkersApi() {
  const res = await requestJson(`${API_BASE}/security/workers`);
  return res.ok && Array.isArray(res.data?.workers) ? res.data.workers : [];
}

// ----------------------------------------------------
// IGL CENTRALIZED AI ARCHITECTURE API
// ----------------------------------------------------
export async function fetchModelHubApi() {
  return (await requestJson(`${API_BASE}/ai/model-hub`)).data;
}
export async function fetchCameraCommandCenterApi() {
  return (await requestJson(`${API_BASE}/ai/cameras`)).data;
}
export async function fetchModelCameraMappingApi() {
  return (await requestJson(`${API_BASE}/ai/model-camera-mapping`)).data;
}
export async function fetchCentralDetectionsApi({ limit = 100, cameraId, eventType } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (cameraId) params.set("camera_id", cameraId);
  if (eventType) params.set("event_type", eventType);
  const res = await requestJson(`${API_BASE}/ai/detections?${params.toString()}`);
  return res.ok && Array.isArray(res.data) ? res.data : [];
}
export async function fetchCameraModeApi(cameraId) {
  return (await requestJson(`${API_BASE}/ai/cameras/${cameraId}/mode`)).data;
}
export async function setCameraModeApi(cameraId, mode) {
  return (await requestJson(`${API_BASE}/ai/cameras/${cameraId}/mode`, {
    method: "PUT", body: JSON.stringify({ mode })
  })).data;
}
export async function setCameraManualModelsApi(cameraId, modelIds) {
  return (await requestJson(`${API_BASE}/ai/cameras/${cameraId}/manual-models`, {
    method: "PUT", body: JSON.stringify({ model_ids: modelIds })
  })).data;
}
export async function reselectCameraModelsApi(cameraId) {
  return (await requestJson(`${API_BASE}/ai/cameras/${cameraId}/reselect`, { method: "POST" })).data;
}
