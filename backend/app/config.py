"""Global application settings and environment configuration."""
from pathlib import Path
from pydantic import Field
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_SECRET_KEY = "development-only-secret-change-before-production-2026"


class Settings(BaseSettings):
    """Platform configuration settings loaded from environment variables."""
    PROJECT_NAME: str = "IGL Industrial AI Safety Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    # Security & JWT
    SECRET_KEY: SecretStr = DEVELOPMENT_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours
    LOGIN_RATE_LIMIT_ATTEMPTS: int = Field(default=5, ge=1, le=100)
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=900, ge=1, le=86400)
    MINIMUM_ADMIN_PASSWORD_LENGTH: int = Field(default=12, ge=12, le=128)

    # First-admin bootstrap. Disabled unless an operator explicitly enables it
    # and supplies every value. There is no default password and no fallback.
    BOOTSTRAP_ADMIN_ENABLED: bool = False
    BOOTSTRAP_ADMIN_USERNAME: str | None = None
    BOOTSTRAP_ADMIN_EMAIL: str | None = None
    BOOTSTRAP_ADMIN_FULL_NAME: str | None = None
    BOOTSTRAP_ADMIN_PASSWORD: SecretStr | None = None

    # Database
    DATABASE_URL: str = f"sqlite:///{Path(__file__).resolve().parents[2] / 'data' / 'database.db'}"
    SQLALCHEMY_ECHO: bool = False

    # CORS
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:5500"]

    # Phase 4 inference configuration. Empty weights never trigger downloads.
    VIDEO_SOURCE: str | None = None
    RTSP_URL: SecretStr | None = None
    CAMERA_URL_ENCRYPTION_KEY: SecretStr | None = None
    MODEL_WEIGHTS_PATH: str | None = None
    MODEL_NAME: str | None = None
    MODEL_VERSION: str | None = None
    MODEL_CONFIDENCE_THRESHOLD: float = 0.25
    MODEL_DEVICE: str = "cpu"
    FRAME_BUFFER_RETENTION_SECONDS: float = 30.0
    FRAME_BUFFER_MAX_FRAMES: int = 100
    FRAME_BUFFER_MAX_BYTES: int = 67_108_864
    CAMERA_HEALTH_INTERVAL_SECONDS: float = Field(default=5.0, gt=0, le=300)
    EVIDENCE_DIR: str = str(Path(__file__).resolve().parents[2] / "data" / "evidence")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def validate_security_settings(self):
        if "*" in self.BACKEND_CORS_ORIGINS:
            raise ValueError("Wildcard CORS origins are not allowed")
        if self.ENVIRONMENT == "production":
            secret = self.SECRET_KEY.get_secret_value()
            if secret == DEVELOPMENT_SECRET_KEY or len(secret) < 32:
                raise ValueError("Production requires a non-default SECRET_KEY of at least 32 characters")
        if self.BOOTSTRAP_ADMIN_ENABLED:
            missing = [
                name
                for name in (
                    "BOOTSTRAP_ADMIN_USERNAME",
                    "BOOTSTRAP_ADMIN_EMAIL",
                    "BOOTSTRAP_ADMIN_FULL_NAME",
                    "BOOTSTRAP_ADMIN_PASSWORD",
                )
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "BOOTSTRAP_ADMIN_ENABLED requires operator-supplied values for: "
                    + ", ".join(missing)
                )
        return self


settings = Settings()
