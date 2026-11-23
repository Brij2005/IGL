"""Shared pytest configuration for the IGL VisionGuard backend tests.

A single temporary database path is configured for the whole test session so
that all test modules share one initialized database and do not overwrite each
other's `DATABASE_PATH`. Individual test modules no longer set their own path.
"""

import os
import shutil
import tempfile

_TEST_ROOT = tempfile.mkdtemp(prefix="igl-visionguard-test-")

os.environ.setdefault("DATABASE_PATH", os.path.join(_TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "ChangeMe-IGL-2026!")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(_TEST_ROOT, "evidence"))
os.environ.setdefault("REPORTS_DIR", os.path.join(_TEST_ROOT, "reports"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)