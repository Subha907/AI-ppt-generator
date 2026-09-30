"""Identity resolution.

In production, tenantId/userId come from verified Cognito JWT claims and are
NEVER trusted from the client (PDF section 7 + security notes). Locally we
inject a fixed dev identity from config so the tenant-isolation logic still
runs end-to-end. Swapping this for a real JWT validator later does not change
any downstream code, which only sees the resolved Identity.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import get_settings


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str


def current_identity() -> Identity:
    settings = get_settings()
    return Identity(
        tenant_id=settings.local_tenant_id,
        user_id=settings.local_user_id,
    )
