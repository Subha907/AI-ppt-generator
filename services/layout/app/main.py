"""Layout Service (Agent 2) - internal HTTP interface (PDF section 10).

Endpoints:
  POST /internal/v1/layouts/generate  - build + validate + persist a layout
  POST /internal/v1/layouts/validate  - validate a supplied layout model only

Run locally:
    uvicorn app.main:app --port 8002
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .agent import run_from_job
from .aws import SSOAuthError, verify_sso
from .store import NotFoundError
from .validation import ValidationFailure, validate

app = FastAPI(
    title="Layout Service (Agent 2)",
    description="Layout & Geometric agent: deterministic 16:9 slide layout.",
    version="1.0.0",
)


class GenerateRequest(BaseModel):
    projectId: str
    jobId: str
    tenantId: str | None = None
    engine: str = "stack"  # stack | stack-abs | graphviz | grid


class ValidateRequest(BaseModel):
    layout: dict


@app.get("/healthz")
def healthz():
    try:
        who = verify_sso()
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "aws": who}


@app.post("/internal/v1/layouts/generate")
def generate_layout(
    body: GenerateRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    tenant_id = x_tenant_id or body.tenantId
    try:
        result = run_from_job(body.projectId, body.jobId, tenant_id, engine=body.engine)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationFailure as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "LAYOUT_VALIDATION_FAILED", "errors": exc.errors},
        ) from exc
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.__dict__


@app.post("/internal/v1/layouts/validate")
def validate_layout(body: ValidateRequest):
    """Validate a supplied layout model without persisting (Agent 2 validator)."""
    try:
        model = validate(body.layout)
    except ValidationFailure as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "LAYOUT_VALIDATION_FAILED", "errors": exc.errors},
        ) from exc
    return {"valid": True, "validation": model.get("validation", {})}
