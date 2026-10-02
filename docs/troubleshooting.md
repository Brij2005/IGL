# Troubleshooting

Read the exact status string the software emits before changing anything. Every entry below names the literal value, because most of these are honest unconfigured states rather than faults.

## Database And Migrations

- **`Database migration is missing or out of date`:** run `alembic -c backend/alembic.ini upgrade head` against the intended `DATABASE_URL`, then restart. Back up the database first when upgrading an existing deployment.
- **`MIGRATIONS_PENDING_OR_UNAVAILABLE` in `migrations`:** the configured database revision differs from the Alembic head, or the migration state could not be read at all. The same command applies. The API refuses startup when the heads differ, so this state is normally visible on a health poll against an older process.
- **`init_db() no longer creates tables`:** schema is migration-owned by design. Use Alembic; there is no runtime `create_all` path.

## Model

- **`MODEL_NOT_CONFIGURED`:** `MODEL_WEIGHTS_PATH` is unset or empty. Model health reports this, `GET /api/v1/system/detectors` reports every implemented detector as `MODEL_NOT_CONFIGURED`, `scripts/validate_model.py` exits 2 with all checks `NOT_RUN`, and the dashboard overview and rules pages show it. Weights are never downloaded automatically. Set an explicit local checkpoint path plus `MODEL_NAME` and `MODEL_VERSION`.
- **`MODEL_INVALID_WEIGHTS` / `MODEL_LOAD_FAILED`:** verify local path, file access, and checkpoint format. The adapter will not fall back to another model.
- **`MODEL_RUNTIME_DEPENDENCY_MISSING`:** the checkpoint file is fine but this machine cannot execute the format. `missing_runtime_packages` in the model health document names exactly which distributions are needed: `ultralytics`+`torch` for `.pt`, `ultralytics`+`onnxruntime` for `.onnx`, `ultralytics`+`tensorrt` for `.engine`. On this machine `onnxruntime` and `tensorrt` are not installed. Install the named package deliberately, or point `MODEL_WEIGHTS_PATH` at a `.pt` checkpoint.
- **`MODEL_FILE_NOT_FOUND` / `MODEL_NOT_READABLE`:** the configured path does not exist, or the file cannot be opened or is zero bytes. `scripts/validate_model.py` reports these as `FILE_EXISTS: FAIL` or `FILE_READABLE: FAIL` and exits 2.
- **`MODEL_CLASSES_MISSING`:** the model loaded but reported no class names, so no class-gated detector can be supported. `CLASSES_PRESENT: FAIL`, exit 1.
- **Discovery finds nothing:** `MODEL_AUTO_DISCOVER` defaults to `false` and the settings validator refuses to start if it is `true` with an empty `MODEL_SEARCH_PATHS`. Discovery only scans the listed directories to `MODEL_SEARCH_MAX_DEPTH` for `.pt`, `.onnx` and `.engine`; it never downloads and never activates. Put a checkpoint in one of those directories and it will appear with its real size and SHA-256 and `activation_state: NOT_ACTIVATED`.

## Detector Capability

- **`NOT_IMPLEMENTED` / `NOT_AVAILABLE`:** this build does not implement the detector at all. That applies to `PPE`, `HELMET`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR`. No configuration, threshold or model makes these appear. The PPE-family reason is that compliance is an absence claim; a detector that can see a hard hat cannot prove one is missing.
- **`MODEL_CLASS_NOT_AVAILABLE`:** the detector is implemented but the loaded model does not expose the classes it requires. Check `required_classes` against `available_model_classes` in `GET /api/v1/system/detectors`. This is a model-selection problem, not a configuration problem.
- **`NOT_CONFIGURED` for an implemented detector:** the model supports the class and an operator has not enabled a detector configuration for it, or no zone geometry is configured. The reason field in the same response says which.

## Email

- **`EMAIL_NOT_CONFIGURED` (health `email_transport`):** `SMTP_HOST` or `NOTIFICATION_FROM_ADDRESS` is missing, or `SMTP_USERNAME` and `SMTP_PASSWORD` are not both set or both unset. This field reports configuration only; it never implies delivery.
- **`EMAIL_NOT_CONFIGURED` (test send `delivery_status`):** the same, or the sender address is malformed, or no recipient is configured. Observed on this machine with reason "SMTP host and sender address are required".
- **`EMAIL_DELIVERY_FAILED`:** a real SMTP attempt was made and failed. Read `error`, which is a safe class such as `SMTP_SMTPAUTHENTICATIONERROR` or `SMTP_TIMEOUT`; raw SMTP responses are intentionally not logged. Select STARTTLS (`SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`) or implicit TLS (`SMTP_USE_TLS=false`, `SMTP_USE_SSL=true`); the two cannot both be enabled and startup will refuse.
- **`EMAIL_DELIVERED`:** SMTP accepted the message. That is not proof of inbox or recipient receipt.
- **`EMAIL_NOT_ATTEMPTED` / `EMAIL_PENDING`:** no attempt has been made yet, or one is queued or in flight. `EMAIL_STATUS_UNKNOWN` means a stored status the mapping does not recognise; inspect the row rather than assuming a delivery.

## WhatsApp

- **`WHATSAPP_NOT_CONFIGURED` (health `whatsapp_transport`):** the access token, phone-number ID or API version is missing.
- **`WHATSAPP_NOT_CONFIGURED` (test send `delivery_status`):** observed on this machine with reason "Cloud API token, phone number ID and API version are required". The phone-number ID must be 5–30 digits and the API version must match `v<digits>.0`; an otherwise complete configuration with a malformed value reports the same status. A recipient that is not E.164 reports `WHATSAPP_DELIVERY_FAILED` with "Recipient must be an E.164 phone number".
- **`WHATSAPP_DELIVERED`:** Meta's Cloud API accepted the message. That is queued by Meta, not delivered or read by a person.

Persisted `notifications.status` values are unchanged (`QUEUED`, `SENT`, `RETRYING`, `FAILED`, `NOT_CONFIGURED`, `NOT_IMPLEMENTED`); `delivery_status` is an additional operator-facing field and never replaces them.

## Alarms

- **`ALARM_DISABLED_BY_POLICY`:** `ALARM_ENABLED=false`. No alarm is raised at all. This appears in health as `alarm_subsystem` and in the alarm centre banner.
- **`ALARM_READY_EVENT_DRIVEN`:** the policy is enabled. It does not mean an alarm is or will be raised; it means the subsystem is armed to raise from a confirmed event.
- **`BELOW_MIN_SEVERITY`:** the event severity is under `ALARM_MIN_SEVERITY`. Raising the alarm threshold will not help; the event severity comes from the detector rule's `severity` parameter.
- **`COOLDOWN_ACTIVE`:** the latest alarm for that event is still inside `ALARM_COOLDOWN_SECONDS`. Wait, or acknowledge the existing alarm if the condition genuinely needs re-alerting sooner. Do not set the cooldown to 0 to force a repeat.
- **`REPEAT_LIMIT_REACHED`:** raises for the event inside `ALARM_REPEAT_WINDOW_SECONDS` hit `ALARM_MAX_REPEATS_PER_WINDOW`. The open alarm's `suppression_count` records that the condition kept firing.
- **A repeat raise updated the existing alarm instead of creating a new row:** that is the designed behaviour. `raise_alarm_for_event` increments `raised_count` and extends `cooldown_until` on the open alarm for the same event, so a sustained condition cannot produce an alarm storm. The evidence is the counter on the existing row, not a separate status.
- **`ALARM not raised: EVENT_NOT_CONFIRMED`:** the event is not `CONFIRMED`. An alarm requires a temporally confirmed event. Check `observation_state` and `verification_state` on the event, and check the detector rule's `minimum_observations` and `minimum_duration_seconds`.
- **`409` on acknowledge, escalate or clear:** the alarm's current state does not permit that transition. Read `state` and `allowed_transitions` in the `GET /alarms/{id}` response. Acknowledging a `CLEARED` or `EXPIRED` alarm will always fail; those states are terminal.
- **`422` on escalate or clear:** the reason was empty or longer than 2000 characters. Every alarm transition requires a reason.
- **`404 Alarm not found`:** the alarm id is not in this database.
- **The board is empty but events exist:** that is the correct state. Alarms require a confirmed event, and if no model is configured there are no confirmed events at all. Check `GET /api/v1/system/detectors` and `GET /api/v1/alarms/policy` before assuming a fault.
- **Unacknowledged alarms keep disappearing:** `ALARM_AUTO_EXPIRE_SECONDS` is expiring them. That is the designed behaviour, recorded with the reason "Alarm expired without acknowledgement". Raise the value if the response window genuinely needs to be longer.

## Physical Alarm

- **`PHYSICAL_ALARM_NOT_CONFIGURED`:** either `ALARM_PHYSICAL_ACTUATION=false` (reason `ALARM_PHYSICAL_ACTUATION_DISABLED`) or no transport is configured (reason `NO_TRANSPORT_CONFIGURED`). Observed on this machine, where nothing is wired and nothing is claimed.
- **`ACTUATOR_NOT_CONNECTED`:** a transport is configured but its driver library or hardware is absent. The reason names it: `MQTT_CLIENT_LIBRARY_NOT_INSTALLED`, `PYSERIAL_NOT_INSTALLED`, `RPI_GPIO_NOT_INSTALLED`. Install the named package and connect the device. `paho-mqtt` and `pyserial` are not installed on this machine; `RPi.GPIO` is a Raspberry Pi library and is not applicable to a Windows laptop.
- **`ACTUATOR_NOT_CONNECTED` with reason `TRANSPORT_CONFIGURED_HARDWARE_NOT_VERIFIED`:** this is what `GET /alarms/physical/status` returns as soon as a transport is configured. It is the honest pre-commissioning state, and `hardware_verified` is still `false`.
- **`ACTIVATION_FAILED`:** a real attempt was made and did not succeed. The reason names the class: `HTTP_TIMEOUT`, `HTTP_CONNECTERROR`, `PROVIDER_HTTP_500`, `BROKER_ACK_TIMEOUT`, `SERIAL_*`, `GPIO_*`. Fix the wiring, the broker or the receiver; the failed attempt is recorded on the alarm and in the audit log, and must not be retried in a loop.
- **`DEACTIVATION_FAILED`:** the alarm was cleared but the release command did not succeed. The actuator may still be active. Attend to it physically.
- **`ACTIVATED`:** the transport call returned success. This is the only state that means a relay, siren or line was actually driven.
- **`409` on `POST /alarms/physical/test`:** your role is not `ADMIN` or `SAFETY_OFFICER`. The action is audited even when it fails.

## Health And Disk

- **`DEGRADED` on `GET /`:** read `degraded_reasons` in the same response. It names the unavailable subsystem rather than implying monitoring is working. On this machine the reason is `MODEL_NOT_CONFIGURED`, plus `CAMERA_CONFIGURED_NOT_OBSERVED` when the camera is stopped. Both are truthful states, not something to suppress.
- **`DISK_FREE_SPACE_BELOW_THRESHOLD`:** measured free space on the volume holding `HEALTH_DISK_PATH` (or `EVIDENCE_DIR`) fell below `HEALTH_DISK_MIN_FREE_BYTES` or `HEALTH_DISK_MIN_FREE_PERCENT`. Read `disk_free_bytes` and `disk_free_percent` in the same response. Free space, or move `EVIDENCE_DIR` and the database to a larger volume.
- **`DISK_UNAVAILABLE`:** the volume could not be measured. The platform reports this rather than assuming a healthy disk. Check that the path exists and the process can stat it.
- **`CAMERA_HEALTH_WORKER_<status>` in `degraded_reasons`:** the background health worker is not `RUNNING`. Restart the API.
- **`UPTIME_UNAVAILABLE`:** the monotonic clock could not be read. Rare; the platform reports it rather than guessing.
- **`NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`:** no frame has been observed, so `camera_fps` and `inference_fps` are `null`. This is not a performance problem; it is an absence of measurement. Start a camera and read the status again.

## Cameras

- **`CAMERA_CONFIGURED_NOT_OBSERVED` / `NO_WEBCAM_DETECTED`:** run `python scripts/test_webcam.py --index 0 --seconds 5 --width 640 --height 480 --fps 15 --max-probe-index 5 --json`. On Windows the probe tries DirectShow, Media Foundation (MSMF), then OpenCV automatic selection. Check Device Manager, connect/enable the device, allow camera and desktop-app access under Windows Settings > Privacy & security > Camera, and close other camera applications. No frame means no preview and no measured FPS.
- **A stopped camera keeps being restarted:** it should not. An operator stop is recorded in `camera_operator_stops.json` beside the database file, and the health worker skips such a camera. If a camera really is being restarted, that marker file is missing, unreadable or was deleted. A corrupt marker is treated as empty rather than as a startup failure, so check the file directly.
- **A stopped camera will not start again:** the operator-stop intent is still recorded. Start it with `POST /cameras/{id}/start`, which is the only action that clears the intent. Starting through the pipeline internals without `operator_start` will not clear it.
- **The camera is `REOPENED` but `running` is false:** that is correct. `REOPENED` means the capture handle is open but no frame from the new session has been observed. Wait for `observed_frames=true` and a rising `total_frames_read`.
- **`reconnect_state=BACKOFF`:** the reader is waiting between reconnect attempts. Check `reconnect_attempts` and the backoff bounds. `RECONNECT_EXHAUSTED` means `CAMERA_MAX_RECONNECT_ATTEMPTS` was reached; `0` means unlimited.
- **The wrong capture backend opened:** register the camera with `backend: "DSHOW"`, `"MSMF"` or `"ANY"` to change the probe order. A preference only reorders the probe and never removes a working option, and `source_backend` reports what actually opened.
- **`Camera is deactivated; activate it before starting its source`:** the camera record is inactive. An ADMIN must reactivate it.
- **Camera credentials rejected:** configure `CAMERA_URL_ENCRYPTION_KEY` before storing credential/query-bearing stream URLs. Do not reset the key for existing encrypted rows.
- **Snapshot returns `EVIDENCE_NOT_AVAILABLE`:** no frame has been observed from that camera. The endpoint returns 404 with that state rather than substituting a placeholder image.

## Authentication And Access

- **`401` while `ALLOW_ANONYMOUS_ACCESS=false`:** sign in at `/api/v1/auth/login`, use the returned bearer token, and verify the account is active. An unset or invalid JWT signing key prevents login.
- **Production/staging startup has no active admin:** supply all `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_FULL_NAME`, and `INITIAL_ADMIN_PASSWORD` for first startup, or provision an active ADMIN before switching anonymous access off.
- **`Wildcard CORS origins are not allowed`:** replace `*` in `BACKEND_CORS_ORIGINS` with exact origins.
- **An alarm action returns `403`:** acknowledge and clear need `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER` or `SUPERVISOR`; escalate needs `ADMIN`, `SAFETY_OFFICER` or `PLANT_MANAGER`; the physical test and operator re-raise need `ADMIN` or `SAFETY_OFFICER`. A `VIEWER` can read the board but cannot act on it.

## Sources, Evidence And Notifications

- **`VIDEO_SOURCE_NOT_CONFIGURED`:** set exactly one `VIDEO_SOURCE` or `RTSP_URL`, or pass `--source`.
- **`SOURCE_UNAVAILABLE` / `DISCONNECTED`:** verify authorized file/stream reachability and OS network/decoder access. Credentials are intentionally omitted from messages.
- **Evidence missing:** evidence is created only for an existing event and an actual supplied frame; a missing frame yields `EVIDENCE_NOT_AVAILABLE`.
- **No events/analytics:** the APIs show database-backed empty counts; no seed incidents are created.
- **A notification template shows a literal `{placeholder}`:** the name is not in the substitutable set, or the fact does not exist. It is left visible and reported as `NOT_SUBSTITUTED` rather than filled. Check the delivery log entry for that message.
- **Browser CORS failure on localhost:** use the project `.env.example` origins for port 8001, keep `BACKEND_CORS_ORIGINS` as a JSON list, and restart the API after changing environment settings.
- **Horizontal overflow in the dashboard at a narrow width:** run `node scripts/smoke_browser.mjs`, which renders all 11 views at 390/768/1024/1440/1920 pixels and reports the offending element. The current CSS wraps status pills and banners and the run reported zero overflow at every width.

No troubleshooting step requires creating synthetic operational data.
