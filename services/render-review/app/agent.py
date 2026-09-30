"""Agent 3 core: read layout -> compile PPTX -> validate -> render preview -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store
from .compiler import compile_pptx
from .config import get_settings
from .preview import render_preview
from .validation import ValidationFailure, validate_pptx


@dataclass
class CompileResult:
    project_id: str
    job_id: str
    version: int
    pptx_s3_uri: str
    preview_s3_uri: str
    stats: dict
    warnings: list[str] = field(default_factory=list)


def compile_from_job(
    project_id: str,
    job_id: str,
    tenant_id: str | None = None,
    version: int = 1,
    attempt: int = 1,
) -> CompileResult:
    settings = get_settings()
    tenant_id = tenant_id or settings.local_tenant_id

    store.get_job(project_id, job_id)
    store.update_job_status(
        project_id, job_id, "COMPILING", current_state="RUN_COMPILER",
        progress_percent=70,
    )

    # 1. Load Agent 2's layout model.
    layout = store.get_layout_model(tenant_id, project_id, job_id)

    # 2. Compile the editable PPTX (native shapes).
    pptx_bytes = compile_pptx(layout)

    # 3. Validate the compiled package (PDF 5.3 gates).
    try:
        stats = validate_pptx(pptx_bytes, layout)
    except ValidationFailure:
        store.update_job_status(project_id, job_id, "FAILED", current_state="RUN_COMPILER")
        raise

    # 4. Render a preview image (for Agent 4).
    preview_bytes = render_preview(layout)

    # 5. Persist artifacts.
    pptx_uri = store.put_pptx(tenant_id, project_id, job_id, version, pptx_bytes)
    preview_uri = store.put_preview(tenant_id, project_id, job_id, attempt, preview_bytes)

    store.update_job_status(
        project_id, job_id, "COMPILING", current_state="RENDER_PREVIEW",
        progress_percent=80,
    )

    return CompileResult(
        project_id=project_id,
        job_id=job_id,
        version=version,
        pptx_s3_uri=pptx_uri,
        preview_s3_uri=preview_uri,
        stats=stats,
    )
