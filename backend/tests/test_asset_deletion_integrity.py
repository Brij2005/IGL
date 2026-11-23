"""Regression tests for asset deletion referential integrity.

Bug fixed: DELETE /api/security/assets/{id} used to hard-delete only the asset
row, leaving orphaned asset_movements / gate_passes rows behind.
"""

import os
import shutil
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-asset-del-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")

import pytest
from fastapi.testclient import TestClient

from database import init_db

init_db()

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _create_asset(client, rfid):
    response = client.post(
        "/api/security/assets",
        json={"asset_name": f"Asset {rfid}", "asset_category": "TOOL", "rfid_tag": rfid},
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_delete_asset_without_history_succeeds(client):
    asset_id = _create_asset(client, "RFID-DEL-1")
    response = client.delete(f"/api/security/assets/{asset_id}")
    assert response.status_code == 200
    remaining = {a["id"] for a in client.get("/api/security/assets").json()}
    assert asset_id not in remaining


def test_delete_asset_with_history_is_refused(client):
    asset_id = _create_asset(client, "RFID-DEL-2")
    movement = client.post(
        f"/api/security/assets/{asset_id}/movements",
        json={"direction": "EXIT", "verification_source": "RFID"},
    )
    assert movement.status_code == 200

    response = client.delete(f"/api/security/assets/{asset_id}")
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "CONFLICT"

    # The asset and its movement must both survive.
    remaining = {a["id"] for a in client.get("/api/security/assets").json()}
    assert asset_id in remaining
    movements = client.get("/api/security/assets/movements").json()
    assert any(m["id"] == movement.json()["id"] for m in movements)


def test_no_orphaned_movements_after_refused_delete(client):
    from database import get_db_connection

    conn = get_db_connection()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM asset_movements "
        "WHERE asset_id NOT IN (SELECT id FROM asset_register)"
    ).fetchone()[0]
    conn.close()
    assert orphans == 0, "Refusing deletion must never leave orphaned movements"


def test_delete_missing_asset_returns_404(client):
    response = client.delete("/api/security/assets/AST-DOES-NOT-EXIST")
    assert response.status_code == 404


def test_delete_asset_with_gate_pass_is_refused(client):
    asset_id = _create_asset(client, "RFID-DEL-3")
    gate_pass = client.post(
        "/api/security/gate-passes",
        json={"asset_id": asset_id, "issued_to_employee_id": "EMP-1"},
    )
    assert gate_pass.status_code == 200

    response = client.delete(f"/api/security/assets/{asset_id}")
    assert response.status_code == 409
    assert response.json()["detail"]["gate_passes"] >= 1

    from database import get_db_connection

    conn = get_db_connection()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM gate_passes WHERE asset_id NOT IN (SELECT id FROM asset_register)"
    ).fetchone()[0]
    conn.close()
    assert orphans == 0


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)