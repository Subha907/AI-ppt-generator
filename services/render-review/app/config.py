"""Runtime configuration for the Rendering & Review Service (Agent 3)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_profile: str | None = None
    aws_region: str = "us-west-2"

    data_bucket: str = ""
    ddb_table: str = "ArchitectureGenerator"

    # Shared reference bucket holding AWS icons (source of truth = S3).
    icon_bucket: str = ""
    icon_version: str = "v2025"
    icon_source: str = "s3"  # s3 | none (none = colored-shape fallback)

    # Agent 4 (Visual Reviewer) vision model.
    review_model_id: str = "us.amazon.nova-pro-v1:0"
    review_provider: str = "nova"  # nova | stub
    max_review_score_pass: int = 80  # decision PASS if score >= this (stub)

    preview_px_per_in: int = 96

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
