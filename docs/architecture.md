# Architecture

## Phase 4 Data Path

`Camera/webcam/file/RTSP -> StreamReader -> bounded FrameBuffer -> UltralyticsModelAdapter -> Detection -> IoUTracker -> SafetyEventOrchestrator -> alarm_engine -> physical_alarm`

The alarm stage sits after the safety orchestrator, which persists the event. An alarm is only ever raised from a persisted event whose temporal verification already marked it `CONFIRMED`. The orchestrator calls `alarm_engine.evaluate_alarm_policy` and, only when the policy allows it, `alarm_engine.raise_alarm_for_event`. Nothing upstream of event persistence can create an alarm, and no configuration, threshold or operator action can fabricate a detection.

## Alarm Pipeline Position

`backend/app/services/alarm_engine.py` owns the whole alarm lifecycle. `evaluate_alarm_policy` returns an explicit decision with a reason:

- `EVENT_NOT_CONFIRMED` when the event is not `CONFIRMED`.
- `ALARM_DISABLED_BY_POLICY` when `ALARM_ENABLED` is false.
- `BELOW_MIN_SEVERITY` when the event severity is under `ALARM_MIN_SEVERITY`.
- `COOLDOWN_ACTIVE` when the latest alarm for that event is still inside its cooldown window.
- `REPEAT_LIMIT_REACHED` when raises inside `ALARM_REPEAT_WINDOW_SECONDS` have reached `ALARM_MAX_REPEATS_PER_WINDOW`.
- `CONFIRMED_EVENT_RAISED_ALARM` when a raise is permitted.

Suppression is never a silent drop. `record_suppression` increments `suppression_count` on the still-open alarm and writes a same-state transition row with the reason, so a condition that keeps firing while the board stays quiet is visible to an operator.

`raise_alarm_for_event` is the only function that creates an `alarms` row. When an alarm is already open for the same event it updates `raised_count` and `cooldown_until` on that row instead of creating a duplicate, which is what stops a sustained condition from producing an alarm storm.

## Alarm States And Audit Trail

States are `ACTIVE`, `ACKNOWLEDGED`, `ESCALATED`, `SUPPRESSED`, `EXPIRED`, `CLEARED`. The transition table is:

| From | Allowed to |
| --- | --- |
| `ACTIVE` | `ACKNOWLEDGED`, `ESCALATED`, `CLEARED`, `EXPIRED` |
| `ESCALATED` | `ACKNOWLEDGED`, `CLEARED`, `EXPIRED` |
| `ACKNOWLEDGED` | `CLEARED`, `ESCALATED` |
| `SUPPRESSED` | `ACTIVE` |
| `EXPIRED` | none |
| `CLEARED` | none |

Every change is validated against that table, requires a non-empty reason of at most 2000 characters, and writes an `alarm_state_transitions` row carrying `previous_state`, `new_state`, `reason`, `user_id` and `transitioned_at`. `expire_stale_alarms` moves unacknowledged alarms past `expires_at` to `EXPIRED` with the reason "Alarm expired without acknowledgement", so the board cannot silently fill with stale alarms.

## Alarm Policy

The policy is operator-tunable through environment settings rather than hardcoded:

- `ALARM_ENABLED`: master switch. When false no alarm is raised and health reports `ALARM_DISABLED_BY_POLICY`.
- `ALARM_MIN_SEVERITY`: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`. Events below it never raise.
- `ALARM_COOLDOWN_SECONDS`: minimum spacing between two raises for the same event.
- `ALARM_MAX_REPEATS_PER_WINDOW` with `ALARM_REPEAT_WINDOW_SECONDS`: bounds how often one sustained condition may re-raise.
- `ALARM_AUTO_EXPIRE_SECONDS`: unacknowledged alarms expire.
- `ALARM_AUDIBLE_BROWSER`: gates the operator-controlled browser sound.
- `ALARM_PHYSICAL_ACTUATION`: master switch for hardware actuation.

## Escalation Link

`backend/app/services/escalation_engine.py` ties the event and alarm lifecycles together. When an escalation policy fires for a persisted event, `_escalate_linked_alarms` moves that event's `ACTIVE` alarms to `ESCALATED` with a reason naming the escalation level. Already acknowledged or cleared alarms are left alone. In the other direction, acknowledging an escalation calls `_acknowledge_linked_alarms`, which acknowledges the same event's live alarms in the same action so the board cannot keep sounding after somebody has taken responsibility. Both helpers are wrapped so a session without the alarm subsystem cannot break escalation processing.

## Physical Alarm Architecture

`backend/app/services/physical_alarm.py` drives a relay, siren, GPIO line or industrial controller from a confirmed alarm. Four transports exist, all opt-in and evaluated in this order: `HTTP_ACTUATOR`, `MQTT_ACTUATOR`, `SERIAL_RELAY`, `GPIO_RELAY`. A transport is only considered when its settings are present (`PHYSICAL_ALARM_HTTP_URL`, `PHYSICAL_ALARM_MQTT_HOST` plus `PHYSICAL_ALARM_MQTT_TOPIC`, `PHYSICAL_ALARM_SERIAL_PORT`, `PHYSICAL_ALARM_GPIO_PIN` > 0).

The state vocabulary is deliberately strict:

- `PHYSICAL_ALARM_NOT_CONFIGURED`: `ALARM_PHYSICAL_ACTUATION` is off (reason `ALARM_PHYSICAL_ACTUATION_DISABLED`) or no transport is configured (reason `NO_TRANSPORT_CONFIGURED`).
- `ACTUATOR_NOT_CONNECTED`: a transport is configured but its driver library is absent. The reason names the package: `MQTT_CLIENT_LIBRARY_NOT_INSTALLED`, `PYSERIAL_NOT_INSTALLED`, or `RPI_GPIO_NOT_INSTALLED`.
- `ACTIVATION_ATTEMPTED`: written to the alarm immediately before a real call begins, so an interrupted attempt is visible as an attempt rather than as a success.
- `ACTIVATED`: written only after a real transport call returned success (HTTP 2xx, broker acknowledgement, completed serial pulse, or a driven GPIO line).
- `DEACTIVATED`: a real `DEACTIVATE` call returned success, so the actuator was released.
- `ACTIVATION_FAILED` / `DEACTIVATION_FAILED`: a real attempt that did not succeed.
- `NOT_ATTEMPTED`: returned with transport `NONE` and reason `UNSUPPORTED_ACTION` when `actuate` is called with an action other than `ACTIVATE` or `DEACTIVATE`.

`raise_alarm_for_event` performs the actuation itself when `ALARM_PHYSICAL_ACTUATION` is on and at least one transport is configured, so an alarm and its physical notification cannot drift apart. With no actuator configured the alarm keeps `PHYSICAL_ALARM_NOT_CONFIGURED` and no call is made.

A serial relay is asserted for `PHYSICAL_ALARM_SERIAL_PULSE_SECONDS` and then released, so a momentary siren actually sounds; a `DEACTIVATE` writes the release level only.

`actuator_status()` reports `hardware_verified: false` in every state the process can currently reach, and the operator test endpoint returns `hardware_verified` true only when the state is `ACTIVATED` and status agrees. Nothing in this module can report a siren sounding when no siren is wired.

The outgoing payload is built from real alarm facts only: `action`, `source`, `alarm_id`, `event_id`, `incident_id`, `camera_id`, `zone_id`, `severity`, `detector_key`, `confidence`, `model_name`, `model_version`, `raised_at`. Transport failures are caught and returned as a state, never raised into the safety path.

## Model Management

`ai_models/ultralytics_adapter.py` loads only an explicitly configured local file. Three checkpoint formats are supported through the same adapter: `.pt` (`ULTRALYTICS_PYTHON`), `.onnx` (`ULTRALYTICS_ONNX`) and `.engine` (`ULTRALYTICS_TENSORRT`). Presence of the required distributions is checked, never installed; a format whose runtime is absent is reported as `MODEL_RUNTIME_DEPENDENCY_MISSING` with `missing_runtime_packages` naming them (`ultralytics`+`torch`, `ultralytics`+`onnxruntime`, `ultralytics`+`tensorrt`).

`health()` reports framework, `weights_path`, `status`, `available`, `model_name`, `model_version`, `weights_checksum_sha256`, `classes`, `confidence_threshold`, `device`, `inference_fps`, `inference_fps_samples`, `latency_ms`, `average_inference_latency_ms`, inference counters, `missing_runtime_packages` and `validation_state`. Two rules matter: `inference_fps` stays `None` until at least two real predictions have completed, because a rate from a single call would be an invention; and `validation_state` is always `NOT_VALIDATED` from this code, because loading and timing a model is not validating it.

`discover_local_models` scans `MODEL_SEARCH_PATHS` to `MODEL_SEARCH_MAX_DEPTH` for `.pt`, `.onnx` and `.engine` files that already exist on the machine. It is read-only: each entry reports the canonical path, framework, real size, modification time, real SHA-256, `readable`, `activation_state: NOT_ACTIVATED` and the discovery note. It never downloads weights, never imports a runtime, and never activates a candidate. `MODEL_AUTO_DISCOVER` requires at least one search path; without it discovery is not permitted. A model runs only when `MODEL_WEIGHTS_PATH` names it.

`scripts/validate_model.py` validates one artifact with no video source. The seven checks are reported independently and an unreached check is `NOT_RUN`, never a pass. The throughput probe feeds a deterministic blank frame and labels itself `SYNTHETIC_PROBE_NOT_REAL_FOOTAGE` throughout; exit codes are 0 (all checks passed), 1 (a check ran and failed) and 2 (no check could run, for example `MODEL_NOT_CONFIGURED`). The report always carries `validation_state: NOT_VALIDATED`, `detection_quality_validated: false` and `igl_validated: false`.

## Safety Engine

`backend/app/services/safety_engine.py` holds an explicit detector catalogue. `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON` and `PHONE` are `IMPLEMENTED`; `HELMET`, `PPE`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR` are `NOT_IMPLEMENTED` with a stated reason, and they evaluate nothing. The PPE-family reason is that compliance is an absence claim, and a detector that can see a hard hat cannot prove one is missing. A class-gated detector is operational only when the loaded model itself exposes the required class in its own class list; otherwise it reports `MODEL_CLASS_NOT_AVAILABLE`.

Every per-detector parameter is honoured end to end: `minimum_confidence` (also accepted as `confidence_threshold` or `confidence_floor`), `minimum_observations`, `minimum_duration_seconds`, `maximum_gap_seconds`, `debounce_seconds`, `cooldown_seconds`, `severity`, `zone_scoping`, `evidence_policy` (`CAPTURE` or `SKIP`; an unrecognised value falls back to `CAPTURE` so a typo cannot silently stop evidence) and `correlation_window_seconds`. The effective values, and the provenance of each one, are exposed through `GET /api/v1/system/detectors`; anything unconfigured is tagged `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION` rather than presented as an IGL-validated operating value.

Debounce and cooldown are keyed on a tracked subject. A frame-level detector with no track is governed by correlation instead, because there is no subject to key on.

## Notification Templates

`backend/app/services/notification_templates.py` renders operator-reviewable plain text. The substitutable set is a fixed frozenset: `event_id`, `event_type`, `severity`, `state`, `workflow_state`, `camera_id`, `zone_id`, `confidence`, `started_at`, `detector_key`, `model_name`, `model_version`, `incident_id`, `alarm_id`, `notes`. The context is built by `event_context` from one real `Event` row and its relationships only; nothing is read from the environment, so no setting value can reach a template.

A name outside the set is left in the text exactly as written and named in `not_substituted`, even when a caller puts a matching key in the context mapping. A known placeholder with no value renders its documented sentinel instead of a plausible substitute: `NOT_MEASURED` for `confidence`, `NOT_AVAILABLE` for the rest. Substitution is a single pass, so a substituted value containing braces is never re-scanned. Templates are plain text so an operator can review exactly what will leave the plant network.

`message_kind` decides between `INCIDENT` and `ESCALATION` from whether the event has a persisted escalation. No policy is inferred and no kind is invented.

## Automated Event Source

Camera-health monitoring can write `CAMERA_FAILURE` events with `observation_state=NOT_ASSESSABLE`, no confidence and no duration; these report source availability, not a safety hazard. When an implemented detector has real model output and operator-supplied configuration, `SafetyEventOrchestrator` applies temporal policy, persists event provenance, captures evidence from the same frame, correlates persisted events, evaluates notification and escalation policy, and finally evaluates the alarm policy. No safety event is produced by configuration alone.

## Event And Incident Lifecycle

The event workflow adds `ESCALATED` and `CANCELLED` to the earlier states. `ESCALATED` means the event was raised past normal handling and can still be acknowledged and worked. `CANCELLED` is terminal, always carries a reason, stamps `ended_at`, and is excluded from open-event reuse, so a withdrawn condition is never reopened as the same record. Illegal transitions are rejected and every transition is audited.

The incident lifecycle gained the same pair: `OPEN` and `INVESTIGATING` may reach `ESCALATED` or `CANCELLED`, `ESCALATED` may return to `INVESTIGATING`, `RESOLVED` or `CANCELLED`, and `CANCELLED` is terminal. The event read API exposes `incident_ids` derived from real `Incident` rows, which is what the Workers view joins on.

## Health Semantics

Health output reports observation, never assumption. The application process being up is `APPLICATION_UP` and is separate from `DATABASE_OK` / `DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT` / `MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED` / `MODEL_NOT_CONFIGURED`, and `NO_CAMERA` / `CAMERA_CONFIGURED_NOT_OBSERVED` / `CAMERA_AVAILABLE` / `CAMERA_RECORDED_BUT_NONE_ACTIVE`. A pipeline is reported as `RUNNING` only when at least one inference actually completed.

`collect_system_health` adds measured uptime (`uptime_seconds`, `uptime_human`, `process_started_at`, `uptime_state` which is `MEASURED` or `UPTIME_UNAVAILABLE`), measured disk health (`disk_state` of `DISK_OK` / `DISK_FREE_SPACE_BELOW_THRESHOLD` / `DISK_UNAVAILABLE`, plus `disk_path`, `disk_total_bytes`, `disk_free_bytes`, `disk_free_percent`, `disk_min_free_bytes`), `email_transport` and `whatsapp_transport` configuration state with explicit notes that configuration is not delivery, `alarm_subsystem` (`ALARM_READY_EVENT_DRIVEN` or `ALARM_DISABLED_BY_POLICY`), `physical_alarm_state` and `physical_alarm_transports`, and measured performance.

Measured performance is `MEASURED_FROM_OBSERVED_FRAMES` with real `camera_fps` and `inference_fps`, or `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES` with both fields `null`. No FPS is reported without an observed frame. Camera health rows store `NULL` rather than `0.0` for telemetry that has not been measured; migration `9c4b2e7a1d55` performs that change.

## Camera Reopen And Operator Stop

`StreamReader` declares `REOPENING` and `REOPENED` alongside `NOT_STARTED`, `STARTING`, `RUNNING`, `END_OF_FILE`, `SOURCE_UNAVAILABLE`, `DISCONNECTED`, `RECONNECT_EXHAUSTED` and `STOPPED`. `REOPENING` means the previous source is being torn down. `REOPENED` means the capture handle was re-established but no frame from the new session has been observed, so it is deliberately not `RUNNING`; the first real frame moves it to `RUNNING`. `reconnect_attempts` and `reconnect_state` (`IDLE`, `BACKOFF`, `RECONNECT_EXHAUSTED`) are derived from what the reader is doing, not from a status string.

A webcam may request a preferred capture backend (`DSHOW`, `MSMF`, `ANY`). A preference only reorders the probe; it never removes a working option, and the backend that actually opened the capture is reported in `source_backend`.

An operator stop is persisted outside the database in `camera_operator_stops.json`, beside the configured database file (falling back to `data/`). The marker holds a camera id and an ISO timestamp and nothing else: no stream URL, no credential. A corrupt or missing marker yields an empty set rather than refusing to start, because losing a record of an intentional stop is bad but refusing to start is worse. The camera health worker skips a camera with a recorded operator stop, evaluating health without dispatching events, and a backend shutdown deliberately does not erase the markers. Only an operator start clears the intent.

## Schema Ownership

The database schema is owned by the Alembic migration chain. No migration calls `Base.metadata.create_all()` or `drop_all()`, and `init_db()` raises rather than creating schema at runtime. Application startup compares the configured database revision against the script heads and refuses to serve when they differ. `backend/tests/conftest.py` migrates a temporary database so the test suite never depends on a deployment's data. Migration `b2a5c8e4f701` adds the alarm subsystem (`alarms` and `alarm_state_transitions`) and is tested for upgrade, downgrade and re-upgrade.

## Configuration And Validation Boundaries

- **IMPLEMENTED:** Bounded frame buffering, local model adapter with checksum reporting and local discovery, detection structure, IoU track association and persistence, camera-to-pipeline wiring, continuous camera-health worker, audited event and alarm transitions, response lifecycles, migration-owned schema, JWT authentication/bootstrap, permission checks, notification templates, and derived health semantics.
- **IMPLEMENTED, NOT OPERATIONAL:** The fire/smoke/restricted-zone/person/phone event path is code-connected but blocked at runtime by missing weights and IGL configuration.
- **NOT_IMPLEMENTED:** Helmet, PPE, safety-vest, proximity, fall, leakage and unsafe-behaviour detection.
- **IMPLEMENTED, NOT COMMISSIONED:** Physical alarm actuation exists as four opt-in transports and reports `PHYSICAL_ALARM_NOT_CONFIGURED` until real hardware answers.
- **NOT_CONFIGURED:** Model weights are absent; model health reports `MODEL_NOT_CONFIGURED`. No SMTP, WhatsApp Cloud API or physical actuator credentials exist here.
- **NOT_VALIDATED:** No IGL layouts, cameras, SOPs or labelled IGL data were supplied or assessed. Production deployment topology was not exercised.
- **BLOCKED_BY_EXTERNAL_DEPENDENCY:** Model inference, live detection, tracking, event, incident and live-alarm validation require an authorised compatible model checkpoint. IGL validation additionally requires authorised plant layout/SOP data and labelled IGL footage.

The system is advisory only. No safety instrumented or operational control systems are called.
