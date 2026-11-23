import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_auth_enabled_protects_api_and_login_works():
    with tempfile.TemporaryDirectory(prefix="igl-auth-test-") as temp_dir:
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_PATH": str(Path(temp_dir) / "auth.db"),
                "ENABLE_AUTH": "true",
                "ADMIN_USERNAME": "admin",
                "ADMIN_PASSWORD": "ChangeMe-IGL-2026!",
                "SECRET_KEY": "test-secret",
            }
        )
        code = """
from fastapi.testclient import TestClient
from main import app
with TestClient(app) as c:
    assert c.get('/api/cameras').status_code == 401
    login = c.post('/api/auth/login', json={'username':'admin','password':'ChangeMe-IGL-2026!'})
    assert login.status_code == 200, login.text
    token = login.json()['access_token']
    assert c.get('/api/cameras', headers={'Authorization': f'Bearer {token}'}).status_code == 200
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr