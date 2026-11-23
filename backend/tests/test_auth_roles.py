"""Authentication, role-based access control, and logout revocation tests.

Runs against an isolated test database with ENABLE_AUTH=true in a subprocess so
the real protection path (middleware + role dependencies) is exercised.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_auth_scenario(temp_dir: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_PATH": str(Path(temp_dir) / "auth.db"),
            "ENABLE_AUTH": "true",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "ChangeMe-IGL-2026!",
            "SECRET_KEY": "test-secret-for-auth-suite",
        }
    )
    code = """
import json
from fastapi.testclient import TestClient
from main import app

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))

with TestClient(app) as c:
    # 1. Unauthenticated API access must be rejected
    r = c.get("/api/cameras")
    check("unauthenticated /api/cameras -> 401", r.status_code == 401, r.status_code)

    # 2. Static (non-API) routes must remain reachable for the SPA
    r = c.get("/")
    check("root (non-API) reachable without auth", r.status_code == 200, r.status_code)

    # 3. Login works and returns a token
    r = c.post("/api/auth/login", json={"username": "admin", "password": "ChangeMe-IGL-2026!"})
    check("login 200", r.status_code == 200, r.text[:200])
    token = r.json().get("access_token")
    check("token issued", bool(token))
    headers = {"Authorization": f"Bearer {token}"}

    # 4. Authenticated access works
    check("authenticated /api/cameras 200", c.get("/api/cameras", headers=headers).status_code == 200)

    # 5. /auth/me returns the admin identity
    me = c.get("/api/auth/me", headers=headers)
    check("/auth/me 200", me.status_code == 200, me.status_code)
    check("/auth/me role admin", me.json().get("user", {}).get("role") == "admin", me.json())

    # 6. Bad password rejected
    r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    check("bad password -> 401", r.status_code == 401, r.status_code)

    # 7. Create an operator and a viewer, then verify the role matrix
    c.post("/api/users", headers=headers, json={"username": "op1", "password": "OperatorPass1!", "role": "operator"})
    c.post("/api/users", headers=headers, json={"username": "vw1", "password": "ViewerPass1!", "role": "viewer"})

    op = c.post("/api/auth/login", json={"username": "op1", "password": "OperatorPass1!"}).json()["access_token"]
    vw = c.post("/api/auth/login", json={"username": "vw1", "password": "ViewerPass1!"}).json()["access_token"]
    oh = {"Authorization": f"Bearer {op}"}
    vh = {"Authorization": f"Bearer {vw}"}

    # Operator: may start/stop cameras but may NOT manage users
    check("operator can start camera", c.post("/api/cameras/does-not-exist/start", headers=oh).status_code in (400, 404), "operator start")
    check("operator cannot list users (403)", c.get("/api/users", headers=oh).status_code == 403, c.get("/api/users", headers=oh).status_code)
    check("operator cannot read audit logs (403)", c.get("/api/audit-logs", headers=oh).status_code == 403)

    # Viewer: read-only; cannot mutate
    check("viewer can read cameras", c.get("/api/cameras", headers=vh).status_code == 200)
    check("viewer cannot create camera (403)", c.post("/api/cameras", headers=vh, json={"name": "x", "source_type": "image", "source_url": "y"}).status_code == 403)
    check("viewer cannot read users (403)", c.get("/api/users", headers=vh).status_code == 403)

    # Admin: full access
    check("admin can read users", c.get("/api/users", headers=headers).status_code == 200)
    check("admin can read audit logs", c.get("/api/audit-logs", headers=headers).status_code == 200)

    # 8. Logout revokes the token
    r = c.post("/api/auth/logout", headers=oh)
    check("logout 200", r.status_code == 200, r.status_code)
    check("token revoked after logout (401)", c.get("/api/cameras", headers=oh).status_code == 401, "revoked token still works")

    # 9. Login events are audited
    logs = c.get("/api/audit-logs", headers=headers).json()
    check("login recorded in audit log", any(l.get("action") == "auth.login" for l in logs))

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: " + detail))
print("SUMMARY: %d passed, %d failed" % (len(results) - len(failed), len(failed)))
raise SystemExit(1 if failed else 0)
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_role_matrix_authentication_and_logout():
    with tempfile.TemporaryDirectory(prefix="igl-auth-matrix-") as temp_dir:
        result = _run_auth_scenario(temp_dir)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, output
        assert "0 failed" in output, output


def test_password_hashing_is_salted_and_verifiable():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "igl-hash-test.db"))
    from security import hash_password, verify_password

    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2, "Hashes must use unique salts"
    assert verify_password("same-password", h1)
    assert verify_password("same-password", h2)
    assert not verify_password("wrong-password", h1)
    assert not verify_password("", h1)


def test_token_rejection_on_tampered_signature():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "igl-token-test.db"))
    from fastapi import HTTPException
    from security import create_access_token, decode_access_token

    token = create_access_token("USR-1", "u", "viewer")
    assert decode_access_token(token)["role"] == "viewer"
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    try:
        decode_access_token(tampered)
        raise AssertionError("Tampered token must be rejected")
    except HTTPException as exc:
        assert exc.status_code == 401