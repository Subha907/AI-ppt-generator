"""Read the architecture model + job from S3/DynamoDB and persist the layout.

Artifact keys (PDF section 9.1):
  read:  tenants/{t}/projects/{p}/jobs/{j}/models/architecture-model.json
  write: tenants/{t}/projects/{p}/jobs/{j}/models/layout-model.json
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


def _models_prefix(tenant_id: str, project_id: str, job_id: str) -> str:
    # Artifacts live OUTSIDE the KB-scanned "tenants/" tree so the knowledge
    # base never re-ingests the pipeline's own generated JSON.
    return f"artifacts/{tenant_id}/projects/{project_id}/jobs/{job_id}/models"


def get_architecture_model(tenant_id: str, project_id: str, job_id: str) -> dict:
    settings = get_settings()
    settings.require("data_bucket")
    key = f"{_models_prefix(tenant_id, project_id, job_id)}/architecture-model.json"
    try:
        obj = s3_client().get_object(Bucket=settings.data_bucket, Key=key)
    except s3_client().exceptions.NoSuchKey as exc:
        raise NotFoundError(
            f"architecture-model.json not found at s3://{settings.data_bucket}/{key}. "
            "Run Agent 1 first."
        ) from exc
    return json.loads(obj["Body"].read())


def put_layout_model(tenant_id: str, project_id: str, job_id: str, model: dict) -> str:
    settings = get_settings()
    settings.require("data_bucket")
    key = f"{_models_prefix(tenant_id, project_id, job_id)}/layout-model.json"
    s3_client().put_object(
        Bucket=settings.data_bucket,
        Key=key,
        Body=json.dumps(model, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{settings.data_bucket}/{key}"
