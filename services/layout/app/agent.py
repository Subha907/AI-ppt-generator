"""Agent 2 core: read architecture model -> layout -> validate -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store
from .config import get_settings
from .engine import build_layout
from .graphviz_engine import GraphvizUnavailable, build_layout_graphviz
from .stack_engine import build_layout_stack
from .validation import ValidationFailure, validate


@dataclass
class RunResult:
    project_id: str
    job_id: str
    layout_s3_uri: str
    engine: str
    object_count: int
    connector_count: int
    overlap_count: int
    out_of_bounds_count: int
    warnings: list[str] = field(default_factory=list)


def _slide_profile() -> dict:
    s = get_settings()
    return {
        "widthIn": s.slide_width_in,
        "heightIn": s.slide_height_in,
        "safeMarginIn": s.safe_margin_in,
    }


def _compute_layout(arch: dict, slide: dict, engine: str) -> tuple[dict, str, list[str]]:
    """Compute the layout with the chosen engine.

    engine="stack" (default): the generic recursive stack engine (AWS-style
      HorizontalStack/VerticalStack) with deterministic orthogonal routing.
    engine="stack-abs": stack engine + Rule 2.2/2.3 absolute swimlane post-pass
      (subnet tiers locked to absolute Y lanes aligned across AZs, dynamic icon
      spacing).
    engine="graphviz": dot for placement + routing.
    engine="grid": the original hand-rolled shelf engine.
    Any engine falls back to the next if it errors.
    """
    warnings: list[str] = []
    if engine in ("stack", "stack-abs"):
        absolute = engine == "stack-abs"
        try:
            return (
                build_layout_stack(arch, slide, absolute_swimlanes=absolute),
                engine,
                warnings,
            )
        except Exception as exc:  # noqa: BLE001 - fall back gracefully
            warnings.append(f"Stack engine failed ({exc}); trying graphviz.")
            engine = "graphviz"
    if engine == "graphviz":
        try:
            return build_layout_graphviz(arch, slide), "graphviz", warnings
        except GraphvizUnavailable as exc:
            warnings.append(f"Graphviz unavailable ({exc}); used grid engine.")
    layout = build_layout(arch, slide)
    warnings.extend(layout.pop("_warnings", []))
    return layout, "grid", warnings


def run_from_job(
    project_id: str,
    job_id: str,
    tenant_id: str | None = None,
    engine: str = "stack",
) -> RunResult:
    settings = get_settings()
    tenant_id = tenant_id or settings.local_tenant_id

    # Ensure the job exists (and mark progress).
    store.get_job(project_id, job_id)
    store.update_job_status(
        project_id, job_id, "LAYOUT",
        current_state="RUN_LAYOUT_AGENT", progress_percent=50,
    )

    # 1. Load Agent 1's architecture model.
    arch = store.get_architecture_model(tenant_id, project_id, job_id)

    # 2. Compute the layout with the chosen engine.
    layout, used_engine, engine_warnings = _compute_layout(
        arch, _slide_profile(), engine
    )

    # 3. Validate (schema + geometry gates); this also fills validation{}.
    try:
        layout = validate(layout)
    except ValidationFailure:
        store.update_job_status(
            project_id, job_id, "FAILED", current_state="VALIDATE_LAYOUT_MODEL",
        )
        raise

    # 4. Persist.
    uri = store.put_layout_model(tenant_id, project_id, job_id, layout)
    store.update_job_status(
        project_id, job_id, "LAYOUT",
        current_state="VALIDATE_LAYOUT_MODEL", progress_percent=60,
    )

    v = layout.get("validation", {})
    return RunResult(
        project_id=project_id,
        job_id=job_id,
        layout_s3_uri=uri,
        engine=used_engine,
        object_count=len(layout.get("objects", [])),
        connector_count=len(layout.get("connectors", [])),
        overlap_count=int(v.get("overlapCount", 0)),
        out_of_bounds_count=int(v.get("outOfBoundsCount", 0)),
        warnings=list(engine_warnings),
    )
