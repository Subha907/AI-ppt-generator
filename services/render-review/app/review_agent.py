"""Agent 4 core: read preview + layout -> VLM review -> validate -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store
from .config import get_settings
from .review import review
from .review_validation import ReviewValidationFailure, validate_report


@dataclass
class ReviewResult:
    project_id: str
    job_id: str
    attempt: int
    provider: str
    decision: str
    score: int
    issue_count: int
    report_s3_uri: str
    issues: list[dict] = field(default_factory=list)


def review_from_job(
    project_id: str,
    job_id: str,
    tenant_id: str | None = None,
    attempt: int = 1,
    provider: str | None = None,
) -> ReviewResult:
    settings = get_settings()
    tenant_id = tenant_id or settings.local_tenant_id
    provider = provider or settings.review_provider

    store.get_job(project_id, job_id)
    store.update_job_status(
        project_id, job_id, "VISUAL_REVIEW",
        current_state="RUN_VISUAL_REVIEWER", progress_percent=90,
    )

    # 1. Load Agent 3's preview + Agent 2's layout.
    preview_png = store.get_preview(tenant_id, project_id, job_id, attempt)
    layout = store.get_layout_model(tenant_id, project_id, job_id)

    # 2. Run the visual review (VLM or stub).
    report = review(provider, preview_png, layout, project_id, job_id, attempt)

    # 3. Validate the report against the contract + gates.
    validate_report(report)

    # 4. Persist.
    uri = store.put_review_report(tenant_id, project_id, job_id, attempt, report)

    # 5. Reflect the decision on the job (PASS -> ready to publish).
    if report["decision"] == "PASS":
        store.update_job_status(
            project_id, job_id, "VISUAL_REVIEW",
            current_state="PUBLISH_ARTIFACT", progress_percent=95,
        )

    return ReviewResult(
        project_id=project_id,
        job_id=job_id,
        attempt=attempt,
        provider=provider,
        decision=report["decision"],
        score=int(report["score"]),
        issue_count=len(report.get("issues", [])),
        report_s3_uri=uri,
        issues=report.get("issues", []),
    )
