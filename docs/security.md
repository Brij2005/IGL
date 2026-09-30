# Security Status

## Implemented

- Password hashing uses bcrypt; JWT tokens are signed and expiry-checked. API schemas reject passwords longer than bcrypt's 72-byte limit instead of silently truncating them.
- Server-side role/permission dependencies protect current APIs.
- Production configuration rejects the development JWT key and wildcard CORS.
- The first administrator is created only by an explicit, one-time operator action. There is no hard-coded or default password, startup never creates an administrator, and bootstrap is refused once any user exists.
- Login attempts are throttled per client IP in the current process. Use a shared rate-limit store for multi-worker deployments.
- User, login, camera mutation, and event transition activity is audit-recorded.
- `.env` is Git-ignored. SQLite database files and generated caches are also Git-ignored and were removed from version control.
- Camera API output strips URL user information, query parameters, and fragments, and delegates to the same redaction helper used by validation reports. Camera mutations do not place source URLs into audit details.
- Credential/query-bearing camera URLs require an environment-supplied Fernet key for DB encryption. Keys are not generated or committed by the app.
- Every response carries a correlation `X-Request-ID`; a caller-supplied value is accepted only when it matches a short restricted character set.

## Limitations

- The application database and its SQLite file are not encrypted at rest by this code.
- Non-credential camera URLs can remain plaintext in the DB. Legacy credential-bearing URLs created before encryption are not automatically re-encrypted; access without the matching key fails closed.
- Fernet key rotation, multi-process rate limiting, session revocation, lockout policy persistence, and evidence-specific authorization policy have not received independent security review.
- `User.employee_code` is an optional field but is not used to identify visual tracks. No face recognition or automatic identity mapping exists.
- The bootstrap utility must be run through a controlled administrative terminal, or with complete operator-supplied configuration, once. It refuses to run if any user already exists.
- No security certification or independent penetration test has been performed.

No production security certification or IGL security review is claimed.