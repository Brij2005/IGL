# Security Status

## Implemented

- Non-local (`production`/`staging`) settings reject `ALLOW_ANONYMOUS_ACCESS=true`; wildcard CORS is rejected in every environment.
- Camera URL user information, queries, and fragments are redacted in API output. Credential/query-bearing URLs require an operator-supplied Fernet key for storage.
- Evidence is resolved within the configured evidence directory and verified against its stored SHA-256 before serving.
- Audit rows written without a verified identity use a NULL actor; the application does not claim an identity it cannot authenticate.
- Responses carry a sanitized `X-Request-ID` header.
- `.env` is Git-ignored. SQLite database files and generated caches are also Git-ignored and were removed from version control.

## Limitations

- Authentication is not implemented. In development/test, anonymous access defaults on and every reachable write API can be used anonymously; declared role/permission dependencies are not identity authorization. Bind only to localhost or an externally authenticated, network-restricted boundary.
- With `ALLOW_ANONYMOUS_ACCESS=false`, protected API dependencies return 401 because no authentication flow exists. This is secure failure, not a usable production identity system.
- Production/staging startup rejects anonymous access, but this repository is not production-deployable until authentication, authorization enforcement, secrets, transport security, and deployment controls are implemented and reviewed.
- There is no external notification sender or delivery worker. SMTP/webhook settings do not imply delivery; configured-but-unsupported channels report `NOT_IMPLEMENTED`.
- SQLite is a local-development database and is not encrypted at rest by this code. No production database topology has been certified.
- Audit entries currently do not persist request IDs as a dedicated audit field. No independent penetration test or security certification has been performed.
- Legacy credential-bearing camera URLs are not automatically re-encrypted; key rotation requires a separately planned procedure.
- No security certification or independent penetration test has been performed.

No production security certification or IGL security review is claimed.