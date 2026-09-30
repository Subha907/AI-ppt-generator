"""Runtime configuration for the Layout Service (Agent 2)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AWS access
    aws_profile: str | None = None
    aws_region: str = "us-west-2"

    # Shared resources
    data_bucket: str = ""
    ddb_table: str = "ArchitectureGenerator"

    # Slide profile (16:9)
    slide_width_in: float = 13.333
    slide_height_in: float = 7.5
    safe_margin_in: float = 0.3

    # Local dev identity
    local_tenant_id: str = "tenant-local-dev"

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "Missing required configuration: "
                + ", ".join(n.upper() for n in missing)
                + ". Populate them in .env."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
