# Security Status

## Implemented

- Non-local (`production`/`staging`) settings reject `ALLOW_ANONYMOUS_ACCESS=true`; wildcard CORS is rejected in every environment.
- Camera URL user information, queries, and fragments are redacted in API output. Credential/query-bearing URLs require an operator-supplied Fernet key for storage.
- Evidence is resolved within the configured evidence directory and verified against its stored SHA-256 before serving.
- Audit rows written without a verified identity use a NULL actor; the application does not claim an identity it cannot authenticate.
- Responses carry a sanitized `X-Request-ID` header.
- `.env` is Git-ignored. SQLite database files and generated caches are also Git-ignored and were removed from version control.

## Authentication

- Passwords are bcrypt-hashed; the database stores hashes only. Login issues an HS256 JWT with issuer, subject, issue/expiry times and password-change timestamp. Tokens expire within the configured limit and password changes invalidate prior tokens.
- Authenticated APIs resolve an active user and enforce the declared role or permission. Login throttling is process-local and should be backed by a gateway limit in multi-worker deployment.
- `INITIAL_ADMIN_*` can create the first admin once, only if no ADMIN exists. Remove bootstrap secrets from deployment configuration after first provisioning.
- Development `.env.example` opts into anonymous mode. Keep it bound to loopback. Production/staging reject anonymous access and require `AUTH_JWT_SECRET_KEY` (at least 32 bytes) and an active admin.

## Limitations

- The browser holds its short-lived bearer token in tab-scoped session storage. Apply a restrictive deployment CSP and prevent script injection; no independent penetration test has been performed.
- JWT signing uses one shared HS256 key; plan secure distribution/rotation. Login throttling is process-local. No production secret manager, TLS termination, gateway rate limiting, or production deployment topology is bundled.
- The app has no recovery email/password reset flow or multi-factor authentication. Admins can create accounts with initial passwords; users can change their own password after login.
- SMTP credentials and WhatsApp Cloud API tokens are secret-typed environment settings and are never returned by status endpoints. Delivery logs record exception classes/status codes rather than raw provider response bodies, which could include sensitive information.
- SQLite is a local-development database and is not encrypted at rest by this code. No production database topology has been certified.
- Audit entries currently do not persist request IDs as a dedicated audit field. No independent penetration test or security certification has been performed.
- Legacy credential-bearing camera URLs are not automatically re-encrypted; key rotation requires a separately planned procedure.
- No security certification or independent penetration test has been performed.

Email uses STARTTLS by default (or explicit SMTP SSL); credentials must be supplied as a pair. WhatsApp uses the official Graph Cloud API endpoint with a configured API version, phone-number ID, access token, and E.164 destinations. Test-send endpoints contact the selected recipient; authenticated deployments restrict them to ADMIN/SAFETY_OFFICER. Provider acceptance is not read/delivery confirmation.

No production security certification or IGL security review is claimed.
