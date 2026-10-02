# Troubleshooting

- **`Database migration is missing or out of date`:** run `alembic -c backend/alembic.ini upgrade head` against the intended `DATABASE_URL`, then restart. Back up the database first when upgrading an existing deployment.
- **`init_db() no longer creates tables`:** schema is migration-owned by design. Use Alembic; there is no runtime `create_all` path.
- **`401` while `ALLOW_ANONYMOUS_ACCESS=false`:** this build has no authentication flow; protected routes intentionally fail closed. Do not enable this mode expecting login to work.
- **Production/staging settings reject anonymous access:** this is intentional. Authentication and authorization must be implemented before a shared deployment.
- **`MODEL_NOT_CONFIGURED`:** set a readable local `.pt` checkpoint, `MODEL_NAME`, and `MODEL_VERSION`. Weights are not downloaded.
- **`MODEL_INVALID_WEIGHTS` / `MODEL_LOAD_FAILED`:** verify local path, file access, compatible Ultralytics/PyTorch environment, and model checkpoint format. The adapter will not fall back.
- **`VIDEO_SOURCE_NOT_CONFIGURED`:** set exactly one `VIDEO_SOURCE` or `RTSP_URL`, or pass `--source`.
- **`SOURCE_UNAVAILABLE` / `DISCONNECTED`:** verify authorized file/stream reachability and OS network/decoder access. Credentials are intentionally omitted from messages.
- **Camera credentials rejected:** configure `CAMERA_URL_ENCRYPTION_KEY` before storing credential/query-bearing stream URLs. Do not reset the key for existing encrypted rows.
- **Evidence missing:** evidence is created only for an existing event and actual supplied frame; a missing frame yields `EVIDENCE_NOT_AVAILABLE`.
- **No events/analytics:** the APIs show database-backed empty counts; no seed incidents are created.
- **`DEGRADED` on `GET /`:** read `degraded_reasons` in the same response. It names the unavailable subsystem rather than implying monitoring is working.
- **Browser CORS failure on localhost:** use the project `.env.example` origins for port 8001, keep `BACKEND_CORS_ORIGINS` as a JSON list, and restart the API after changing environment settings.

No troubleshooting step requires creating synthetic operational data.