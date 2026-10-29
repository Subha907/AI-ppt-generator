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


# Whitelist of NON-GEOMETRIC renderer_hints keys carried from the rule pack to
# the architecture model, mapped from YAML snake_case -> contract camelCase.
# Only layout INTENT (strategy ids, ordering, emphasis) is carried; anything
# resembling coordinates/sizes is intentionally excluded (Agent 1 is geometry-
# free; Agent 2 owns all geometry).
_HINT_KEY_MAP: dict[str, str] = {
    "preferred_layout": "preferredLayout",
    "alternate_layout": "alternateLayout",
    "lane_order": "laneOrder",
    "layer_order": "layerOrder",
    "columns": "columns",
    "emphasize": "emphasis",
}
# Keys whose value must be a list of strings to be carried.
_HINT_LIST_KEYS = {"laneOrder", "layerOrder", "columns", "emphasis"}


def _attach_layout_hint(model: dict, architecture_type: str) -> None:
    """Record the rule pack's layout INTENT on the model (rendererHints).

    Reads the resolved rule pack's renderer_hints (rules.renderer_hints) and
    carries the whitelisted, non-geometric fields onto
    model['rendererHints'] using the contract's camelCase keys:
    preferredLayout, alternateLayout, laneOrder, layerOrder, columns, emphasis.

    Purely advisory: Agent 2's policy resolver/planner interprets these. No
    coordinates/sizes are ever carried. Defensive by design - any failure is
    swallowed so hint resolution can never fail model generation, and existing
    values on the model are never overwritten.
    """
    try:
        from . import rules

        pack_id = rules.resolve_type(architecture_type)
        raw = rules.renderer_hints(pack_id)
    except Exception:
        raw = {}
    if not isinstance(raw, dict) or not raw:
        return

    carried: dict = {}
    for yaml_key, out_key in _HINT_KEY_MAP.items():
        if yaml_key not in raw:
            continue
        value = raw[yaml_key]
        if out_key in _HINT_LIST_KEYS:
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                carried[out_key] = list(value)
        elif isinstance(value, str) and value.strip():
            carried[out_key] = value.strip()

    if not carried:
        return

    hints = model.get("rendererHints")
    if not isinstance(hints, dict):
        hints = {}
    for key, value in carried.items():
        hints.setdefault(key, value)  # never overwrite caller-set values
    model["rendererHints"] = hints


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

    # 2b. Attach the non-geometric layout INTENT from the rule pack so Agent 2
    # can resolve a layout strategy without re-parsing the semantic YAML. This
    # carries ONLY the preferred-layout id (no coordinates/sizes) and is
    # additive: if the pack declares no hint, rendererHints is omitted and the
    # model stays valid. Never let a hint lookup break generation.
    _attach_layout_hint(model, architecture_type)

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
