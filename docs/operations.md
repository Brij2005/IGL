# Operations Runbook

## Startup

1. Install `backend/requirements.txt` and copy `.env.example` to `.env` for the localhost demo.
2. From the project root, run `python -m alembic -c backend/alembic.ini upgrade head`.
3. Start the API with `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`.
4. Start the static dashboard with `python -m http.server 8001 --directory frontend`, then open `http://127.0.0.1:8001`.
5. In another PowerShell terminal run `./scripts/test_frontend_smoke.ps1` to verify dashboard assets and read-only health/worker API responses. This command sends no email/WhatsApp, raises no alarm and creates no events.

If the default API port 8000 is occupied, start it on 8002 and change the dashboard API base to `http://127.0.0.1:8002/api/v1` in the header field.

The API refuses startup if the DB revision is not at the Alembic head. Startup does not create schema. When `INITIAL_ADMIN_*` is configured, it creates the first admin only if no admin exists. Development allows anonymous access only because `.env.example` opts into localhost demo mode. Production/staging require `ALLOW_ANONYMOUS_ACCESS=false`, `AUTH_JWT_SECRET_KEY`, and an existing active admin.

## Reported States

The Workers view and `GET /api/v1/workers/tracks?recent_only=true&recent_within_seconds=30` show persisted visual track sessions observed within the requested timestamp window. If the model/pipeline has not persisted tracks, the response is empty and the dashboard shows `NO_LIVE_TRACK_DATA` and states that this does not indicate the area is safe. The API never maps track IDs to employee identities. Helmet/PPE/phone states remain `UNKNOWN` unless the inference pipeline provides a supported rule verdict.

- The process is up: `APPLICATION_UP`. This is not health.
- No model weights: `MODEL_NOT_CONFIGURED`.
- No camera records: `NO_CAMERA`; cameras recorded but none active: `CAMERA_RECORDED_BUT_NONE_ACTIVE`.
- Active camera configuration without a live frame: `CAMERA_CONFIGURED_NOT_OBSERVED`; per-camera health is `CONFIGURED` or `CONNECTING` with measured values `null`. `CAMERA_AVAILABLE` requires a running stream with an observed real frame.
- Source unavailable or timed out: offline/timeout state, not healthy monitoring.
- `overall_status` is `DEGRADED` with an explicit `degraded_reasons` list whenever the database, migrations, model, cameras, the camera-health worker or free disk space are not in a usable state. On this machine the reason is `MODEL_NOT_CONFIGURED`, plus `CAMERA_CONFIGURED_NOT_OBSERVED` when the camera is stopped. That is the truthful state.
- No observed frame: `measured_performance` is `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES` and `camera_fps` and `inference_fps` are `null`.
- Disk: `DISK_OK`, `DISK_FREE_SPACE_BELOW_THRESHOLD` or `DISK_UNAVAILABLE`, measured from the real volume. The thresholds are `HEALTH_DISK_MIN_FREE_BYTES` and `HEALTH_DISK_MIN_FREE_PERCENT`.
- Uptime: `MEASURED`, from this process only. A backend restart resets it.
- SMTP EMAIL and WhatsApp Cloud API channels are sent by the notification worker when configured. Other external channels remain `NOT_IMPLEMENTED`. A provider acceptance is recorded as `SENT`, which does not confirm the destination inbox or WhatsApp user received or read the message.
- No verified real input: `validation_status` is `NOT_VALIDATED`, `igl_validated` is `false`, and `igl_configuration_status` is `NOT_CONFIGURED`.
- Observed camera failure: a `CAMERA_FAILURE` event is written with `observation_state=NOT_ASSESSABLE` and no confidence value, deduplicated per camera for 60 seconds. Fix the source rather than suppressing the record.

## Day-To-Day Alarm Operation

The alarm centre view reads `GET /api/v1/alarms?active_only=true`, `GET /api/v1/alarms?limit=200&offset=0`, `GET /api/v1/alarms/policy` and `GET /api/v1/alarms/physical/status`. An empty board reads `NO_ACTIVE_ALARMS`; the view states that nothing is synthesised to fill it.

Per-alarm actions and their roles:

| Action | Endpoint | Roles |
| --- | --- | --- |
| Acknowledge | `POST /alarms/{id}/acknowledge` | `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`, `SUPERVISOR` |
| Escalate | `POST /alarms/{id}/escalate?reason=...` | `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER` |
| Clear | `POST /alarms/{id}/clear` | `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`, `SUPERVISOR` |
| History | `GET /alarms/{id}/history` | `events:view` |
| Physical test | `POST /alarms/physical/test` | `ADMIN`, `SAFETY_OFFICER` |

Working an alarm:

1. Read the row. `severity`, `detector_key`, `confidence`, `model_name` and `model_version` tell you which detector and which weights produced the underlying event. Check the same facts on the event before treating the alarm as a real hazard.
2. Acknowledge with a note naming who took it. The note is stored on the alarm and in the audit log. Acknowledging is idempotent only in the sense that re-acknowledging an already acknowledged alarm returns `409`.
3. Escalate when the condition is unhandled past its policy window. Escalation requires a reason and moves the alarm to `ESCALATED`; the escalation worker does the same automatically when an event escalation policy fires.
4. Clear with a reason when the condition has ended. Clearing a live alarm whose `physical_state` is `ACTIVATED` also attempts a real deactivation.

The browser audible alarm is driven by the real active-alarm list and has an operator mute toggle. It is an operator-controlled sound, not a physical output, and it is suppressed by `ALARM_AUDIBLE_BROWSER=false`.

If an acknowledge or clear returns `409`, read the alarm's `state` and `allowed_transitions` before retrying; the transition was refused because the starting state does not permit it, not because the request was lost. If an operator re-raise returns `409`, the detail names the policy reason (`ALARM_DISABLED_BY_POLICY`, `BELOW_MIN_SEVERITY`, `COOLDOWN_ACTIVE`, `REPEAT_LIMIT_REACHED`, `EVENT_NOT_CONFIRMED`). Do not work around a policy suppression by raising severity in configuration to make an alarm appear.

## Alarm Policy Tuning

Tune in `.env` and restart the API. `GET /alarms/policy` echoes the live values back.

- `ALARM_ENABLED=false` stops all raising and reports `ALARM_DISABLED_BY_POLICY` in health. Use it only for a planned maintenance window.
- `ALARM_MIN_SEVERITY` controls the floor. Lowering it to `LOW` increases board volume on every event; raising it to `HIGH` means only the most serious conditions alarm. The trade-off is deliberate and is recorded in the audit trail of each suppression.
- `ALARM_COOLDOWN_SECONDS` is the minimum spacing between two raises for the same event. A sustained condition re-raises after this interval and increments `raised_count` on the same row rather than creating duplicates.
- `ALARM_MAX_REPEATS_PER_WINDOW` with `ALARM_REPEAT_WINDOW_SECONDS` bounds repeats. Beyond the bound the condition is suppressed and `suppression_count` on the open alarm increments, so you can see that something kept firing while the board stayed quiet.
- `ALARM_AUTO_EXPIRE_SECONDS` bounds how long an unacknowledged alarm stays live. Too short and real alarms disappear before anyone acts; too long and the board fills with stale rows.
- `ALARM_PHYSICAL_ACTUATION` is the master switch for hardware. See below.

## Physical Actuator Commissioning

There is no actuator on the machine this repository was validated on. Commissioning is a hardware task, and the platform is designed to say so until it is done.

1. `GET /api/v1/alarms/physical/status`. While `state` is `PHYSICAL_ALARM_NOT_CONFIGURED`, either `ALARM_PHYSICAL_ACTUATION` is false or no transport is configured. Fix that first.
2. Choose one transport and configure only that one:
   - HTTP: `PHYSICAL_ALARM_HTTP_URL` (secret-typed), `PHYSICAL_ALARM_HTTP_METHOD` (`POST` or `PUT`), `PHYSICAL_ALARM_HTTP_BEARER_TOKEN` (secret-typed), `PHYSICAL_ALARM_HTTP_TIMEOUT_SECONDS`.
   - MQTT: `PHYSICAL_ALARM_MQTT_HOST`, `PHYSICAL_ALARM_MQTT_PORT`, `PHYSICAL_ALARM_MQTT_TOPIC` (host requires topic), optional paired `PHYSICAL_ALARM_MQTT_USERNAME` / `PHYSICAL_ALARM_MQTT_PASSWORD`, `PHYSICAL_ALARM_MQTT_USE_TLS`.
   - Serial relay: `PHYSICAL_ALARM_SERIAL_PORT`, `PHYSICAL_ALARM_SERIAL_BAUD`. Requires `pyserial`.
   - GPIO: `PHYSICAL_ALARM_GPIO_PIN` (must be greater than 0 to be considered configured), `PHYSICAL_ALARM_GPIO_ACTIVE_HIGH`. Requires `RPi.GPIO`, which is a Raspberry Pi library and is not present on this Windows laptop.
3. Set `ALARM_PHYSICAL_ACTUATION=true` and restart. `GET /alarms/physical/status` now reports `ACTUATOR_NOT_CONNECTED` with reason `TRANSPORT_CONFIGURED_HARDWARE_NOT_VERIFIED` and `hardware_verified=false`. That is expected until hardware answers.
4. Install the driver's own dependency. MQTT needs `paho-mqtt` and serial needs `pyserial`; neither is installed here. A missing library reports `ACTUATOR_NOT_CONNECTED` with the package named (`MQTT_CLIENT_LIBRARY_NOT_INSTALLED`, `PYSERIAL_NOT_INSTALLED`, `RPI_GPIO_NOT_INSTALLED`) and never falls back to reporting success.
5. Have an authorised operator run `POST /api/v1/alarms/physical/test` and confirm the state. Record the `PHYSICAL_ALARM_TEST_ATTEMPTED` audit entry.
6. Only `ACTIVATED` counts as commissioned, and only `hardware_verified=true` means the actuator answered. Record the actuator identity, the transport, the test result and the audit id in the commissioning log. If the state is `ACTIVATION_FAILED`, the reason names the failure class (`HTTP_TIMEOUT`, `PROVIDER_HTTP_503`, `BROKER_ACK_TIMEOUT`, `SERIAL_*`, `GPIO_*`); fix the wiring or the broker, do not treat the attempt as an alarm.

Commissioning was not performed on this machine, so no physical alarm has ever been activated and none is claimed.

## Camera Operation

Starting and stopping a camera source:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/cameras/$cameraId/start
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/cameras/$cameraId/status
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/cameras/$cameraId/stop
```

Start and stop require `ADMIN` or `SAFETY_OFFICER`; status requires `cameras:view`. Neither start nor stop creates a safety event.

Reopening a camera. When a source is deliberately stopped and then started again, the reader reports `REOPENING` while the previous source is torn down and `REOPENED` once the capture handle is open but no frame from the new session has been observed. `REOPENED` is deliberately not `RUNNING`: wait for `observed_frames=true` and a rising `total_frames_read` before calling the camera live. The first real frame moves it to `RUNNING` and clears `reopen_in_progress`. `reconnect_state` stays `IDLE` when nothing is being retried; `BACKOFF` means the reader is waiting between reconnect attempts, and `RECONNECT_EXHAUSTED` means `CAMERA_MAX_RECONNECT_ATTEMPTS` was reached. Neither the browser nor the API ever restarts a camera on its own.

Operator stop persistence. `POST /cameras/{id}/stop` records the stop in `camera_operator_stops.json` beside the database file. The camera health worker skips such a camera, and a backend restart does not clear the record, so the health worker will not silently resume a camera an operator stopped. Only an operator start (`POST /cameras/{id}/start`) clears the intent. If a camera will not start, check that file before touching the database; it is git-ignored deployment state, not configuration.

Capture backend. A webcam may request `DSHOW`, `MSMF` or `ANY` at registration. The preference only reorders the probe and never removes a working option. `source_backend` in the status payload reports the backend that actually opened the capture, not the one requested.

Verified on this machine: the registered "Laptop Webcam" opened through DSHOW, delivered 165 real frames, and health reported `camera_state=CAMERA_AVAILABLE`, `measured_performance=MEASURED_FROM_OBSERVED_FRAMES`, `camera_fps=8.03`. Stop returned `NOT_STARTED`; the camera was checked at +8 s, +18 s and +28 s against a 5 s health interval and stayed stopped with 0 frames, with health correctly dropping to `CAMERA_CONFIGURED_NOT_OBSERVED`. Reopening returned to `RUNNING` with 67 frames through DSHOW; a final stop returned `NOT_STARTED`. `reconnect_state=IDLE` throughout. If a device will not open here, run `python scripts/test_webcam.py --index 0 --seconds 5 --width 640 --height 480 --fps 15 --max-probe-index 5 --json` and check Device Manager and Windows camera permissions.

## Model Validation

Validate an artifact before pointing a camera at it. This needs no video source.

```powershell
python scripts/validate_model.py
python scripts/validate_model.py --weights "C:\authorized\path\checkpoint.pt" --timed-passes 10 --report "C:\secure\model_report.json"
```

Read the output as follows. `overall_state` is the headline. Each check appears on its own line: `FILE_EXISTS`, `FILE_READABLE`, `MODEL_LOADS`, `CLASSES_PRESENT`, `INFERENCE_EXECUTES`, `OUTPUT_SCHEMA_VALID`, `LATENCY_MEASURED`, each `PASS`, `FAIL` or `NOT_RUN`. Exit code 0 means every check passed, 1 means a check ran and failed, 2 means no check could run at all. The throughput figures carry the label `SYNTHETIC_PROBE_NOT_REAL_FOOTAGE`; they measure load and speed on a blank frame and say nothing about detection accuracy. `validation_state` is `NOT_VALIDATED` and `detection_quality_validated` is `false` in every case, including a clean pass.

Local discovery:

```powershell
python -c "from ai_models.ultralytics_adapter import discover_local_models; print(discover_local_models([r'C:\models'], 3))"
```

Discovery only reports files that already exist, with their real size and SHA-256 and `activation_state: NOT_ACTIVATED`. It never downloads. A discovered file runs only when `MODEL_WEIGHTS_PATH` names it.

Recorded on this machine: `python scripts/validate_model.py` returned `overall_state=MODEL_NOT_CONFIGURED`, `validation_state=NOT_VALIDATED`, every check `NOT_RUN`, `detection_quality_validated=false`, exit code 2. Local discovery found 0 candidate checkpoints. There are no weights in the repository and none were downloaded. `onnxruntime` and `tensorrt` are not installed, so an `.onnx` or `.engine` checkpoint would report `MODEL_RUNTIME_DEPENDENCY_MISSING` with the package named.

## Notification Operations

`POST /api/v1/notifications/test/email` and `/test/whatsapp` contact a real recipient. Read `delivery_status` in the response: `EMAIL_DELIVERED` / `EMAIL_DELIVERY_FAILED` / `EMAIL_NOT_CONFIGURED` / `EMAIL_NOT_ATTEMPTED` / `EMAIL_PENDING`, and the same for `WHATSAPP_*`. A provider acceptance is not proof of receipt.

Both test sends returned `*_NOT_CONFIGURED` on this machine: "SMTP host and sender address are required" and "Cloud API token, phone number ID and API version are required". No SMTP or Meta Cloud API credentials exist here, so no notification has ever been delivered from this deployment.

Message templates are plain text with a fixed placeholder set. If a placeholder is unknown it is left visible in the sent text and reported as `NOT_SUBSTITUTED` in the delivery log; it is never filled with invented content. Review a template before enabling it on a real recipient.

## Recovery And Limitations

Use additive Alembic migrations; do not delete or recreate databases. Back up the database and evidence before any planned migration or key rotation. Revision `9c4b2e7a1d55` rebuilds `camera_health` to make `fps` and `latency_ms` nullable and backfills placeholder zeros to `NULL`; it preserves all rows and was tested for upgrade, downgrade and re-upgrade on a temporary database. Revision `b2a5c8e4f701` adds the alarm tables `alarms` and `alarm_state_transitions` and downgrades cleanly. Apply migrations with `alembic -c backend/alembic.ini upgrade head`.

Camera health polling, notification delivery and active pipelines are process-local. Running more than one API worker will duplicate the health polling and the notification delivery loop unless a shared scheduler and a shared broker are added first. Alarm expiry is not a background job: it runs inside `GET /api/v1/alarms/policy`, so any process serving that endpoint can expire stale alarms. That topology has not been validated.

RTSP/file deployment behaviour has not been validated on this machine. No compatible model weights, IGL layout, SOP or labelled model-validation data is included. SQLite is for local development; no production database or topology has been certified. Production deployment steps are in [docs/deployment.md](docs/deployment.md) and are explicitly not validated.

Reports and evidence are sensitive operational data; restrict filesystem and report access.
