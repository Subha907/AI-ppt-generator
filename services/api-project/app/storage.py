"""S3 helpers: object-key layout and presigned uploads (PDF section 9.1).

Key layout (private data bucket):
  tenants/{tenantId}/projects/{projectId}/rfps/{documentId}/source/{fileName}

The KB data source ingests everything under the tenants/ prefix, so uploading
into this layout automatically makes the file eligible for ingestion.
"""
from __future__ import annotations

from .aws import s3_client
from .config import get_settings


def source_object_key(
    tenant_id: str, project_id: str, document_id: str, file_name: str
) -> str:
    return (
        f"tenants/{tenant_id}/projects/{project_id}"
        f"/rfps/{document_id}/source/{file_name}"
    )


def create_presigned_put(object_key: str, content_type: str) -> tuple[str, int]:
    """Return (uploadUrl, expiresInSeconds) for a direct-to-S3 PUT."""
    settings = get_settings()
    settings.require("data_bucket")
    expires = settings.presign_expiry_seconds
    url = s3_client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.data_bucket,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires,
    )
    return url, expires


def head_object(object_key: str) -> dict:
    """Confirm an uploaded object exists; raises if not found."""
    settings = get_settings()
    settings.require("data_bucket")
    return s3_client().head_object(Bucket=settings.data_bucket, Key=object_key)


def create_presigned_get(object_key: str) -> tuple[str, int]:
    """Short-lived download URL for artifacts."""
    settings = get_settings()
    settings.require("data_bucket")
    expires = settings.presign_expiry_seconds
    url = s3_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.data_bucket, "Key": object_key},
        ExpiresIn=expires,
    )
    return url, expires
