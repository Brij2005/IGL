# API Surface

All application routes are under `/api/v1`. `POST /auth/login` issues short-lived signed bearer tokens; `GET /auth/me` resolves the authenticated account and `POST /auth/password` changes its password and invalidates prior tokens. `ALLOW_ANONYMOUS_ACCESS=true` is a development-only mode; production/staging require JWT auth. Every response carries a sanitized `X-Request-ID` header.

Permission and role names below are the values the code enforces. `require_permission` admits a role whose `permissions_json` contains the name; `require_role` admits only the listed roles. In explicitly enabled anonymous development mode the actor is `None` and a write is recorded with a NULL actor.

## Alarm Centre

Mounted at `/api/v1/alarms` (`backend/app/api/alarms.py`). Every response is derived from persisted `alarms` rows. The API never creates an alarm except through the operator re-raise route, which still requires a `CONFIRMED` event.

### `GET /alarms` — permission `events:view`

Alarm history, newest first. Query parameters: `state` (exact state match, max 30 characters), `active_only` (boolean, default `false`; returns only `ACTIVE` and `ESCALATED` rows and ignores `offset`), `limit` (1–500, default 100), `offset` (>= 0, default 0).

Each element of the list is `AlarmOut`:

```json
{
  "id": "str",
  "event_id": "str",
  "camera_id": "str|null",
  "zone_id": "str|null",
  "track_id": "str|null",
  "incident_id": "str|null",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "state": "ACTIVE|ACKNOWLEDGED|ESCALATED|SUPPRESSED|EXPIRED|CLEARED",
  "detector_key": "str|null",
  "confidence": 0.0,
  "model_name": "str|null",
  "model_version": "str|null",
  "reason": "str|null",
  "raised_count": 1,
  "suppression_count": 0,
  "cooldown_until": "iso8601|null",
  "raised_at": "iso8601",
  "acknowledged_at": "iso8601|null",
  "acknowledged_by_user_id": "str|null",
  "cleared_at": "iso8601|null",
  "expires_at": "iso8601|null",
  "physical_state": "PHYSICAL_ALARM_NOT_CONFIGURED",
  "physical_result": "str|null",
  "physical_activated_at": "iso8601|null",
  "physical_cleared_at": "iso8601|null",
  "allowed_transitions": ["ACKNOWLEDGED", "CLEARED", "ESCALATED", "EXPIRED"]
}
```

`allowed_transitions` is computed from the alarm's current state, so a client never has to infer it. `404` is not used here; an empty list is an empty list.

### `GET /alarms/policy` — permission `events:view`

The live policy, counters and physical actuator state. It first expires any alarm past `expires_at`, so the counters reflect the current board. Response is `AlarmPolicyOut`:

```json
{
  "alarm_policy_enabled": true,
  "min_severity": "MEDIUM",
  "cooldown_seconds": 120.0,
  "max_repeats_per_window": 3,
  "repeat_window_seconds": 900.0,
  "auto_expire_seconds": 1800.0,
  "audible_browser_alarm": true,
  "total_alarms": 0,
  "active_alarm_count": 0,
  "escalated_alarm_count": 0,
  "acknowledged_alarm_count": 0,
  "suppressed_alarm_count": 0,
  "expired_alarm_count": 0,
  "cleared_alarm_count": 0,
  "last_alarm_id": null,
  "last_alarm_raised_at": null,
  "physical_alarm": { "...": "see GET /alarms/physical/status" }
}
```

Observed on this machine: `alarm_policy_enabled` true, `min_severity` `MEDIUM`, `cooldown_seconds` 120, `max_repeats_per_window` 3, and all counters 0, because no confirmed event has ever been produced here. `GET /alarms` returned an empty list.

### `GET /alarms/physical/status` — permission `events:view`

Reports what is configured without claiming anything is connected. No database access.

```json
{
  "state": "PHYSICAL_ALARM_NOT_CONFIGURED",
  "enabled": false,
  "configured_transports": [],
  "reason": "ALARM_PHYSICAL_ACTUATION is disabled",
  "hardware_verified": false
}
```

With `ALARM_PHYSICAL_ACTUATION=true` but no transport set, `reason` is "No physical alarm transport is configured". With a transport configured, `state` is `ACTUATOR_NOT_CONNECTED`, `reason` is `TRANSPORT_CONFIGURED_HARDWARE_NOT_VERIFIED`, and `hardware_verified` is still `false`. `configured_transports` lists only the transports an operator has actually configured, from `HTTP_ACTUATOR`, `MQTT_ACTUATOR`, `SERIAL_RELAY`, `GPIO_RELAY`.

### `POST /alarms/physical/test` — role `ADMIN` or `SAFETY_OFFICER`

Attempts one real physical actuation for a test. This is deliberately not a simulation. It writes an audit record `PHYSICAL_ALARM_TEST_ATTEMPTED` with the state and transport. Request body: none. Response is `PhysicalAlarmTestResult`:

```json
{
  "state": "PHYSICAL_ALARM_NOT_CONFIGURED",
  "transport": "NONE",
  "reason": "ALARM_PHYSICAL_ACTUATION_DISABLED",
  "hardware_verified": false
}
```

Other reachable states: `ACTUATOR_NOT_CONNECTED` with the missing dependency named (`MQTT_CLIENT_LIBRARY_NOT_INSTALLED`, `PYSERIAL_NOT_INSTALLED`, `RPI_GPIO_NOT_INSTALLED`), `ACTIVATED` only after a real successful transport call, and `ACTIVATION_FAILED` with a reason such as `HTTP_TIMEOUT`, `PROVIDER_HTTP_500`, `BROKER_ACK_TIMEOUT`, `SERIAL_SERIALEXCEPTION` or `GPIO_RUNTIMEERROR`. `hardware_verified` is true only when the state is `ACTIVATED` and the status endpoint agrees. Observed on this machine: `PHYSICAL_ALARM_NOT_CONFIGURED`, `hardware_verified=false`. No actuator is connected to this laptop and nothing was activated.

### `GET /alarms/{alarm_id}` — permission `events:view`

One `AlarmOut`. `404` with detail "Alarm not found" when the id does not exist.

### `GET /alarms/{alarm_id}/history` — permission `events:view`

The audited transition history, oldest first. `404` when the alarm does not exist. Each element:

```json
{
  "previous_state": "str|null",
  "new_state": "str",
  "reason": "str",
  "user_id": "str|null",
  "transitioned_at": "iso8601"
}
```

`user_id` is null for a machine transition such as expiry, and `previous_state` is null on the initial `ACTIVE` row.

### `POST /alarms/{alarm_id}/acknowledge` — role `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`, `SUPERVISOR`

Body: `{"notes": "str|null"}`, at most 2000 characters. Returns the updated `AlarmOut`. A missing `notes` falls back to the reason "Alarm acknowledged by operator". Writes audit record `ALARM_ACKNOWLEDGED` with the event id and notes. `409` when the alarm is not in `ACTIVE` or `ESCALATED`; `422` for an invalid transition; `404` when the alarm does not exist. Viewers cannot acknowledge.

### `POST /alarms/{alarm_id}/escalate` — role `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`

`reason` is a required query parameter, 1–2000 characters, for example `POST /alarms/{id}/escalate?reason=Area%20unattended`. Returns the updated `AlarmOut`. Writes audit record `ALARM_ESCALATED`. `409` when the alarm is not `ACTIVE`; `404` when it does not exist. A missing `reason` is a request-validation error (`422`).

### `POST /alarms/{alarm_id}/clear` — role `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`, `SUPERVISOR`

Body:

```json
{ "reason": "str (required, 1-2000)", "deactivate_physical": true }
```

Returns the updated `AlarmOut`. When `deactivate_physical` is true and the alarm's `physical_state` is `ACTIVATED`, a real `DEACTIVATE` call is attempted; success writes `physical_state: DEACTIVATED` and stamps `physical_cleared_at`, while a failure writes `DEACTIVATION_FAILED` and leaves `physical_cleared_at` null so the actuator is still known to be asserted. Writes audit record `ALARM_CLEARED` with the reason. `409` when the alarm is not `ACTIVE`, `ESCALATED` or `ACKNOWLEDGED`.

### `POST /alarms/events/{event_id}/raise` — role `ADMIN` or `SAFETY_OFFICER`

Body: `{"reason": "str (required, 1-2000)"}`. Evaluates the alarm policy against an existing event and raises an alarm only if the policy allows it. This never fabricates an alarm: the event must exist and must already be `CONFIRMED`, and the policy can still suppress the raise. Returns the created `AlarmOut`. `404` when the event does not exist. `409` with detail `Alarm not raised: <policy reason>` when the policy refuses; the policy reason is one of `EVENT_NOT_CONFIRMED`, `ALARM_DISABLED_BY_POLICY`, `BELOW_MIN_SEVERITY`, `COOLDOWN_ACTIVE`, `REPEAT_LIMIT_REACHED`. Suppressed attempts write audit record `ALARM_RAISE_SUPPRESSED`; successful ones write `ALARM_RAISED_BY_OPERATOR`.

### Alarm lifecycle summary

| State | Meaning |
| --- | --- |
| `ACTIVE` | Raised from a confirmed event and awaiting an operator. |
| `ESCALATED` | Moved by the escalation worker or an operator; still actionable. |
| `ACKNOWLEDGED` | An operator took responsibility. |
| `SUPPRESSED` | Recorded by the suppression path; may return to `ACTIVE`. |
| `EXPIRED` | Auto-expired without acknowledgement. Terminal. |
| `CLEARED` | Cleared by an operator. Terminal. |

## System Health

### `GET /system/health`

No permission required. Returns the full `collect_system_health()` document. Fields beyond the pre-existing ones:

- `uptime_seconds`, `uptime_human`, `process_started_at`, `uptime_state` (`MEASURED` or `UPTIME_UNAVAILABLE`).
- `disk_state` (`DISK_OK`, `DISK_FREE_SPACE_BELOW_THRESHOLD`, `DISK_UNAVAILABLE`), `disk_path`, `disk_total_bytes`, `disk_free_bytes`, `disk_free_percent`, `disk_min_free_bytes`. The volume is measured with `shutil.disk_usage` on `HEALTH_DISK_PATH` or `EVIDENCE_DIR`; an unmeasurable volume is `DISK_UNAVAILABLE`, never a healthy guess. `DISK_FREE_SPACE_BELOW_THRESHOLD` is added to `degraded_reasons`.
- `email_transport` (`EMAIL_CONFIGURED` / `EMAIL_NOT_CONFIGURED`) with `email_transport_note`, and `whatsapp_transport` (`WHATSAPP_CONFIGURED` / `WHATSAPP_NOT_CONFIGURED`) with `whatsapp_transport_note`. Both notes state that this is configuration only and that delivery is reported per notification from the provider response.
- `alarm_subsystem` (`ALARM_READY_EVENT_DRIVEN` or `ALARM_DISABLED_BY_POLICY`), `physical_alarm_state`, `physical_alarm_transports`.
- `measured_performance` (`MEASURED_FROM_OBSERVED_FRAMES` or `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`) with `camera_fps` and `inference_fps`. Both are `null` when no frame has been observed.
- `model`, the adapter health document described under `GET /system/ai-health`.
- `validation_status` is always `NOT_VALIDATED` and `igl_validated` is always `false`.

### `GET /system/detectors` — permission `cameras:view`

Reports per-detector implementation state, model availability, operator configuration, and the effective rule configuration. It is the endpoint that separates "not implemented" from "not configured":

```json
{
  "model_status": "MODEL_NOT_CONFIGURED",
  "model_classes_available": 0,
  "detectors": [
    {
      "detector_key": "PERSON",
      "implementation_state": "IMPLEMENTED",
      "availability_state": "MODEL_NOT_CONFIGURED",
      "operational": false,
      "reason": "No usable model is loaded (model state MODEL_NOT_CONFIGURED); no detection can be produced",
      "requirement": "A person class present in the loaded model's classes",
      "configured": false,
      "enabled": false,
      "required_classes": ["person"],
      "available_model_classes": [],
      "detection_capability": "CLASS_PRESENCE_IN_FRAME",
      "validation_state": "NOT_CONFIGURED",
      "configuration": { "...": "effective rule values with per-parameter provenance" },
      "health": { "...": "the model health the decision was made from" }
    }
  ],
  "operational_detector_count": 0,
  "igl_validation_status": "NOT_VALIDATED"
}
```

`implementation_state` is `IMPLEMENTED` or `NOT_IMPLEMENTED`. `availability_state` is one of `NOT_AVAILABLE`, `MODEL_NOT_CONFIGURED`, `MODEL_CLASS_NOT_AVAILABLE`, `NOT_CONFIGURED` or `READY`. `operational` is true only when the build implements the detector, the loaded model supports it, and an operator has enabled a configuration for it. `configuration` carries the effective `minimum_confidence`, `minimum_observations`, `minimum_duration_seconds`, `maximum_gap_seconds`, `debounce_seconds`, `cooldown_seconds`, `severity`, `zone_scoping`, `evidence_policy`, `correlation_window_seconds` plus a `sources` map marking each value as operator-configured or `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`.

Observed on this machine: `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON` and `PHONE` reported `IMPLEMENTED` with `MODEL_NOT_CONFIGURED`; `PPE`, `HELMET`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR` reported `NOT_IMPLEMENTED` with `NOT_AVAILABLE`. `operational_detector_count` was 0. Nothing is operational.

### `GET /system/ai-health`, `GET /system/pipelines`

`GET /system/ai-health` returns the adapter health document under `model`, the pipeline list under `pipelines`, and `igl_validation_status: NOT_VALIDATED`. The `model` document reports `framework`, `weights_path`, `status`, `available`, `weights_loaded`, `model_name`, `model_version`, `weights_checksum_sha256`, `classes`, `confidence_threshold`, `device`, `inference_fps`, `inference_fps_samples`, `latency_ms`, `inference_latency_ms`, `average_inference_latency_ms`, `inference_count`, `inference_attempt_count`, `inference_failure_count`, `missing_runtime_packages` and `validation_state`. `inference_fps` is `null` until two real predictions have completed, and `validation_state` is always `NOT_VALIDATED`. A `.onnx` or `.engine` checkpoint whose runtime is absent reports `status: MODEL_RUNTIME_DEPENDENCY_MISSING` with `missing_runtime_packages` naming the package.

## Camera Status

### `GET /cameras/{camera_id}/status` — permission `cameras:view`
### `POST /cameras/{camera_id}/start` — role `ADMIN` or `SAFETY_OFFICER`
### `POST /cameras/{camera_id}/stop` — role `ADMIN` or `SAFETY_OFFICER`

All three return the same `WebcamStatusOut` shape:

```json
{
  "camera_id": "str",
  "camera_name": "str|null",
  "source_type": "WEBCAM|RTSP|FILE|UNKNOWN",
  "stream_state": "NOT_STARTED|STARTING|REOPENING|REOPENED|RUNNING|END_OF_FILE|SOURCE_UNAVAILABLE|DISCONNECTED|RECONNECT_EXHAUSTED|STOPPED",
  "running": false,
  "is_connected": false,
  "device_index": 0,
  "source_backend": "DSHOW",
  "observed_frames": false,
  "total_frames_read": 165,
  "measured_fps": 8.03,
  "dropped_frames": 0,
  "decode_failures": 0,
  "reconnects": 0,
  "reconnect_attempts": 0,
  "reconnect_state": "IDLE",
  "reopen_in_progress": false,
  "last_frame_timestamp": "iso8601|null",
  "last_frame_interval_ms": 125.0,
  "resolution": [640, 480],
  "source_reported_fps": 30.0,
  "target_fps": 15.0,
  "inference_state": "NOT_RUNNING",
  "detections_observed": 0,
  "last_error": null,
  "model_state": "MODEL_NOT_CONFIGURED",
  "health": { "...": "last recorded camera health observation" }
}
```

`stream_state` includes the explicit `REOPENING` and `REOPENED` states. `REOPENED` means the capture handle is open but no frame from the new session has been observed, so `running` stays false until the first real frame arrives. `reconnect_attempts` counts open reconnect attempts and `reconnect_state` (`IDLE`, `BACKOFF`, `RECONNECT_EXHAUSTED`) says what the reader is doing about them right now. `reopen_in_progress` mirrors the reader's reopen flag. `measured_fps` is `null` when no frame has been observed; it is never zero. `source_backend` is the backend that actually opened the capture, not the one requested.

Observed on this machine: the registered "Laptop Webcam" started through `DSHOW`, reached `RUNNING` with `total_frames_read=165`, `measured_fps=8.03`, `reconnect_state=IDLE`, and `inference_state=NOT_RUNNING` with `detections_observed=0`. Stop returned `stream_state=NOT_STARTED`; the camera stayed stopped with 0 frames at +8 s, +18 s and +28 s; reopening returned to `RUNNING` with 67 frames; a final stop returned `NOT_STARTED`.

### `POST /cameras/webcam/register` — role `ADMIN` or `SAFETY_OFFICER`

Body fields: `device_index` (0–5), `width`, `height`, `fps`, `backend` (optional `DSHOW` / `MSMF` / `ANY`), `name`, `code`, `zone_id`, `location_description`. The device is probed with a real frame read before anything is written; if no frame can be read the response is `409` naming the reason. `backend` only reorders the probe; the audit record `WEBCAM_CAMERA_REGISTERED` records both the requested backend and the one that actually opened the device.

### `GET /cameras/webcam/devices` — permission `cameras:view`

Probes device indices and reports only what it can prove. `discovery_state` is `AVAILABLE` or `NO_WEBCAM_DETECTED`, with `probe_limit` recording how far probing went. On this machine it found 6 probe indices with device 0 reporting 640x480.

## Notifications

### `POST /notifications/test/email` and `POST /notifications/test/whatsapp` — role `ADMIN` or `SAFETY_OFFICER`

Body: `{"recipient": "str (required, 3-255)"}`. Sends a real one-off message. Response is `TestDeliveryResult`:

```json
{
  "channel": "EMAIL",
  "status": "EMAIL_NOT_CONFIGURED",
  "delivery_status": "EMAIL_NOT_CONFIGURED",
  "provider": "SMTP",
  "recipient": "operator@example.com",
  "message_id": null,
  "error": "SMTP host and sender address are required"
}
```

`status` is the transport result; `delivery_status` is the external operator-facing vocabulary. The mapping is `EMAIL_DELIVERED` / `EMAIL_DELIVERY_FAILED` / `EMAIL_NOT_CONFIGURED` / `EMAIL_NOT_ATTEMPTED` / `EMAIL_PENDING` / `EMAIL_STATUS_UNKNOWN`, and the same for `WHATSAPP_*`. No field is ever populated from an assumption. The persisted `notifications.status` values are unchanged (`QUEUED`, `SENT`, `RETRYING`, `FAILED`, `NOT_CONFIGURED`, `NOT_IMPLEMENTED`); only the delivery vocabulary is added.

Observed on this machine: the email test send returned `status=EMAIL_NOT_CONFIGURED`, `delivery_status=EMAIL_NOT_CONFIGURED`, reason "SMTP host and sender address are required". The WhatsApp test send returned `status=WHATSAPP_NOT_CONFIGURED`, `delivery_status=WHATSAPP_NOT_CONFIGURED`, reason "Cloud API token, phone number ID and API version are required". No SMTP or Cloud API credentials exist here, so no message was delivered.

### `GET /notifications/channels/status` — permission `events:view`

Per channel: `channel`, `configuration_state` (`CONFIGURED`, `NOT_CONFIGURED`, `NOT_IMPLEMENTED`), `delivery_status` (from the newest persisted row only), `last_delivery_status` (from the newest row that was actually attempted, `null` when none was), and the `queued` / `sent` / `failed` / `not_configured` / `retrying` counts. A configured channel is never shown as delivered on the strength of its settings alone.

### Other notification routes

`GET /notifications` lists the queue (`events:view`); `POST /notifications/events/{event_id}` creates a queue record (role `ADMIN` or `SAFETY_OFFICER`); `GET /notifications/delivery-queue` lists work waiting to be sent, which is not a report of what was delivered. A background worker delivers configured `EMAIL` and `WHATSAPP` records with bounded retries; `WEBHOOK`, `SMS`, `TEAMS` and `BUZZER` remain `NOT_IMPLEMENTED`. Provider acceptance is recorded as `SENT`, not as proof of inbox or recipient receipt.

## Events

`GET /events` reads persisted events. Each row includes `track_id`, `track_uuid`, `evidence_count`, `correlation_keys`, `incident_ids` (derived from real `Incident` rows, empty when none), `allowed_transitions` for the current workflow state, and `provenance`. `allowed_transitions` now includes `ESCALATED` and `CANCELLED` where the state permits them: `NEW` may go to `UNACKNOWLEDGED` or `CANCELLED`; `UNACKNOWLEDGED` and `ACKNOWLEDGED` may also go to `ESCALATED`; `ESCALATED` may go to `ACKNOWLEDGED` or `CANCELLED`; `CANCELLED` and `CLOSED` are terminal. A `CANCELLED` event always carries a reason, is stamped with `ended_at`, and is excluded from open-event reuse.

`POST /events/{event_id}/transitions` applies only allowed transitions and records actor and reason history. Illegal transitions return `409`. `GET/POST /events/{event_id}/acknowledgements` records an acknowledgement and advances the event in one transaction; repeating it returns `409`. An event in `ESCALATED` can still be acknowledged. `GET/POST /events/{event_id}/assignments` assigns an acknowledged event to an active user; assigning an inactive user or an event that is not `ACKNOWLEDGED` returns `409`.

## Response Lifecycles

`GET /incidents`, `GET /incidents/{incident_id}`, and `POST /incidents/{incident_id}/transitions` manage the incident lifecycle; `GET /incidents/{incident_id}/transitions` returns its history. `OPEN` and `INVESTIGATING` may reach `ESCALATED` or `CANCELLED`, `ESCALATED` may return to `INVESTIGATING` or `RESOLVED`, and `CANCELLED` is terminal. `GET/POST /near-misses` and `POST /near-misses/{near_miss_id}/transitions` manage the near-miss lifecycle. `GET/POST /corrective-actions` and `POST /corrective-actions/{action_id}/transitions` manage corrective actions; a corrective action must name exactly one existing parent (`event_id`, `incident_id`, or `near_miss_id`).

`GET /lifecycle-states` publishes the declared state machine for every response entity, including the alarm states, so a client never has to infer allowed states.

## Other Implemented Routes

- `GET/POST /identity/users`, identity role/state updates, and `GET /identity/audit-logs`. New users can receive an account password; password hashes are never returned. Alarm actions appear in this audit log as `ALARM_ACKNOWLEDGED`, `ALARM_ESCALATED`, `ALARM_CLEARED`, `ALARM_RAISED_BY_OPERATOR`, `ALARM_RAISE_SUPPRESSED` and `PHYSICAL_ALARM_TEST_ATTEMPTED`.
- `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{camera_id}`, `GET /cameras/{camera_id}/health`, and `POST /cameras/{camera_id}/health/evaluate` (permission `cameras:view_live`). The read-only health route never evaluates the source, never writes a row and never creates a `CAMERA_FAILURE` event; the explicit evaluate route can and is audited.
- `GET /cameras/{camera_id}/preview.mjpg` streams buffered real captures and emits an `X-Frame-State: NO_FRAME_OBSERVED` comment line instead of an image when nothing has been observed. `GET /cameras/{camera_id}/snapshot.jpg` returns `404 EVIDENCE_NOT_AVAILABLE` when no frame has been observed.
- `GET /events/{event_id}/evidence` lists evidence linked to a persisted event; `GET /events/{event_id}/evidence/{evidence_id}/content` serves an existing in-root file only.
- `GET/POST /configuration/plants`, `/configuration/areas`, `/configuration/zones`, and `/configuration/ppe-rules` provide operator-supplied configuration. Detector rules, safety rules, thresholds, escalation policies and notification policies are also under `/configuration/*`. Writes are audited with a NULL actor when anonymous. Validation claims cannot be submitted through safety-rule or threshold creation.
- `GET /analytics/summary` returns counts from database records and `NOT_MEASURED` accuracy state.
- `GET /workers/tracks` returns persisted visual tracking sessions, the latest persisted detection, camera/zone, associated active event records, and a timestamp-derived freshness state. Supports `recent_only`, `recent_within_seconds`, `camera_id`, `limit`, `offset`. Track IDs are camera-local visual sessions and are never resolved to employee identities. `helmet_status`, `ppe_status` and `phone_status` are always `UNKNOWN` because no implemented detector can produce an absence verdict. The Workers view joins linked incidents client-side from `GET /events` `incident_ids` matched on `track_uuid`; the track endpoint itself does not return incident ids. On this machine the endpoint returned 0 records.

## Root

`GET /` is a derived health summary, not evidence of real input or AI validation. It reports `APPLICATION_UP` separately from `DATABASE_OK` / `DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT` / `MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED` / `MODEL_NOT_CONFIGURED`, and the camera states, plus `alarm_subsystem`, `physical_alarm_state`, `physical_alarm_transports`, `email_transport`, `whatsapp_transport`, `uptime_seconds` / `uptime_human` / `uptime_state`, `disk_state` and `disk_free_percent`, `measured_performance` / `camera_fps` / `inference_fps`, `access_control`, `authentication`, `validation_status`, `igl_validated` (always `false`) and `igl_configuration_status`. The byte counts `disk_total_bytes` and `disk_free_bytes` are on `GET /api/v1/system/health` only, not on the root summary. The process being up is never reported as health. On this machine `overall_status` was `DEGRADED` with `MODEL_NOT_CONFIGURED` in `degraded_reasons`.

## Response Safety

- No response model includes a password hash.
- Camera responses never include URL user information, query parameters or fragments. An unparseable source is reduced to its final path segment.
- `detail` fields never carry a stream URL or credential.
- Physical alarm status never echoes a configured URL, token or broker password.

## Not Implemented

There is no WebSocket and no complete user-administration UI. Helmet, PPE, safety-vest, proximity, fall, leakage and unsafe-behaviour detectors are not implemented. Fire/smoke, restricted-zone, person and phone evaluation are conditionally implemented but cannot run until a compatible model and authorised configuration are supplied. MFA, password recovery and centralised rate limiting are not implemented. Webhook, SMS, Teams and buzzer notification channels are not implemented. Camera failures remain separate `NOT_ASSESSABLE` system observations. The workers endpoint exposes only real persisted track/detection/event records; it does not claim continuous live presence when a recent track record is absent.
