"""Agent 2 core: read architecture model -> layout -> validate -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store
from .config import get_settings
from .engine import build_layout
from .graphviz_engine import GraphvizUnavailable, build_layout_graphviz
from .band_engine import build_layout_from_plan
from .planner import (
    PlanValidationError,
    build_plan,
    check_plan_geometry,
    validate_plan,
)
from .policy import LayoutConfig, resolve_layout_config
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
    # Phase 2 diagnostics: how the engine was chosen (policy resolver output).
    strategy: str = ""
    engine_source: str = ""  # 'override' | 'hint' | 'alternate' | 'default'
    preferred_layout: str | None = None
    selection_diagnostics: list[str] = field(default_factory=list)
    # Phase B: structural layout plan (advisory this phase) + its band summary.
    band_kind: str = ""
    band_count: int = 0
    plan_valid: bool = False
    plan_provider: str = "deterministic"  # 'deterministic' | 'nova'
    plan_diagnostics: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def diagnostics_summary(self) -> dict:
        """A single structured, non-secret diagnostics record for this run
        (improvement-plan section 12): selected strategy, engine + why, plan
        provider, band structure, geometry validation, warnings. Safe to log."""
        return {
            "projectId": self.project_id,
            "jobId": self.job_id,
            "strategy": self.strategy,
            "engine": self.engine,
            "engineSource": self.engine_source,
            "preferredLayout": self.preferred_layout,
            "planProvider": self.plan_provider,
            "planValid": self.plan_valid,
            "bandKind": self.band_kind,
            "bandCount": self.band_count,
            "objectCount": self.object_count,
            "connectorCount": self.connector_count,
            "overlapCount": self.overlap_count,
            "outOfBoundsCount": self.out_of_bounds_count,
            "selectionDiagnostics": list(self.selection_diagnostics),
            "planDiagnostics": list(self.plan_diagnostics),
            "warnings": list(self.warnings),
        }


def _slide_profile() -> dict:
    s = get_settings()
    return {
        "widthIn": s.slide_width_in,
        "heightIn": s.slide_height_in,
        "safeMarginIn": s.safe_margin_in,
    }


def _compute_layout(
    arch: dict, slide: dict, engine: str, plan: dict | None = None
) -> tuple[dict, str, list[str]]:
    """Compute the layout with the chosen engine.

    engine="band": plan-driven reusable band engine for lane/layer strategies
      (Phase C). Requires `plan`; falls back to "stack" if it errors.
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
    if engine == "band":
        try:
            layout = build_layout_from_plan(arch, plan or {}, slide)
            warnings.extend(layout.pop("_warnings", []))
            return layout, "band", warnings
        except Exception as exc:  # noqa: BLE001 - fall back to the generic stacker
            warnings.append(f"Band engine failed ({exc}); trying stack.")
            engine = "stack"
    if engine == "stack-region":
        try:
            return (
                build_layout_stack(arch, slide, region_mirror=True),
                "stack-region",
                warnings,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the generic stacker
            warnings.append(f"Region-mirror engine failed ({exc}); trying stack.")
            engine = "stack"
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
    engine: str | None = None,
    plan_provider: str | None = None,
) -> RunResult:
    """Run Agent 2 for a job.

    ``plan_provider`` selects the layout planner: 'deterministic' (default) or
    'nova' (LLM band assignment/ordering with deterministic fallback). When
    None, falls back to the configured default.

    ``engine`` is an OPTIONAL explicit override. When None (the default), the
    engine is chosen by the policy resolver from the architecture model's
    ``rendererHints.preferredLayout`` (falling back to a documented default).
    Passing an engine string forces it, preserving the previous behavior.
    """
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

    # 2. Resolve the layout strategy -> engine from the model's hint (or the
    #    caller's explicit override). Deterministic and fully diagnosed.
    cfg: LayoutConfig = resolve_layout_config(arch, requested_engine=engine)

    # 2b. Build the structural layout plan (Phase B) and validate it.
    plan: dict | None = None
    plan_valid = False
    plan_diags: list[str] = []
    band_kind = ""
    band_count = 0
    plan_provider_used = "deterministic"
    provider = plan_provider or settings.plan_provider
    try:
        plan, plan_provider_used, prov_diags = build_plan(
            arch, cfg, provider=provider
        )
        plan_diags.extend(prov_diags)
        band_kind = plan.get("bandKind", "")
        band_count = len(plan.get("bands", []))
        plan_diags.extend(plan.get("diagnostics", []))
        try:
            validate_plan(plan, arch)
            plan_valid = True
        except PlanValidationError as exc:
            plan_diags.append(f"plan invalid: {'; '.join(exc.errors)}")
    except Exception as exc:  # noqa: BLE001 - planner must never break layout
        plan_diags.append(f"planner error: {exc}")

    # 2c. Phase C: the plan DRIVES geometry for ordered-band strategies
    #     (lane/layer) when the caller did not force an engine and the plan is
    #     valid. Containment (multi-AZ/region), group, hub, and generic plans
    #     continue to use the resolver-selected engine unchanged.
    effective_engine = cfg.engine
    if engine is None and plan_valid and band_kind in (
        "lane", "column", "layer", "group", "hub"
    ):
        effective_engine = "band"
        plan_diags.append(
            f"plan-driven geometry: bandKind '{band_kind}' -> band engine"
        )

    # 3. Compute the layout with the chosen engine.
    layout, used_engine, engine_warnings = _compute_layout(
        arch, _slide_profile(), effective_engine, plan=plan
    )

    # 4. Validate (schema + geometry gates); this also fills validation{}.
    try:
        layout = validate(layout)
    except ValidationFailure:
        store.update_job_status(
            project_id, job_id, "FAILED", current_state="VALIDATE_LAYOUT_MODEL",
        )
        raise

    # 4b. Phase F: policy-specific geometry verification (advisory). Confirms the
    #     geometry honored the plan's band order + membership. Warnings only -
    #     the hard gates already passed above.
    if plan is not None and used_engine == "band":
        try:
            for w in check_plan_geometry(plan, layout):
                plan_diags.append(f"plan-geometry: {w}")
        except Exception as exc:  # noqa: BLE001 - advisory check never breaks
            plan_diags.append(f"plan-geometry check error: {exc}")

    # 5. Persist.
    uri = store.put_layout_model(tenant_id, project_id, job_id, layout)
    store.update_job_status(
        project_id, job_id, "LAYOUT",
        current_state="VALIDATE_LAYOUT_MODEL", progress_percent=60,
    )

    # Diagnostics: record if the intended engine differed from what actually ran
    # (i.e. an engine-level fallback happened inside _compute_layout).
    selection_diags = list(cfg.diagnostics)
    if used_engine != effective_engine:
        selection_diags.append(
            f"engine fallback: intended '{effective_engine}' but ran '{used_engine}'"
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
        strategy=cfg.strategy,
        engine_source=cfg.source,
        preferred_layout=cfg.preferred_layout,
        selection_diagnostics=selection_diags,
        band_kind=band_kind,
        band_count=band_count,
        plan_valid=plan_valid,
        plan_provider=plan_provider_used,
        plan_diagnostics=plan_diags,
        warnings=list(engine_warnings),
    )
