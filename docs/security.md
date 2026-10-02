# Security Status

## Implemented

- Non-local (`production`/`staging`) settings reject `ALLOW_ANONYMOUS_ACCESS=true`; wildcard CORS is rejected in every environment.
- Non-local settings reject SQLite URLs. Configure an operator-managed production database and its SQLAlchemy driver; production database operation is not validated by this repository run.
- Camera URL user information, queries, and fragments are redacted in API output. Credential/query-bearing URLs require an operator-supplied Fernet key for storage.
- Evidence is resolved within the configured evidence directory and verified against its stored SHA-256 before serving.
- Audit rows written without a verified identity use a NULL actor; the application does not claim an identity it cannot authenticate.
- Responses carry a sanitized `X-Request-ID` header.
- Responses include `nosniff`, frame denial, a restrictive referrer policy, camera/microphone/geolocation permissions, and CSP directives for base URI, objects, and framing. HTTPS requests also receive HSTS; authentication endpoints return `Cache-Control: no-store`.
- `.env` is Git-ignored. SQLite database files, generated caches, validation reports and the operator-stop marker file are also Git-ignored. `*.pt`, `*.onnx` and `*.engine` are Git-ignored so weights are never committed.

## Authentication

- Passwords are bcrypt-hashed; the database stores hashes only. Login issues an HS256 JWT with issuer, subject, issue/expiry times and password-change timestamp. Tokens expire within the configured limit and password changes invalidate prior tokens.
- Authenticated APIs resolve an active user and enforce the declared role or permission. Login throttling is process-local and should be backed by a gateway limit in multi-worker deployment.
- The visual track listing requires `cameras:view`; responses use session track IDs only and omit employee account identity. The browser hides event transition actions unless the account is an ADMIN or SAFETY_OFFICER, matching the backend role gate.
- `INITIAL_ADMIN_*` can create the first admin once, only if no ADMIN exists. Remove bootstrap secrets from deployment configuration after first provisioning.
- Development `.env.example` opts into anonymous mode. Keep it bound to loopback. Production/staging reject anonymous access and require `AUTH_JWT_SECRET_KEY` (at least 32 bytes) and an active admin.

## Alarm Audit Trail

- Every alarm state change is validated against the declared transition table, requires a non-empty reason of at most 2000 characters, and writes an `alarm_state_transitions` row carrying `previous_state`, `new_state`, `reason`, `user_id` and `transitioned_at`. An undeclared or reasonless transition is refused with `422`.
- Alarm reads require `events:view`. Mutation routes are role-gated and separated by severity of action: acknowledge and clear admit `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`, `SUPERVISOR`; escalate admits `ADMIN`, `SAFETY_OFFICER`, `PLANT_MANAGER`; the physical actuator test and the operator re-raise admit `ADMIN` and `SAFETY_OFFICER` only. A viewer cannot acknowledge an alarm.
- Operator actions additionally write the central audit log: `ALARM_ACKNOWLEDGED`, `ALARM_ESCALATED`, `ALARM_CLEARED`, `ALARM_RAISED_BY_OPERATOR`, `ALARM_RAISE_SUPPRESSED` and `PHYSICAL_ALARM_TEST_ATTEMPTED`, each with the actor id, the resource id and the client IP address. Suppressed re-raise attempts are audited too, so a policy refusal is visible rather than invisible.
- Suppression is recorded on the open alarm (`suppression_count` plus a same-state transition row with the reason), so a condition that kept firing while the board stayed quiet is auditable.
- An alarm cannot exist without a persisted `CONFIRMED` event. Alarm rows store the originating `event_id`, `camera_id`, `zone_id`, `track_id`, `detector_key`, `confidence`, `model_name` and `model_version`, plus a `provenance_json` copy of the event's verification and observation state and the model weights checksum. Alarm history is therefore traceable back to the detector rule and the exact checkpoint bytes.
- Escalation acknowledgement moves the linked alarm in the same action, so taking responsibility for an escalation cannot leave a live alarm sounding.
- The physical actuator test is audited before the result is returned, so an actuation attempt is recorded whether or not it succeeded.

## Physical Actuator Credentials

- Actuator credentials are secret-typed settings and are never returned by any status or test endpoint. `GET /api/v1/alarms/physical/status` reports only `state`, `enabled`, `configured_transports`, `reason` and `hardware_verified`; it never echoes the configured URL, bearer token, broker host, username or password.
- `PHYSICAL_ALARM_HTTP_URL`, `PHYSICAL_ALARM_HTTP_BEARER_TOKEN`, `PHYSICAL_ALARM_MQTT_PASSWORD` and the surrounding broker and device settings live in `.env` or a secret manager. They must not be committed, logged, or placed in a template.
- The outgoing actuator payload is assembled from a fixed allowlist of alarm facts. No environment value, template or arbitrary context object can be appended to it.
- Transport failures log an exception class or provider status, not a raw response body, and are caught so an actuator error can never propagate into the safety path or crash the API process.
- `hardware_verified` is `false` in every state the process can reach without a real successful transport call, and the operator test endpoint only returns `true` when the state is `ACTIVATED`. An unreachable broker or an unwired relay is reported as a failure, never as an alert delivered.
- Transport configuration is opt-in and defaults to off. A deployment that does not commission hardware should leave `ALARM_PHYSICAL_ACTUATION=false` and configure nothing.

## Template Injection And Secret Safety

- Notification templates can substitute only the fixed placeholder set: `event_id`, `event_type`, `severity`, `state`, `workflow_state`, `camera_id`, `zone_id`, `confidence`, `started_at`, `detector_key`, `model_name`, `model_version`, `incident_id`, `alarm_id`, `notes`. A name outside that set is never substituted, even when a caller supplies a matching key in the context mapping, so a template cannot reach a password, token, SMTP credential or any other setting value.
- The context mapping is built from one real `Event` row and its relationships. Nothing is read from the environment, so no configuration secret is in scope.
- Substitution is a single pass, so a substituted value containing braces is never re-scanned as a placeholder; a value cannot inject a new placeholder.
- A known placeholder with no value renders a documented sentinel (`NOT_MEASURED` for `confidence`, `NOT_AVAILABLE` otherwise) or is left visible and reported as `NOT_SUBSTITUTED`. A missing fact is never replaced with plausible content.
- Templates are plain text with no markup or script surface, so an operator can review exactly what will leave the plant network before enabling it on a real recipient.
- In-app values rendered in the dashboard are escaped before insertion into the DOM, and stream URLs, credentials and provider response bodies are never rendered.

## Limitations

- MFA is **NOT_IMPLEMENTED**. There is no second factor, no TOTP, no hardware key and no conditional-access policy.
- Password recovery is **NOT_IMPLEMENTED**. There is no reset email, no recovery code and no administrator reset flow. Admins can create accounts with initial passwords; users can change their own password after login. A user who forgets their password has no self-service path.
- Centralised rate limiting is **NOT_IMPLEMENTED**. Login throttling is process-local and resets on restart, and no per-endpoint or per-account rate limit exists at the application layer. A gateway or reverse-proxy limit is required for any shared deployment, and none is bundled.
- The browser holds its short-lived bearer token in tab-scoped session storage. The CSP intentionally leaves resource loading and API origins to the deployment/frontend topology; deployments should add an origin-specific CSP at their trusted reverse proxy.
- JWT signing uses one shared HS256 key; plan secure distribution/rotation. No production secret manager, TLS termination, gateway rate limiting, or production deployment topology is bundled, and none was validated.
- Database URLs, SMTP credentials, camera source URLs, physical actuator credentials and WhatsApp Cloud API tokens are secret-typed environment settings and are never returned by status endpoints. Delivery logs record exception classes and status codes rather than raw provider response bodies, which could include sensitive information.
- SQLite is a local-development database and is not encrypted at rest by this code. No production database topology has been certified.
- Audit entries do not persist request IDs as a dedicated audit field, so a specific request cannot be joined to an audit row from the audit table alone. The `X-Request-ID` correlation header is still on every response.
- Legacy credential-bearing camera URLs are not automatically re-encrypted; key rotation requires a separately planned procedure.
- The operator-stop marker file records a camera id and a timestamp and nothing else. A test asserts it holds no stream URL or credential, but the file is plain text on disk and relies on filesystem permissions.
- No independent penetration test, security certification or IGL security review has been performed.

Email uses STARTTLS by default (or explicit SMTP SSL); credentials must be supplied as a pair. WhatsApp uses the official Graph Cloud API endpoint with a configured API version, phone-number ID, access token, and E.164 destinations. Test-send endpoints contact the selected recipient and are restricted to ADMIN/SAFETY_OFFICER. Provider acceptance is not read/delivery confirmation.

No production security certification or IGL security review is claimed.
