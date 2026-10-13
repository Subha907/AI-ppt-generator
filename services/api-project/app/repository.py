"""Single-table DynamoDB data access (PDF section 9).

Table: ArchitectureGenerator
Access patterns (PK / SK):
  Project        PK=TENANT#{tenantId}   SK=PROJECT#{projectId}
  Document       PK=PROJECT#{projectId} SK=DOCUMENT#{documentId}
  Job            PK=PROJECT#{projectId} SK=JOB#{jobId}
  Artifact       PK=PROJECT#{projectId} SK=ARTIFACT#{artifactId}
  Idempotency    PK=TENANT#{tenantId}   SK=KEY#{idempotencyKey}

Tenant isolation: project reads/writes are keyed by TENANT#{tenantId}, so a
caller can never read a project outside their tenant. Document/job/artifact
records hang off PROJECT#{projectId}; callers must first resolve the project
within their tenant (get_project) before touching child records, which is how
ownership is enforced at the service layer.
"""
from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from .aws import dynamodb_resource
from .config import get_settings
from .models import (
    ArchitectureType,
    IngestionStatus,
    JobStatus,
    ProjectStatus,
    utcnow,
)


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


def _table():
    return dynamodb_resource().Table(get_settings().ddb_table)


# --- Key builders ----------------------------------------------------------
def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _project_pk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


def _project_sk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


def _document_sk(document_id: str) -> str:
    return f"DOCUMENT#{document_id}"


def _job_sk(job_id: str) -> str:
    return f"JOB#{job_id}"


def _artifact_sk(artifact_id: str) -> str:
    return f"ARTIFACT#{artifact_id}"


def _idempotency_sk(key: str) -> str:
    return f"KEY#{key}"


# --- Projects --------------------------------------------------------------
def create_project(
    tenant_id: str,
    owner_user_id: str,
    project_id: str,
    name: str,
    description: str | None,
    default_theme_id: str,
) -> dict[str, Any]:
    now = utcnow()
    item = {
        "PK": _tenant_pk(tenant_id),
        "SK": _project_sk(project_id),
        "entity": "PROJECT",
        "tenantId": tenant_id,
        "projectId": project_id,
        "ownerUserId": owner_user_id,
        "name": name,
        "description": description,
        "status": ProjectStatus.CREATED.value,
        "kbStatus": IngestionStatus.QUEUED.value,
        "defaultThemeId": default_theme_id,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        _table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError(f"Project {project_id} already exists") from exc
        raise
    return item


def get_project(tenant_id: str, project_id: str) -> dict[str, Any]:
    resp = _table().get_item(
        Key={"PK": _tenant_pk(tenant_id), "SK": _project_sk(project_id)}
    )
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"Project {project_id} not found in tenant scope")
    return item


def list_projects(tenant_id: str) -> list[dict[str, Any]]:
    resp = _table().query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": _tenant_pk(tenant_id),
            ":sk": "PROJECT#",
        },
    )
    return resp.get("Items", [])


def update_project_kb_status(
    tenant_id: str,
    project_id: str,
    kb_status: IngestionStatus,
    last_ingestion_job_id: str | None = None,
) -> dict[str, Any]:
    # Ensures the project exists (and is in-tenant) before updating.
    expr = "SET kbStatus = :kb, updatedAt = :ts"
    values: dict[str, Any] = {":kb": kb_status.value, ":ts": utcnow()}
    if last_ingestion_job_id is not None:
        expr += ", lastIngestionJobId = :lij"
        values[":lij"] = last_ingestion_job_id
    try:
        resp = _table().update_item(
            Key={"PK": _tenant_pk(tenant_id), "SK": _project_sk(project_id)},
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFoundError(f"Project {project_id} not found") from exc
        raise
    return resp["Attributes"]


# --- Documents -------------------------------------------------------------
def create_document(
    project_id: str,
    document_id: str,
    file_name: str,
    content_type: str,
    size_bytes: int,
    s3_key: str,
    sha256: str | None,
) -> dict[str, Any]:
    now = utcnow()
    item = {
        "PK": _project_pk(project_id),
        "SK": _document_sk(document_id),
        "entity": "DOCUMENT",
        "projectId": project_id,
        "documentId": document_id,
        "fileName": file_name,
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "s3Key": s3_key,
        "sha256": sha256,
        "ingestionStatus": IngestionStatus.QUEUED.value,
        "createdAt": now,
        "updatedAt": now,
    }
    _table().put_item(Item=item)
    return item


def get_document(project_id: str, document_id: str) -> dict[str, Any]:
    resp = _table().get_item(
        Key={"PK": _project_pk(project_id), "SK": _document_sk(document_id)}
    )
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"Document {document_id} not found")
    return item


def list_documents(project_id: str) -> list[dict[str, Any]]:
    resp = _table().query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": _project_pk(project_id),
            ":sk": "DOCUMENT#",
        },
    )
    return resp.get("Items", [])


def update_document_status(
    project_id: str, document_id: str, status: IngestionStatus
) -> dict[str, Any]:
    resp = _table().update_item(
        Key={"PK": _project_pk(project_id), "SK": _document_sk(document_id)},
        UpdateExpression="SET ingestionStatus = :s, updatedAt = :ts",
        ExpressionAttributeValues={":s": status.value, ":ts": utcnow()},
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]


# --- Jobs ------------------------------------------------------------------
def create_job(
    project_id: str,
    job_id: str,
    architecture_type: str,
    document_ids: list[str],
    instruction: str,
    slide_profile: str,
    output_format: str,
    execution_arn: str | None = None,
) -> dict[str, Any]:
    """architecture_type is the resolved rule-pack id (e.g.
    'deployment.network_focused') or a legacy family word."""
    now = utcnow()
    item = {
        "PK": _project_pk(project_id),
        "SK": _job_sk(job_id),
        "entity": "JOB",
        "projectId": project_id,
        "jobId": job_id,
        "architectureType": architecture_type,
        "documentIds": document_ids,
        "instruction": instruction,
        "slideProfile": slide_profile,
        "outputFormat": output_format,
        "executionArn": execution_arn,
        "status": JobStatus.QUEUED.value,
        "currentState": None,
        "progressPercent": 0,
        "attempt": 0,
        "artifactIds": [],
        "createdAt": now,
        "updatedAt": now,
    }
    _table().put_item(Item=item)
    return item


def get_job(project_id: str, job_id: str) -> dict[str, Any]:
    resp = _table().get_item(
        Key={"PK": _project_pk(project_id), "SK": _job_sk(job_id)}
    )
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"Job {job_id} not found")
    return item


def list_jobs(project_id: str) -> list[dict[str, Any]]:
    resp = _table().query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": _project_pk(project_id),
            ":sk": "JOB#",
        },
    )
    return resp.get("Items", [])


# --- Idempotency -----------------------------------------------------------
def put_idempotency_key(
    tenant_id: str, key: str, request_hash: str, response_ref: str, ttl_epoch: int
) -> bool:
    """Store an idempotency key. Returns False if it already exists."""
    try:
        _table().put_item(
            Item={
                "PK": _tenant_pk(tenant_id),
                "SK": _idempotency_sk(key),
                "entity": "IDEMPOTENCY",
                "requestHash": request_hash,
                "responseRef": response_ref,
                "expiresAt": ttl_epoch,
            },
            ConditionExpression="attribute_not_exists(SK)",
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def get_idempotency_key(tenant_id: str, key: str) -> dict[str, Any] | None:
    resp = _table().get_item(
        Key={"PK": _tenant_pk(tenant_id), "SK": _idempotency_sk(key)}
    )
    return resp.get("Item")
