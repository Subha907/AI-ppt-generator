"""AWS client factory (SSO-based), shared pattern with the api-project service.

All AWS calls go through a boto3 session pinned to the SSO profile in .env.
Credentials come from the cached SSO token created by `aws sso login`.
"""
from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import (
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from .config import get_settings


class SSOAuthError(RuntimeError):
    """Raised when the SSO profile has no valid, unexpired token."""


@lru_cache
def _session() -> boto3.session.Session:
    settings = get_settings()
    if not settings.aws_profile:
        raise SSOAuthError(
            "AWS_PROFILE is not set. This service authenticates through AWS SSO."
        )
    return boto3.session.Session(
        profile_name=settings.aws_profile,
        region_name=settings.aws_region,
    )


_boto_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def _client(service: str):
    return _session().client(service, config=_boto_config)


@lru_cache
def dynamodb_resource():
    return _session().resource("dynamodb", config=_boto_config)


@lru_cache
def s3_client():
    return _client("s3")


@lru_cache
def bedrock_agent_runtime_client():
    """Data-plane client: KB retrieval."""
    return _client("bedrock-agent-runtime")


@lru_cache
def bedrock_runtime_client():
    """Model invocation (Nova Pro via Converse)."""
    return _client("bedrock-runtime")


def verify_sso() -> dict:
    settings = get_settings()
    try:
        identity = _client("sts").get_caller_identity()
    except (SSOTokenLoadError, UnauthorizedSSOTokenError, TokenRetrievalError) as exc:
        raise SSOAuthError(
            f"AWS SSO session for profile '{settings.aws_profile}' is missing or "
            f"expired. Run: aws sso login --profile {settings.aws_profile}"
        ) from exc
    return {
        "profile": settings.aws_profile,
        "region": settings.aws_region,
        "account": identity.get("Account"),
        "arn": identity.get("Arn"),
    }
