"""Read the layout model + job and persist the draft PPTX + preview to S3.

Artifact keys (PDF section 9.1):
  read:  tenants/{t}/projects/{p}/jobs/{j}/models/layout-model.json
  write: tenants/{t}/projects/{p}/jobs/{j}/outputs/version-{n}/architecture.pptx
         tenants/{t}/projects/{p}/jobs/{j}/previews/attempt-{n}/slide-1.png
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .aws import dynamodb_resource, s3_client
from .config import get_settings


class NotFoundError(Exception):
    pass


def _table():
    return dynamodb_resource().Table(get_settings().ddb_table)


def get_job(project_id: str, job_id: str) -> dict[str, Any]:
    resp = _table().get_item(
        Key={"PK": f"PROJECT#{project_id}", "SK": f"JOB#{job_id}"}
    )
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"Job {job_id} not found in project {project_id}")
    return item


def update_job_status(
    project_id: str, job_id: str, status: str,
    current_state: str | None = None, progress_percent: int | None = None,
) -> None:
    expr = ["#s = :s", "updatedAt = :ts"]
    values: dict[str, Any] = {
        ":s": status,
        ":ts": datetime.now(timezone.utc).isoformat(),
    }
    if current_state is not None:
        expr.append("currentState = :cs")
        values[":cs"] = current_state
    if progress_percent is not None:
        expr.append("progressPercent = :pp")
        values[":pp"] = progress_percent
    _table().update_item(
        Key={"PK": f"PROJECT#{project_id}", "SK": f"JOB#{job_id}"},
        UpdateExpression="SET " + ", ".join(expr),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
    )


def _job_prefix(tenant_id: str, project_id: str, job_id: str) -> str:
    # Artifacts live OUTSIDE the KB-scanned "tenants/" tree so the knowledge
    # base never re-ingests the pipeline's own generated PPTX/PNG/JSON.
    return f"artifacts/{tenant_id}/projects/{project_id}/jobs/{job_id}"


def get_layout_model(tenant_id: str, project_id: str, job_id: str) -> dict:
    settings = get_settings()
    settings.require("data_bucket")
    key = f"{_job_prefix(tenant_id, project_id, job_id)}/models/layout-model.json"
    try:
        obj = s3_client().get_object(Bucket=settings.data_bucket, Key=key)
    except s3_client().exceptions.NoSuchKey as exc:
        raise NotFoundError(
            f"layout-model.json not found at s3://{settings.data_bucket}/{key}. "
            "Run Agent 2 (Layout) first."
        ) from exc
    return json.loads(obj["Body"].read())


def put_pptx(
    tenant_id: str, project_id: str, job_id: str, version: int, data: bytes
) -> str:
    settings = get_settings()
    key = (
        f"{_job_prefix(tenant_id, project_id, job_id)}"
        f"/outputs/version-{version}/architecture.pptx"
    )
    s3_client().put_object(
        Bucket=settings.data_bucket,
        Key=key,
        Body=data,
        ContentType=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )
    return f"s3://{settings.data_bucket}/{key}"


def put_preview(
    tenant_id: str, project_id: str, job_id: str, attempt: int, data: bytes
) -> str:
    settings = get_settings()
    key = (
        f"{_job_prefix(tenant_id, project_id, job_id)}"
        f"/previews/attempt-{attempt}/slide-1.png"
    )
    s3_client().put_object(
        Bucket=settings.data_bucket, Key=key, Body=data, ContentType="image/png"
    )
    return f"s3://{settings.data_bucket}/{key}"


# --- Agent 4 (Visual Reviewer) artifacts -----------------------------------
def get_preview(tenant_id: str, project_id: str, job_id: str, attempt: int) -> bytes:
    settings = get_settings()
    settings.require("data_bucket")
    key = (
        f"{_job_prefix(tenant_id, project_id, job_id)}"
        f"/previews/attempt-{attempt}/slide-1.png"
    )
    try:
        obj = s3_client().get_object(Bucket=settings.data_bucket, Key=key)
    except s3_client().exceptions.NoSuchKey as exc:
        raise NotFoundError(
            f"preview not found at s3://{settings.data_bucket}/{key}. "
            "Run Agent 3 (Compiler) first."
        ) from exc
    return obj["Body"].read()


def put_review_report(
    tenant_id: str, project_id: str, job_id: str, attempt: int, report: dict
) -> str:
    settings = get_settings()
    key = f"{_job_prefix(tenant_id, project_id, job_id)}/reviews/attempt-{attempt}.json"
    s3_client().put_object(
        Bucket=settings.data_bucket,
        Key=key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{settings.data_bucket}/{key}"
