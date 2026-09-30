"""Architecture Intelligence Service (Agent 1) - internal HTTP interface.

Exposes POST /internal/v1/architectures/generate (PDF section 10). Step
Functions invokes this later; the same core logic is used by the CLI (app.run).

Run locally:
    uvicorn app.main:app --port 8001
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .agent import run_from_job
from .aws import SSOAuthError, verify_sso
from .config import get_settings
from .store import NotFoundError
from .validation import ValidationFailure

app = FastAPI(
    title="Architecture Intelligence Service (Agent 1)",
    description="Solution Architect agent: RFP retrieval + architecture generation.",
    version="1.0.0",
)


class GenerateRequest(BaseModel):
    projectId: str
    jobId: str
    tenantId: str | None = None
    provider: str | None = None  # stub | nova


@app.get("/healthz")
def healthz():
    try:
        who = verify_sso()
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "aws": who, "provider": get_settings().generation_provider}


@app.post("/internal/v1/architectures/generate")
def generate_architecture(
    body: GenerateRequest,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    # Prefer the orchestrator-injected tenant header over the body (never trust
    # the client to widen scope); fall back to body/config for local use.
    tenant_id = x_tenant_id or body.tenantId
    try:
        result = run_from_job(
            project_id=body.projectId,
            job_id=body.jobId,
            tenant_id=tenant_id,
            provider=body.provider,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationFailure as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "ARCHITECTURE_SCHEMA_INVALID", "errors": exc.errors},
        ) from exc
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return result.__dict__
