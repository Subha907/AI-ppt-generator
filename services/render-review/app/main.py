"""Rendering & Review Service (Agent 3) - internal HTTP interface (PDF section 10).

Endpoints:
  POST /internal/v1/presentations/compile - compile + validate + render + persist

Run locally:
    uvicorn app.main:app --port 8003
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .agent import compile_from_job
from .aws import SSOAuthError, verify_sso
from .review_agent import review_from_job
from .review_validation import ReviewValidationFailure
from .store import NotFoundError
from .validation import ValidationFailure

app = FastAPI(
    title="Rendering & Review Service (Agent 3)",
    description="Compiler agent: builds an editable PPTX from the layout model.",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    projectId: str
    jobId: str
    tenantId: str | None = None
    version: int = 1
    attempt: int = 1


class ReviewRequest(BaseModel):
    projectId: str
    jobId: str
    tenantId: str | None = None
    attempt: int = 1
    provider: str | None = None  # nova | stub


@app.get("/healthz")
def healthz():
    try:
        who = verify_sso()
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "aws": who}


@app.post("/internal/v1/presentations/compile")
def compile_presentation(
    body: CompileRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    tenant_id = x_tenant_id or body.tenantId
    try:
        result = compile_from_job(
            body.projectId, body.jobId, tenant_id, body.version, body.attempt
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationFailure as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PPTX_COMPILE_FAILED", "errors": exc.errors},
        ) from exc
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.__dict__


@app.post("/internal/v1/presentations/review")
def review_presentation(
    body: ReviewRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    tenant_id = x_tenant_id or body.tenantId
    try:
        result = review_from_job(
            body.projectId, body.jobId, tenant_id, body.attempt, body.provider
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewValidationFailure as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REVIEW_REPORT_INVALID", "errors": exc.errors},
        ) from exc
    except SSOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.__dict__
