"""API and Project Service - FastAPI application.

Implements the setup-phase endpoints from PDF section 7: project CRUD,
presigned RFP upload, upload completion + KB ingestion trigger, and knowledge
readiness status. This service hosts NO agent; it prepares the project +
ingested RFPs that the four-agent pipeline later consumes.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from . import catalog as catalog_mod
from . import knowledge_base as kb
from . import repository as repo
from . import storage
from .auth import Identity, current_identity
from .aws import SSOAuthError, verify_sso
from .config import get_settings
from .ids import new_document_id, new_job_id, new_project_id
from .models import (
    ArchitectureCategory,
    CompleteUploadRequest,
    CreateProjectRequest,
    DocumentResponse,
    IngestionStatus,
    JobResponse,
    KnowledgeStatusResponse,
    PresignRequest,
    PresignResponse,
    ProjectResponse,
    StartJobRequest,
)

app = FastAPI(
    title="API and Project Service",
    description="Setup-phase service for the Multi-Agent AWS Architecture Slide Generator.",
    version="1.0.0",
)

API_PREFIX = "/api/v1"


# --- Dependencies ----------------------------------------------------------
def identity() -> Identity:
    return current_identity()


def _project_response(item: dict) -> ProjectResponse:
    return ProjectResponse(
        projectId=item["projectId"],
        name=item["name"],
        description=item.get("description"),
        status=item["status"],
        kbStatus=item.get("kbStatus"),
        defaultThemeId=item.get("defaultThemeId", "aws-standard-v1"),
        createdAt=item["createdAt"],
        updatedAt=item["updatedAt"],
    )


# --- Health / identity -----------------------------------------------------
@app.get("/healthz")
def healthz():
    """Confirms the service is up AND authenticated to AWS via SSO."""
    try:
        who = verify_sso()
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "aws": who}


# --- Architecture-type catalog (diagram-type picker) -----------------------
@app.get(f"{API_PREFIX}/architecture-types", response_model=list[ArchitectureCategory])
def architecture_types():
    """Return the selectable diagram types as category -> sub-types.

    Derived live from the semantic rule packs, so a UI can populate a
    category dropdown and a dependent sub-category dropdown. Adding a new
    rule-pack YAML automatically adds an option here.
    """
    return catalog_mod.catalog()


# --- Projects --------------------------------------------------------------
@app.post(f"{API_PREFIX}/projects", response_model=ProjectResponse, status_code=201)
def create_project(body: CreateProjectRequest, ident: Identity = Depends(identity)):
    project_id = new_project_id()
    item = repo.create_project(
        tenant_id=ident.tenant_id,
        owner_user_id=ident.user_id,
        project_id=project_id,
        name=body.name,
        description=body.description,
        default_theme_id=body.defaultThemeId,
    )
    return _project_response(item)


@app.get(f"{API_PREFIX}/projects", response_model=list[ProjectResponse])
def list_projects(ident: Identity = Depends(identity)):
    return [_project_response(i) for i in repo.list_projects(ident.tenant_id)]


@app.get(f"{API_PREFIX}/projects/{{project_id}}", response_model=ProjectResponse)
def get_project(project_id: str, ident: Identity = Depends(identity)):
    try:
        item = repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _project_response(item)


# --- Documents: presign + complete (ingest) --------------------------------
@app.post(
    f"{API_PREFIX}/projects/{{project_id}}/documents/presign",
    response_model=PresignResponse,
)
def presign_upload(
    project_id: str, body: PresignRequest, ident: Identity = Depends(identity)
):
    settings = get_settings()
    if body.sizeBytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    if body.contentType not in ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        raise HTTPException(status_code=415, detail="Unsupported file type (PDF/DOCX only)")

    # Ownership check: project must exist in caller's tenant.
    try:
        repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    document_id = new_document_id()
    object_key = storage.source_object_key(
        ident.tenant_id, project_id, document_id, body.fileName
    )
    upload_url, expires = storage.create_presigned_put(object_key, body.contentType)

    # Register the pending document so complete-upload can validate it.
    repo.create_document(
        project_id=project_id,
        document_id=document_id,
        file_name=body.fileName,
        content_type=body.contentType,
        size_bytes=body.sizeBytes,
        s3_key=object_key,
        sha256=body.sha256,
    )
    return PresignResponse(
        documentId=document_id,
        uploadUrl=upload_url,
        objectKey=object_key,
        expiresInSeconds=expires,
    )


@app.post(
    f"{API_PREFIX}/projects/{{project_id}}/documents/complete", status_code=202
)
def complete_upload(
    project_id: str, body: CompleteUploadRequest, ident: Identity = Depends(identity)
):
    # Ownership + existence checks.
    try:
        repo.get_project(ident.tenant_id, project_id)
        doc = repo.get_document(project_id, body.documentId)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Confirm the object really landed in S3 before ingesting.
    try:
        storage.head_object(doc["s3Key"])
    except Exception as exc:  # botocore ClientError (404) etc.
        raise HTTPException(
            status_code=409, detail=f"Uploaded object not found in S3: {exc}"
        ) from exc

    # Trigger KB ingestion and record status.
    repo.update_document_status(project_id, body.documentId, IngestionStatus.INGESTING)
    ingestion_job_id = kb.start_ingestion(
        description=f"Ingest {body.documentId} for project {project_id}"
    )
    repo.update_project_kb_status(
        ident.tenant_id, project_id, IngestionStatus.INGESTING, ingestion_job_id
    )
    return {
        "documentId": body.documentId,
        "ingestionStatus": IngestionStatus.INGESTING.value,
        "ingestionJobId": ingestion_job_id,
    }


@app.get(
    f"{API_PREFIX}/projects/{{project_id}}/documents",
    response_model=list[DocumentResponse],
)
def list_documents(project_id: str, ident: Identity = Depends(identity)):
    try:
        repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    docs = repo.list_documents(project_id)
    return [
        DocumentResponse(
            documentId=d["documentId"],
            fileName=d["fileName"],
            contentType=d["contentType"],
            sizeBytes=int(d["sizeBytes"]),
            s3Key=d["s3Key"],
            ingestionStatus=d["ingestionStatus"],
            createdAt=d["createdAt"],
        )
        for d in docs
    ]


# --- Knowledge readiness ---------------------------------------------------
@app.get(
    f"{API_PREFIX}/projects/{{project_id}}/knowledge-status",
    response_model=KnowledgeStatusResponse,
)
def knowledge_status(project_id: str, ident: Identity = Depends(identity)):
    try:
        project = repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    last_job = project.get("lastIngestionJobId")
    # Prefer polling the specific job recorded on the project.
    if last_job:
        status, _ = kb.get_ingestion_status(last_job)
    else:
        status = kb.latest_ingestion_status()

    # Reflect terminal status back onto the project + its documents.
    if status in (IngestionStatus.READY, IngestionStatus.FAILED):
        repo.update_project_kb_status(ident.tenant_id, project_id, status, last_job)
        for d in repo.list_documents(project_id):
            if d.get("ingestionStatus") not in (
                IngestionStatus.READY.value,
                IngestionStatus.FAILED.value,
            ):
                repo.update_document_status(project_id, d["documentId"], status)

    docs = repo.list_documents(project_id)
    ready = sum(1 for d in docs if d.get("ingestionStatus") == IngestionStatus.READY.value)
    return KnowledgeStatusResponse(
        projectId=project_id,
        kbStatus=status,
        documentCount=len(docs),
        readyCount=ready,
        lastIngestionJobId=last_job,
    )


# --- Slide jobs (creates job record; orchestration wired later) ------------
@app.post(
    f"{API_PREFIX}/projects/{{project_id}}/slide-jobs",
    response_model=JobResponse,
    status_code=202,
)
def start_slide_job(
    project_id: str,
    body: StartJobRequest,
    ident: Identity = Depends(identity),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Resolve the user's selection (category + subCategory, or a legacy word /
    # direct pack id) to a canonical rule-pack id via the catalog.
    try:
        resolved_type = catalog_mod.resolve(
            body.category, body.subCategory, body.architectureType
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Idempotency: a repeated key returns the original job instead of a new one.
    if idempotency_key:
        req_hash = hashlib.sha256(
            (project_id + body.model_dump_json()).encode()
        ).hexdigest()
        existing = repo.get_idempotency_key(ident.tenant_id, idempotency_key)
        if existing:
            job = repo.get_job(project_id, existing["responseRef"])
            return _job_response(job)

    job_id = new_job_id()
    job = repo.create_job(
        project_id=project_id,
        job_id=job_id,
        architecture_type=resolved_type,
        document_ids=body.documentIds,
        instruction=body.instruction,
        slide_profile=body.slideProfile,
        output_format=body.outputFormat,
    )
    if idempotency_key:
        repo.put_idempotency_key(
            ident.tenant_id,
            idempotency_key,
            req_hash,
            job_id,
            ttl_epoch=int(time.time()) + 86400,
        )
    return _job_response(job)


@app.get(
    f"{API_PREFIX}/projects/{{project_id}}/slide-jobs/{{job_id}}",
    response_model=JobResponse,
)
def get_slide_job(project_id: str, job_id: str, ident: Identity = Depends(identity)):
    try:
        repo.get_project(ident.tenant_id, project_id)
        job = repo.get_job(project_id, job_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_response(job)


@app.get(
    f"{API_PREFIX}/projects/{{project_id}}/slide-jobs",
    response_model=list[JobResponse],
)
def list_slide_jobs(project_id: str, ident: Identity = Depends(identity)):
    try:
        repo.get_project(ident.tenant_id, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_job_response(j) for j in repo.list_jobs(project_id)]


def _job_response(item: dict) -> JobResponse:
    return JobResponse(
        jobId=item["jobId"],
        projectId=item["projectId"],
        architectureType=item["architectureType"],
        status=item["status"],
        currentState=item.get("currentState"),
        progressPercent=int(item.get("progressPercent", 0)),
        attempt=int(item.get("attempt", 0)),
        createdAt=item["createdAt"],
        updatedAt=item["updatedAt"],
    )


# --- Error handling for auth issues ---------------------------------------
@app.exception_handler(SSOAuthError)
def _sso_error_handler(_request, exc: SSOAuthError):
    return JSONResponse(status_code=503, content={"error": {"code": "SSO_AUTH", "message": str(exc)}})
