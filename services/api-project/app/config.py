"""Runtime configuration for the API and Project Service.

Values are read from environment variables (optionally via a local `.env`
file). The same settings object is used whether the service runs on your
laptop or on ECS Fargate - only the values differ.
"""
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

    # Resource names (from CloudFormation stack outputs)
    data_bucket: str = ""
    vector_bucket: str = ""
    ddb_table: str = "ArchitectureGenerator"
    knowledge_base_id: str = ""
    data_source_id: str = ""

    # Local dev identity (stands in for Cognito claims)
    local_tenant_id: str = "tenant-local-dev"
    local_user_id: str = "user-local-dev"

    # Upload constraints
    presign_expiry_seconds: int = 900
    max_upload_bytes: int = 52_428_800  # 50 MiB

    def require(self, *names: str) -> None:
        """Raise if any required resource setting is unset.

        Used by endpoints that touch AWS so we fail with a clear message
        instead of an opaque boto3 error when the stack isn't wired up yet.
        """
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "Missing required configuration: "
                + ", ".join(missing.upper() for missing in missing)
                + ". Populate them in .env from the CloudFormation stack outputs."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
