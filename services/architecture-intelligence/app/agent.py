"""Agent 1 core: retrieve -> generate -> validate -> persist.

Shared by both the CLI trigger and the internal HTTP endpoint so behavior is
identical however it is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store
from .config import get_settings
from .generation import generate
from .retrieval import retrieve
from .validation import ValidationFailure, validate


@dataclass
class RunResult:
    project_id: str
    job_id: str
    architecture_type: str
    provider: str
    evidence_count: int
    model_s3_uri: str
    node_count: int
    edge_count: int
    warnings: list[str] = field(default_factory=list)


def run_from_job(
    project_id: str,
    job_id: str,
    tenant_id: str | None = None,
    provider: str | None = None,
) -> RunResult:
    """Load the job, run Agent 1, and persist the architecture model."""
    settings = get_settings()
    tenant_id = tenant_id or settings.local_tenant_id
    provider = provider or settings.generation_provider

    job = store.get_job(project_id, job_id)
    architecture_type = job["architectureType"]
    instruction = job.get("instruction", "")
    document_ids = list(job.get("documentIds", []))

    store.update_job_status(
        project_id, job_id, "ARCHITECTING", current_state="RUN_SOLUTION_ARCHITECT",
        progress_percent=20,
    )

    # 1. Retrieve evidence (scoped to project + selected documents).
    query = instruction or f"{architecture_type} architecture requirements"
    evidence = retrieve(tenant_id, project_id, document_ids, query)

    # 2. Generate the architecture model.
    model = generate(
        provider, architecture_type, instruction, project_id, job_id, evidence
    )

    # 3. Validate against the contract + Agent 1 gates.
    try:
        validate(model)
    except ValidationFailure as exc:
        store.update_job_status(
            project_id, job_id, "FAILED",
            current_state="VALIDATE_ARCHITECTURE_MODEL",
        )
        raise

    # 4. Persist to S3.
    uri = store.put_architecture_model(tenant_id, project_id, job_id, model)

    store.update_job_status(
        project_id, job_id, "ARCHITECTING",
        current_state="VALIDATE_ARCHITECTURE_MODEL", progress_percent=35,
    )

    warnings = []
    if not evidence:
        warnings.append("No evidence retrieved; model may be ungrounded.")

    return RunResult(
        project_id=project_id,
        job_id=job_id,
        architecture_type=architecture_type,
        provider=provider,
        evidence_count=len(evidence),
        model_s3_uri=uri,
        node_count=len(model.get("nodes", [])),
        edge_count=len(model.get("edges", [])),
        warnings=warnings,
    )
