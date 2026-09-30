# Troubleshooting

- **`Database migration is missing or out of date`:** run `alembic -c backend/alembic.ini upgrade head` against the intended `DATABASE_URL`, then restart. Back up the database first when upgrading an existing deployment.
- **`init_db() no longer creates tables`:** schema is migration-owned by design. Use Alembic; there is no runtime `create_all` path.
- **`Bootstrap refused: BOOTSTRAP_ADMIN_ENABLED is not set`:** the non-interactive bootstrap is disabled by default and startup never creates an administrator. Run `python scripts/bootstrap_admin.py` interactively, or supply the complete `BOOTSTRAP_ADMIN_*` configuration.
- **`Bootstrap refused: a user already exists`:** bootstrap is one-time. Use `POST /api/v1/auth/users` as an existing administrator.
- **`MODEL_NOT_CONFIGURED`:** set a readable local `.pt` checkpoint, `MODEL_NAME`, and `MODEL_VERSION`. Weights are not downloaded.
- **`MODEL_INVALID_WEIGHTS` / `MODEL_LOAD_FAILED`:** verify local path, file access, compatible Ultralytics/PyTorch environment, and model checkpoint format. The adapter will not fall back.
- **`VIDEO_SOURCE_NOT_CONFIGURED`:** set exactly one `VIDEO_SOURCE` or `RTSP_URL`, or pass `--source`.
- **`SOURCE_UNAVAILABLE` / `DISCONNECTED`:** verify authorized file/stream reachability and OS network/decoder access. Credentials are intentionally omitted from messages.
- **Camera credentials rejected:** configure `CAMERA_URL_ENCRYPTION_KEY` before storing credential/query-bearing stream URLs. Do not reset the key for existing encrypted rows.
- **Evidence missing:** evidence is created only for an existing event and actual supplied frame; a missing frame yields `EVIDENCE_NOT_AVAILABLE`.
- **No events/analytics:** the APIs show database-backed empty counts; no seed incidents are created.
- **`DEGRADED` on `GET /`:** read `degraded_reasons` in the same response. It names the unavailable subsystem rather than implying monitoring is working.
- **`429` on login:** the client exceeded `LOGIN_RATE_LIMIT_ATTEMPTS` within `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, including after a successful credential match.

No troubleshooting step requires creating synthetic operational data.