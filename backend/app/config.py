"""Global application settings and environment configuration."""
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


# Environments treated as non-production for deployment-safety reporting.
LOCAL_ONLY_ENVIRONMENTS = frozenset({"development", "test"})


class Settings(BaseSettings):
    """Platform configuration settings loaded from environment variables."""
    PROJECT_NAME: str = "IGL Industrial AI Safety Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "production", "staging"] = "development"

    # Anonymous access is a development-only convenience. Shared deployments
    # use signed bearer tokens and must provide the JWT signing key.
    ALLOW_ANONYMOUS_ACCESS: bool = True
    AUTH_JWT_SECRET_KEY: SecretStr | None = None
    AUTH_JWT_ISSUER: str = "igl-safety-intelligence"
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=1440)
    INITIAL_ADMIN_USERNAME: str | None = None
    INITIAL_ADMIN_EMAIL: str | None = None
    INITIAL_ADMIN_FULL_NAME: str | None = None
    INITIAL_ADMIN_PASSWORD: SecretStr | None = None

    # Database
    DATABASE_URL: SecretStr = SecretStr(f"sqlite:///{Path(__file__).resolve().parents[2] / 'data' / 'database.db'}")
    SQLALCHEMY_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=5, ge=1, le=50)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    DATABASE_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=60, le=86400)
    DATABASE_STATEMENT_TIMEOUT_MS: int = Field(default=5000, ge=100, le=120000)
    DATABASE_CONNECT_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=120)

    # CORS
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:5500",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Logging
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"

    # Phase 4 inference configuration. Empty weights never trigger downloads.
    VIDEO_SOURCE: str | None = None
    RTSP_URL: SecretStr | None = None
    CAMERA_URL_ENCRYPTION_KEY: SecretStr | None = None
    MODEL_WEIGHTS_PATH: str | None = None
    MODEL_NAME: str | None = None
    MODEL_VERSION: str | None = None
    MODEL_CONFIDENCE_THRESHOLD: float = Field(default=0.25, ge=0.0, le=1.0)
    MODEL_DEVICE: str = "cpu"
    FRAME_BUFFER_RETENTION_SECONDS: float = Field(default=30.0, gt=0.0, le=3600.0)
    FRAME_BUFFER_MAX_FRAMES: int = Field(default=100, ge=1, le=10000)
    FRAME_BUFFER_MAX_BYTES: int = Field(default=67_108_864, ge=1_048_576)
    CAMERA_HEALTH_INTERVAL_SECONDS: float = Field(default=5.0, gt=0.5, le=300)

    # Camera ingestion. Timeouts and backoff bounds are explicit so a bad source
    # cannot spin a thread forever, and every value is operator-tunable.
    CAMERA_CONNECT_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.5, le=300.0)
    CAMERA_READ_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.5, le=300.0)
    CAMERA_RECONNECT_INITIAL_DELAY_SECONDS: float = Field(default=1.0, gt=0.1, le=60.0)
    CAMERA_RECONNECT_MAX_DELAY_SECONDS: float = Field(default=15.0, gt=1.0, le=600.0)
    CAMERA_MAX_RECONNECT_ATTEMPTS: int = Field(default=0, ge=0, le=1000)  # 0 = unlimited
    # Target capture rate used to pace a local file or stream reader. 0 means
    # "read as fast as the source delivers", which is the OpenCV default.
    CAMERA_TARGET_FPS: float = Field(default=0.0, ge=0.0, le=120.0)
    CAMERA_STALE_FRAME_SECONDS: float = Field(default=5.0, gt=0.5, le=300.0)
    CAMERA_BLACK_MEAN_BRIGHTNESS: float = Field(default=10.0, ge=0.0, le=255.0)
    CAMERA_FROZEN_MSE_THRESHOLD: float = Field(default=0.5, ge=0.0, le=10000.0)
    # Frozen detection compares this many distinct buffered frames before it can
    # conclude a source is frozen. A value below 2 disables the check, because a
    # single frame cannot distinguish a static scene from a stalled source.
    CAMERA_FROZEN_DISTINCT_FRAMES: int = Field(default=4, ge=2, le=240)
    CAMERA_SHARPNESS_REFERENCE_VARIANCE: float = Field(default=500.0, gt=0.0, le=100000.0)

    # Evidence retention. 0 disables automatic cleanup entirely.
    EVIDENCE_RETENTION_DAYS: int = Field(default=0, ge=0, le=3650)
    EVIDENCE_DIR: str = str(Path(__file__).resolve().parents[2] / "data" / "evidence")

    # Notification delivery. Nothing is sent unless a channel is configured.
    NOTIFICATION_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=20)
    NOTIFICATION_RETRY_BASE_SECONDS: float = Field(default=30.0, ge=1.0, le=3600.0)
    NOTIFICATION_RETRY_MAX_SECONDS: float = Field(default=3600.0, ge=10.0, le=86400.0)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    NOTIFICATION_FROM_ADDRESS: str | None = None
    SMTP_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.1, le=120.0)
    NOTIFICATION_RECIPIENTS: list[str] = Field(default_factory=list)
    WHATSAPP_ACCESS_TOKEN: SecretStr | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_API_VERSION: str | None = None
    WHATSAPP_RECIPIENTS: list[str] = Field(default_factory=list)
    WHATSAPP_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.1, le=120.0)
    WEBHOOK_URL: SecretStr | None = None
    WEBHOOK_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0.1, le=120.0)

    # Correlation. Larger values are truncated to this bound before hashing.
    MAX_REQUEST_ID_LENGTH: int = Field(default=64, ge=8, le=256)

    # Temporal verification defaults. These are engineering defaults used only
    # when an operator has not configured a detector policy; every event created
    # under them is tagged ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION rather than
    # presented as an IGL-validated operating rule.
    EVENT_MINIMUM_OBSERVATIONS: int = Field(default=3, ge=1, le=1000)
    EVENT_MINIMUM_DURATION_SECONDS: float = Field(default=2.0, ge=0.0, le=3600.0)
    EVENT_MAXIMUM_OBSERVATION_GAP_SECONDS: float = Field(default=5.0, gt=0.0, le=3600.0)

    # Correlation window applied when a detector configuration does not specify
    # one. Correlation never invents events; it only groups persisted ones.
    EVENT_CORRELATION_WINDOW_SECONDS: int = Field(default=300, ge=1, le=86400)

    # Safety event worker: evaluates correlation and escalation for persisted
    # events. 0 disables the worker entirely.
    SAFETY_EVENT_WORKER_INTERVAL_SECONDS: float = Field(default=10.0, ge=0.5, le=600.0)
    SAFETY_EVENT_WORKER_ENABLED: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_local_environment(self) -> bool:
        return self.ENVIRONMENT in LOCAL_ONLY_ENVIRONMENTS

    def authentication_state(self) -> str:
        """Report the access model in force, for health output and audit review."""
        if self.ALLOW_ANONYMOUS_ACCESS:
            return "ANONYMOUS_ACCESS_ENABLED_NO_AUTHENTICATION"
        return "ANONYMOUS_ACCESS_DISABLED_AUTHENTICATION_REQUIRED"

    @model_validator(mode="after")
    def validate_security_settings(self):
        if "*" in self.BACKEND_CORS_ORIGINS:
            raise ValueError("Wildcard CORS origins are not allowed")

        if not self.is_local_environment and self.ALLOW_ANONYMOUS_ACCESS:
            raise ValueError(
                "Anonymous access is only permitted in development/test"
            )
        if not self.is_local_environment and make_url(self.DATABASE_URL.get_secret_value()).get_backend_name() == "sqlite":
            raise ValueError("SQLite is local-development only; configure a production database URL")
        if not self.ALLOW_ANONYMOUS_ACCESS:
            jwt_secret = self.AUTH_JWT_SECRET_KEY.get_secret_value() if self.AUTH_JWT_SECRET_KEY else ""
            if len(jwt_secret.encode("utf-8")) < 32:
                raise ValueError("AUTH_JWT_SECRET_KEY must contain at least 32 bytes when anonymous access is disabled")
        bootstrap_values = (self.INITIAL_ADMIN_USERNAME, self.INITIAL_ADMIN_EMAIL, self.INITIAL_ADMIN_FULL_NAME, self.INITIAL_ADMIN_PASSWORD)
        if any(bootstrap_values) and not all(bootstrap_values):
            raise ValueError("INITIAL_ADMIN_USERNAME, EMAIL, FULL_NAME, and PASSWORD must be configured together")
        if self.INITIAL_ADMIN_PASSWORD:
            password_length = len(self.INITIAL_ADMIN_PASSWORD.get_secret_value().encode("utf-8"))
            if password_length < 12 or password_length > 72:
                raise ValueError("INITIAL_ADMIN_PASSWORD must contain 12 to 72 UTF-8 bytes")

        if self.CAMERA_RECONNECT_MAX_DELAY_SECONDS < self.CAMERA_RECONNECT_INITIAL_DELAY_SECONDS:
            raise ValueError("CAMERA_RECONNECT_MAX_DELAY_SECONDS must not be below the initial delay")
        if self.NOTIFICATION_RETRY_MAX_SECONDS < self.NOTIFICATION_RETRY_BASE_SECONDS:
            raise ValueError("NOTIFICATION_RETRY_MAX_SECONDS must not be below the base delay")
        if self.SMTP_USE_TLS and self.SMTP_USE_SSL:
            raise ValueError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled")
        smtp_password = self.SMTP_PASSWORD.get_secret_value().strip() if self.SMTP_PASSWORD else ""
        if bool(self.SMTP_USERNAME) != bool(smtp_password):
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")

        return self


settings = Settings()
