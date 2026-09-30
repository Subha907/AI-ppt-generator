"""Fetch AWS service icons from the shared S3 reference bucket.

Icons live at s3://{ICON_BUCKET}/aws-icons/{version}/{iconKey}.png. We download
each icon once per process and cache the bytes, so a slide that reuses a service
icon does not re-fetch it.
"""
from __future__ import annotations

import io
from functools import lru_cache

from .aws import s3_client
from .config import get_settings


@lru_cache(maxsize=256)
def _fetch_bytes(icon_key: str) -> bytes | None:
    settings = get_settings()
    if settings.icon_source != "s3" or not settings.icon_bucket:
        return None
    key = f"aws-icons/{settings.icon_version}/{icon_key}.png"
    try:
        obj = s3_client().get_object(Bucket=settings.icon_bucket, Key=key)
        return obj["Body"].read()
    except Exception:
        # Missing icon -> caller falls back to a colored shape.
        return None


def get_icon_stream(icon_key: str | None) -> io.BytesIO | None:
    """Return a fresh BytesIO of the icon PNG, or None if unavailable."""
    if not icon_key:
        return None
    data = _fetch_bytes(icon_key)
    if not data:
        return None
    return io.BytesIO(data)
