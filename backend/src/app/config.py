from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from env vars with prefix HERMES_CONSOLE_."""

    model_config = SettingsConfigDict(
        env_prefix="HERMES_CONSOLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Hermes Console"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./hermes_console.db"

    # JWT — HS256
    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-insecure-do-not-use-in-prod"),
        description="HS256 signing key. Override via HERMES_CONSOLE_JWT_SECRET in prod.",
    )
    jwt_access_ttl_seconds: int = 7200  # 2 hours
    jwt_refresh_ttl_seconds: int = 604800  # 7 days
    jwt_algorithm: str = "HS256"

    # Fernet master key
    master_key_path: Path = Path.home() / ".hermes-console" / "master.key"

    # Hermes installation root — default profile lives here directly (~/.hermes/.env,
    # ~/.hermes/config.yaml). Named profiles live under ~/.hermes/profiles/<name>/.
    # Override via HERMES_CONSOLE_HERMES_HOME env var.
    hermes_home: Path = Field(
        default_factory=lambda: Path.home() / ".hermes",
        description="Hermes installation root. Default profile lives here directly.",
    )

    # Where deleted Bot tar.gz archives go. Retained 30 days (M1 cleanup is manual;
    # automatic eviction lands in M2). Override via HERMES_CONSOLE_ARCHIVE_DIR.
    archive_dir: Path = Field(
        default_factory=lambda: Path.home() / ".hermes-console" / "archives",
        description="Where deleted Bot tar.gz archives go. Retained 30 days.",
    )

    # Single worker enforcement (NFR-04)
    single_worker_banner: str = "⚠ Single worker mode (M1 constraint) — do not use --workers > 1"

    # Phase 4: WS Origin allowlist (GATEWAY-08). Empty list ⇒ skip the origin
    # check (used by tests + same-origin dev). Production deployments should
    # set this to the console's public URL(s) to mitigate CSWSH attacks.
    ws_allowed_origins: list[str] = Field(
        default_factory=list,
        description="Allowed Origin headers on /ws/gateway/.../logs upgrades. Empty = skip.",
    )

    @field_validator("master_key_path", "hermes_home", "archive_dir", mode="before")
    @classmethod
    def expand_user_paths(cls, value: str | Path) -> Path:
        return Path(value).expanduser()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
