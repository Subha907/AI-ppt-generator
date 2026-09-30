"""AWS client factory.

Every AWS call this service makes goes through the boto3 session created here,
and that session is pinned to your **SSO profile** (``AWS_PROFILE`` in ``.env``).

How credential resolution works:
  * The session is built with ``profile_name=<your SSO profile>``.
  * boto3 reads that profile from ``~/.aws/config`` where it is defined as an
    ``sso_*`` profile.
  * It then uses the cached SSO token created by ``aws sso login --profile <p>``
    to fetch short-lived role credentials automatically.

Nothing here stores, prints, or handles long-lived secrets. If the SSO token is
missing or expired, we raise a clear, actionable error telling you to log in.
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
            "AWS_PROFILE is not set. This service is configured to authenticate "
            "through AWS SSO. Set AWS_PROFILE in .env to your SSO profile name."
        )
    return boto3.session.Session(
        profile_name=settings.aws_profile,
        region_name=settings.aws_region,
    )


_boto_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

# S3 needs regional endpoints + SigV4 so presigned URLs point directly at the
# bucket's region (avoids 307 redirects on fresh buckets outside us-east-1).
_s3_config = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
)


def _client(service: str):
    return _session().client(service, config=_boto_config)


@lru_cache
def s3_client():
    return _session().client("s3", config=_s3_config)


@lru_cache
def dynamodb_resource():
    return _session().resource("dynamodb", config=_boto_config)


@lru_cache
def bedrock_agent_client():
    """Control-plane client: manage KB ingestion jobs."""
    return _client("bedrock-agent")


@lru_cache
def bedrock_agent_runtime_client():
    """Data-plane client: retrieve from the knowledge base."""
    return _client("bedrock-agent-runtime")


def verify_sso() -> dict:
    """Confirm the app can authenticate via the configured SSO profile.

    Returns the resolved profile, region, and STS caller identity so you can
    see exactly which account/role the app is using. Raises SSOAuthError with
    a clear next step if the SSO session is missing or expired.
    """
    settings = get_settings()
    try:
        identity = _client("sts").get_caller_identity()
    except (
        SSOTokenLoadError,
        UnauthorizedSSOTokenError,
        TokenRetrievalError,
    ) as exc:
        raise SSOAuthError(
            f"AWS SSO session for profile '{settings.aws_profile}' is missing or "
            f"expired. Run:\n\n    aws sso login --profile {settings.aws_profile}\n\n"
            f"then restart the service. (underlying error: {exc})"
        ) from exc

    return {
        "profile": settings.aws_profile,
        "region": settings.aws_region,
        "account": identity.get("Account"),
        "arn": identity.get("Arn"),
        "userId": identity.get("UserId"),
    }


# Backwards-compatible alias
def whoami() -> dict:
    return verify_sso()
