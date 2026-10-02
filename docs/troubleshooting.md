# Troubleshooting

- **`Database migration is missing or out of date`:** run `alembic -c backend/alembic.ini upgrade head` against the intended `DATABASE_URL`, then restart. Back up the database first when upgrading an existing deployment.
- **`init_db() no longer creates tables`:** schema is migration-owned by design. Use Alembic; there is no runtime `create_all` path.
- **`401` while `ALLOW_ANONYMOUS_ACCESS=false`:** sign in at `/api/v1/auth/login`, use the returned bearer token, and verify the account is active. An unset or invalid JWT signing key prevents login.
- **`CAMERA_CONFIGURED_NOT_OBSERVED` / `NO_WEBCAM_DETECTED`:** run `python scripts/test_webcam.py --index 0 --seconds 5 --width 640 --height 480 --fps 15 --max-probe-index 5 --json`. On Windows the probe tries DirectShow, Media Foundation (MSMF), then OpenCV automatic selection. Check Device Manager, connect/enable the device, allow camera and desktop-app access under Windows Settings > Privacy & security > Camera, and close other camera applications. No frame means no preview or measured FPS.
- **Production/staging startup has no active admin:** supply all `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_FULL_NAME`, and `INITIAL_ADMIN_PASSWORD` for first startup, or provision an active ADMIN before switching anonymous access off.
- **`MODEL_NOT_CONFIGURED`:** set a readable local `.pt` checkpoint, `MODEL_NAME`, and `MODEL_VERSION`. Weights are not downloaded.
- **`MODEL_INVALID_WEIGHTS` / `MODEL_LOAD_FAILED`:** verify local path, file access, compatible Ultralytics/PyTorch environment, and model checkpoint format. The adapter will not fall back.
- **`VIDEO_SOURCE_NOT_CONFIGURED`:** set exactly one `VIDEO_SOURCE` or `RTSP_URL`, or pass `--source`.
- **`SOURCE_UNAVAILABLE` / `DISCONNECTED`:** verify authorized file/stream reachability and OS network/decoder access. Credentials are intentionally omitted from messages.
- **Camera credentials rejected:** configure `CAMERA_URL_ENCRYPTION_KEY` before storing credential/query-bearing stream URLs. Do not reset the key for existing encrypted rows.
- **Evidence missing:** evidence is created only for an existing event and actual supplied frame; a missing frame yields `EVIDENCE_NOT_AVAILABLE`.
- **No events/analytics:** the APIs show database-backed empty counts; no seed incidents are created.
- **`DEGRADED` on `GET /`:** read `degraded_reasons` in the same response. It names the unavailable subsystem rather than implying monitoring is working.
- **Browser CORS failure on localhost:** use the project `.env.example` origins for port 8001, keep `BACKEND_CORS_ORIGINS` as a JSON list, and restart the API after changing environment settings.
- **Email remains `NOT_CONFIGURED`:** check SMTP host/sender and configure both username and password or neither. Select STARTTLS (`SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`) or implicit TLS (`SMTP_USE_TLS=false`, `SMTP_USE_SSL=true`). Inspect the queue status and safe error class; raw SMTP responses are intentionally not logged.
- **WhatsApp remains `NOT_CONFIGURED`:** configure the Cloud API access token, numeric phone-number ID, `vNN.0` API version, and one or more E.164 recipient numbers. A provider HTTP acceptance means queued by Meta, not delivered/read by a person.

No troubleshooting step requires creating synthetic operational data.
