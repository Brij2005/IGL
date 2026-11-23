import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("IGL_DATA_DIR", str(BACKEND_DIR / "data")))
EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", str(BACKEND_DIR / "evidence")))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", str(BACKEND_DIR / "reports")))
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(BACKEND_DIR / "models")))

for directory in (DATA_DIR, EVIDENCE_DIR, REPORTS_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-igl-secret-change-in-production")
ACCESS_TOKEN_TTL_MINUTES = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "720"))
ENABLE_AUTH = os.getenv("ENABLE_AUTH", "false").lower() == "true"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe-IGL-2026!")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_EVIDENCE_IMAGE_BASE64 = int(os.getenv("MAX_EVIDENCE_IMAGE_BASE64", str(8 * 1024 * 1024)))
CAMERA_RECONNECT_MAX_ATTEMPTS = int(os.getenv("CAMERA_RECONNECT_MAX_ATTEMPTS", "5"))
CAMERA_READ_TIMEOUT_SECONDS = float(os.getenv("CAMERA_READ_TIMEOUT_SECONDS", "5"))
LEAKAGE_MIN_FRAMES = int(os.getenv("LEAKAGE_MIN_FRAMES", "3"))
LEAKAGE_COOLDOWN_SECONDS = int(os.getenv("LEAKAGE_COOLDOWN_SECONDS", "180"))
ALERT_DEDUP_SECONDS = int(os.getenv("ALERT_DEDUP_SECONDS", "120"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT in {"prod", "production"}

INSECURE_DEFAULT_SECRET = "dev-only-igl-secret-change-in-production"
INSECURE_DEFAULT_PASSWORD = "ChangeMe-IGL-2026!"


def validate_production_config() -> list:
    """Return a list of unsafe production settings (empty means safe).

    Called at startup. In production the application refuses to start when any
    unsafe condition is present, so authentication can never be silently off
    and default credentials can never reach production.
    """
    if not IS_PRODUCTION:
        return []
    problems = []
    if not ENABLE_AUTH:
        problems.append("ENABLE_AUTH must be true in production")
    if SECRET_KEY == INSECURE_DEFAULT_SECRET or len(SECRET_KEY) < 32:
        problems.append("SECRET_KEY must be a strong unique value (>= 32 chars)")
    if ADMIN_PASSWORD == INSECURE_DEFAULT_PASSWORD or len(ADMIN_PASSWORD) < 12:
        problems.append("ADMIN_PASSWORD must be strong and not the development default")
    return problems