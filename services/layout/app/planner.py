"""Deterministic layout planner for Agent 2.

Turns (architecture model + resolved LayoutConfig) into a STRUCTURAL layout
plan: ordered bands + a node->band assignment, with NO coordinates. The plan is
the single shared abstraction that the geometry patterns (Phase C+) and an
optional LLM planner (Phase E) both target.

Design rules:
  * NON-GEOMETRIC: the plan never contains x/y/w/h/routes. Geometry stays in the
    engines; the plan only says "which band, in what order".
  * DETERMINISTIC: same model + config always yields the same plan.
  * NEVER SILENT: unknown hint tokens, unassigned nodes, and ordering fallbacks
    are recorded in plan['diagnostics'].
  * SINGLE SOURCE OF TRUTH: band ORDER comes from the policy hints the model
    already carries (laneOrder/layerOrder/columns) or from the architecture
    topology (containment); it is not re-derived from YAML here.

Strategy -> bandKind mapping (reuses a SMALL set of organizing primitives, not a
per-policy algorithm):

    lane        : left_to_right_flow, swimlane
    layer       : layered_architecture, layered_controls
    column      : current_to_target
    group       : capability_map, domain_grouped_layers
    hub         : central_platform, hub_and_spoke
    containment : multi_az_columns, multi_region_mirror
    none        : generic_stack / anything unmapped (advisory bands only)
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from jsonschema import Draft202012Validator

from .policy import LayoutConfig, RendererHints

_PLAN_CONTRACT_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "layout_plan.contract.json"
)


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_plan_contract() -> dict:
    return json.loads(_PLAN_CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_plan(plan: dict, model: dict | None = None) -> dict:
    """Validate a layout plan against the contract + structural gates.

    Schema gate: the plan matches layout_plan.contract.json.
    Structural gates:
      * every assignment targets an existing band,
      * no node is assigned to more than one band,
      * when `model` is given, every assigned nodeId exists in the model and
        every non-container model node is assigned (unassigned -> error),
      * band orders are unique and contiguous from 0.
    Raises PlanValidationError on failure; returns the plan on success.
    """
    errors: list[str] = []
    validator = Draft202012Validator(_load_plan_contract())
    for e in sorted(validator.iter_errors(plan), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"schema: {loc}: {e.message}")
    if errors:
        raise PlanValidationError(errors)

    band_ids = {b["bandId"] for b in plan.get("bands", [])}

    # Every assignment targets a real band; no double assignment.
    seen_nodes: set[str] = set()
    for a in plan.get("assignments", []):
        if a["bandId"] not in band_ids:
            errors.append(
                f"assignment for node '{a['nodeId']}' targets unknown band "
                f"'{a['bandId']}'"
            )
        if a["nodeId"] in seen_nodes:
            errors.append(f"node '{a['nodeId']}' assigned more than once")
        seen_nodes.add(a["nodeId"])

    # Band order contiguity.
    orders = sorted(b["order"] for b in plan.get("bands", []))
    if orders and orders != list(range(len(orders))):
        errors.append(f"band orders are not contiguous from 0: {orders}")

    # Coverage against the model (if provided): non-container nodes assigned,
    # assigned nodes exist.
    if model is not None:
        model_ids = {n["id"] for n in model.get("nodes", [])}
        for nid in seen_nodes:
            if nid not in model_ids:
                errors.append(f"assignment references unknown node '{nid}'")
        container_types = _CONTAINER_TYPES
        missing = [
            n["id"]
            for n in model.get("nodes", [])
            if (n.get("type") or "") not in container_types
            and n["id"] not in seen_nodes
        ]
        if missing:
            errors.append(
                f"{len(missing)} non-container node(s) unassigned: "
                f"{missing[:8]}" + (" ..." if len(missing) > 8 else "")
            )

    if errors:
        raise PlanValidationError(errors)
    return plan

# ---------------------------------------------------------------------------
# Strategy -> organizing primitive (bandKind).
# ---------------------------------------------------------------------------
_STRATEGY_BAND_KIND: dict[str, str] = {
    "left_to_right_flow": "lane",
    "swimlane": "lane",
    "layered_architecture": "layer",
    "layered_controls": "layer",
    "current_to_target": "column",
    "capability_map": "group",
    "domain_grouped_layers": "group",
    "central_platform": "hub",
    "hub_and_spoke": "hub",
    "multi_az_columns": "containment",
    "multi_region_mirror": "containment",
    # generic / default
    "generic_stack": "none",
}

# Structural container types that define the containment hierarchy.
_CONTAINER_TYPES = {
    "CONTAINER", "ACCOUNT", "REGION", "VPC", "AVAILABILITYZONE", "SUBNET",
}

# Node-type -> coarse band token, used when a node carries no explicit
# concern/lane/layer/group property. Deliberately generic; the geometry pattern
# refines placement. Keys are uppercase node types.
_TYPE_BAND_TOKEN: dict[str, str] = {
    "ACTOR": "actors",
    "EXTERNAL": "external",
    "EDGE": "edge",
    "NETWORK": "edge",
    "INTEGRATION": "integration",
    "COMPUTE": "application_services",
    "SERVICE": "application_services",
    "COMPONENT": "application_services",
    "CAPABILITY": "capabilities",
    "PROCESS": "process",
    "DATABASE": "data",
    "DATASTORE": "data",
    "STORAGE": "data",
    "SECURITY": "security",
    "OBSERVABILITY": "observability",
}

_UNASSIGNED_BAND = "_unassigned"


def band_kind_for(strategy: str) -> str:
    """Return the organizing primitive for a strategy id (default 'none')."""
    return _STRATEGY_BAND_KIND.get(strategy, "none")


def _dedupe(seq: list[str]) -> list[str]:
    """Order-preserving de-duplication (first occurrence wins)."""
    out: list[str] = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


def _hint_order(kind: str, hints: RendererHints) -> list[str]:
    """The explicit ordered band tokens for this band kind, if the policy
    declared them (laneOrder / layerOrder / columns). De-duplicated so a
    malformed hint with repeats cannot create duplicate bands."""
    if kind == "lane":
        return _dedupe(hints.lane_order)
    if kind == "layer":
        return _dedupe(hints.layer_order)
    if kind == "column":
        return _dedupe(hints.columns)
    return []


def _node_band_token(node: dict) -> str | None:
    """Explicit band token carried on the node, if any.

    Honors (in priority order) properties.lane, .layer, .column, .group,
    .concern. These are optional; most models won't set them, in which case the
    planner falls back to the node-type heuristic.
    """
    props = node.get("properties") or {}
    for key in ("lane", "layer", "column", "group", "concern"):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _topology_bands(nodes: list[dict]) -> tuple[list[dict], list[dict], str]:
    """Containment-based bands: top-level structural containers become bands;
    every node is assigned to the top-level container it descends from.

    Used for 'containment' strategies (multi-AZ / multi-region) where the
    architecture topology itself defines the structure. Returns
    (bands, assignments, ordering_source).
    """
    by_id = {n["id"]: n for n in nodes}

    def root_container(nid: str) -> str | None:
        """Walk up parentId to the highest ancestor; that's the band."""
        cur = by_id.get(nid)
        if cur is None:
            return None
        seen = set()
        while True:
            pid = cur.get("parentId")
            if not pid or pid not in by_id or pid in seen:
                return cur["id"]
            seen.add(pid)
            cur = by_id[pid]

    # Top-level nodes (no parent) are the band roots.
    roots = [n for n in nodes if not n.get("parentId")]
    bands = [
        {"bandId": r["id"], "label": r.get("label"), "order": i, "role": None}
        for i, r in enumerate(roots)
    ]
    band_ids = {b["bandId"] for b in bands}
    assignments = []
    for n in nodes:
        root = root_container(n["id"])
        if root in band_ids:
            assignments.append({"nodeId": n["id"], "bandId": root, "rank": None})
    return bands, assignments, "topology"


def _ordered_bands(
    kind: str, nodes: list[dict], hints: RendererHints
) -> tuple[list[dict], list[dict], str, list[str]]:
    """Build ordered bands for lane/layer/column kinds + assign nodes.

    Band order: the policy hint ordering if present, otherwise the order in
    which band tokens are first encountered among the (non-container) nodes.
    Returns (bands, assignments, ordering_source, diagnostics).
    """
    diagnostics: list[str] = []
    leaves = [n for n in nodes if (n.get("type") or "") not in _CONTAINER_TYPES]

    # Resolve each node's band token (explicit property, else type heuristic).
    token_of: dict[str, str] = {}
    for n in leaves:
        token = _node_band_token(n) or _TYPE_BAND_TOKEN.get(
            (n.get("type") or "").upper()
        )
        if token:
            token_of[n["id"]] = token

    hint_order = _hint_order(kind, hints)
    if hint_order:
        ordering_source = "hint"
        ordered_tokens = list(hint_order)
        # Append any encountered tokens the hint omitted (kept after hinted).
        for n in leaves:
            t = token_of.get(n["id"])
            if t and t not in ordered_tokens:
                ordered_tokens.append(t)
                diagnostics.append(
                    f"band '{t}' not in policy ordering; appended after hinted bands"
                )
    else:
        ordering_source = "default"
        ordered_tokens = []
        for n in leaves:  # first-seen order (deterministic for a given model)
            t = token_of.get(n["id"])
            if t and t not in ordered_tokens:
                ordered_tokens.append(t)
        diagnostics.append(
            "no explicit band ordering hint; using first-seen token order"
        )

    bands = [
        {"bandId": tok, "label": tok.replace("_", " ").title(), "order": i, "role": None}
        for i, tok in enumerate(ordered_tokens)
    ]
    band_ids = {b["bandId"] for b in bands}

    assignments = []
    unassigned = []
    for n in leaves:
        t = token_of.get(n["id"])
        if t and t in band_ids:
            assignments.append({"nodeId": n["id"], "bandId": t, "rank": None})
        else:
            unassigned.append(n["id"])
    if unassigned:
        diagnostics.append(
            f"{len(unassigned)} node(s) could not be assigned to an ordered "
            f"band: {unassigned[:8]}" + (" ..." if len(unassigned) > 8 else "")
        )
    return bands, assignments, ordering_source, diagnostics


def _group_bands(
    nodes: list[dict], hints: RendererHints
) -> tuple[list[dict], list[dict], str, list[str]]:
    """Group kind: labeled, unordered grouping boxes.

    Groups come from the existing top-level CONTAINER nodes when present
    (capability map / domain groups are authored as containers); otherwise from
    the node-type token. Order is non-binding.
    """
    diagnostics: list[str] = []
    containers = [
        n for n in nodes
        if (n.get("type") or "") in _CONTAINER_TYPES and n.get("parentId")
    ]
    if containers:
        bands = [
            {"bandId": c["id"], "label": c.get("label"), "order": i, "role": None}
            for i, c in enumerate(containers)
        ]
        band_ids = {b["bandId"] for b in bands}
        by_id = {n["id"]: n for n in nodes}
        assignments = []
        unassigned = []
        for n in nodes:
            if n["id"] in band_ids:
                continue  # the group box itself
            pid = n.get("parentId")
            # Assign to nearest ancestor that is a group band.
            cur = by_id.get(pid) if pid else None
            hop = 0
            while cur and hop < 20:
                if cur["id"] in band_ids:
                    assignments.append(
                        {"nodeId": n["id"], "bandId": cur["id"], "rank": None}
                    )
                    break
                cur = by_id.get(cur.get("parentId"))
                hop += 1
            else:
                if (n.get("type") or "") not in _CONTAINER_TYPES:
                    unassigned.append(n["id"])
        if unassigned:
            diagnostics.append(
                f"{len(unassigned)} node(s) outside any group container: "
                f"{unassigned[:8]}" + (" ..." if len(unassigned) > 8 else "")
            )
        return bands, assignments, "topology", diagnostics

    # Fallback: group by node-type token.
    diagnostics.append("no group containers in topology; grouping by node type")
    bands_out, assigns, _, diags = _ordered_bands("lane", nodes, hints)
    diagnostics.extend(diags)
    return bands_out, assigns, "default", diagnostics


def _hub_bands(
    nodes: list[dict]
) -> tuple[list[dict], list[dict], str, list[str]]:
    """Hub kind: one central band + one peripheral band. The hub is the node
    with the highest degree (most connected); everything else is a spoke."""
    diagnostics: list[str] = []
    leaves = [n for n in nodes if (n.get("type") or "") not in _CONTAINER_TYPES]
    bands = [
        {"bandId": "hub", "label": "Core", "order": 0, "role": "hub"},
        {"bandId": "spoke", "label": "Periphery", "order": 1, "role": "spoke"},
    ]
    if not leaves:
        return bands, [], "default", ["no leaf nodes to assign for hub layout"]
    # degree is computed by the caller normally; here fall back to: the node
    # with the most children/least external-ness. Simplest deterministic rule:
    # the first non-ACTOR/EXTERNAL leaf is the hub.
    hub_id = None
    for n in leaves:
        if (n.get("type") or "").upper() not in ("ACTOR", "EXTERNAL"):
            hub_id = n["id"]
            break
    if hub_id is None:
        hub_id = leaves[0]["id"]
    assignments = [
        {"nodeId": n["id"], "bandId": "hub" if n["id"] == hub_id else "spoke",
         "rank": None}
        for n in leaves
    ]
    diagnostics.append(f"hub selected deterministically: '{hub_id}'")
    return bands, assignments, "default", diagnostics


def build_layout_plan(model: dict, config: LayoutConfig) -> dict:
    """Produce a validated-shape layout plan dict (no coordinates).

    The plan's strategy is the resolved config.strategy; bandKind + bands +
    assignments follow the organizing primitive for that strategy.
    """
    nodes: list[dict[str, Any]] = model.get("nodes", [])
    hints = config.hints
    strategy = config.strategy or "generic_stack"
    kind = band_kind_for(strategy)
    diagnostics: list[str] = []

    if kind in ("lane", "layer", "column"):
        bands, assignments, ordering_source, diags = _ordered_bands(
            kind, nodes, hints
        )
        diagnostics.extend(diags)
    elif kind == "group":
        bands, assignments, ordering_source, diags = _group_bands(nodes, hints)
        diagnostics.extend(diags)
    elif kind == "hub":
        bands, assignments, ordering_source, diags = _hub_bands(nodes)
        diagnostics.extend(diags)
    elif kind == "containment":
        bands, assignments, ordering_source = _topology_bands(nodes)
    else:  # 'none' generic fall-through: single advisory band holding all nodes.
        bands = [{"bandId": "all", "label": "All", "order": 0, "role": None}]
        assignments = [
            {"nodeId": n["id"], "bandId": "all", "rank": None} for n in nodes
        ]
        ordering_source = "default"
        diagnostics.append(
            f"strategy '{strategy}' has no band structure; "
            "plan is advisory (single band)"
        )

    diagnostics.append(f"strategy '{strategy}' -> bandKind '{kind}'")

    return {
        "schemaVersion": "1.0",
        "projectId": model.get("projectId", ""),
        "jobId": model.get("jobId", ""),
        "strategy": strategy,
        "bandKind": kind,
        "bands": bands,
        "assignments": assignments,
        "emphasis": list(hints.emphasis),
        "orderingSource": ordering_source,
        "diagnostics": diagnostics,
    }


# ===========================================================================
# Phase E: optional LLM planner (band assignment + ordering ONLY).
#
# The LLM NEVER produces geometry. It may only (a) reorder the deterministic
# bands and (b) reassign nodes among those existing bands. We rebuild the plan
# from the deterministic scaffold using the LLM's choices, so coordinates stay
# fully deterministic and no new structure can be invented. Any failure (parse,
# schema, coverage, unknown band/node) falls back to the deterministic plan.
# ===========================================================================
import json as _json
import re as _re


class LLMPlannerError(RuntimeError):
    pass


_PLANNER_SYSTEM_PROMPT = """You assign architecture nodes to layout bands for a
slide. You are given the band kind, the available bands (ids + labels), and the
node list (id, type, label). Decide which band each node belongs to and the
left-to-right / top-to-bottom order of the bands.

Hard rules:
- Output ONLY a single JSON object, no prose, no code fences.
- Use ONLY the given bandIds. Do NOT invent bands or nodes.
- Do NOT output any coordinates, sizes, x/y/w/h, or routes - placement is done
  by a separate engine. You decide grouping and order ONLY.
- Assign EVERY node to exactly one band.

Required JSON shape:
{
  "bandOrder": ["bandId1", "bandId2", ...],
  "assignments": [{"nodeId": "...", "bandId": "..."}, ...]
}
"""

# Keys that must NEVER appear in LLM output (geometry leakage guard).
_FORBIDDEN_PLAN_KEYS = {
    "x", "y", "w", "h", "z", "width", "height", "points", "route", "pos",
    "coordinates", "position",
}


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return _json.loads(text[start : end + 1])
        raise LLMPlannerError("planner model did not return valid JSON")


def _has_forbidden_keys(obj) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _FORBIDDEN_PLAN_KEYS:
                return True
            if _has_forbidden_keys(v):
                return True
    elif isinstance(obj, list):
        return any(_has_forbidden_keys(v) for v in obj)
    return False


def _planner_user_message(model: dict, base_plan: dict) -> str:
    bands = [
        {"bandId": b["bandId"], "label": b.get("label")}
        for b in base_plan.get("bands", [])
    ]
    nodes = [
        {"id": n["id"], "type": n.get("type"), "label": n.get("label")}
        for n in model.get("nodes", [])
        if (n.get("type") or "") not in _CONTAINER_TYPES
    ]
    return (
        f"bandKind: {base_plan.get('bandKind')}\n"
        f"bands: {_json.dumps(bands)}\n"
        f"nodes: {_json.dumps(nodes)}\n"
        "Return ONLY the JSON object."
    )


def _apply_llm_choices(base_plan: dict, model: dict, llm: dict) -> dict:
    """Rebuild a plan from the deterministic scaffold using the LLM's band order
    and assignments. Validates against the base bands/nodes; raises on any
    inconsistency so the caller can fall back."""
    if _has_forbidden_keys(llm):
        raise LLMPlannerError("planner output contained geometry keys")

    base_bands = {b["bandId"]: b for b in base_plan.get("bands", [])}
    model_leaf_ids = {
        n["id"] for n in model.get("nodes", [])
        if (n.get("type") or "") not in _CONTAINER_TYPES
    }

    # 1. Band order: a permutation of the existing bandIds (unknowns rejected;
    #    missing ones appended in their original order).
    order = [b for b in (llm.get("bandOrder") or []) if b in base_bands]
    for bid in base_bands:
        if bid not in order:
            order.append(bid)
    new_bands = []
    for i, bid in enumerate(order):
        b = dict(base_bands[bid])
        b["order"] = i
        new_bands.append(b)

    # 2. Assignments: every leaf assigned exactly once to a known band.
    assigns: list[dict] = []
    seen: set[str] = set()
    for a in llm.get("assignments") or []:
        nid, bid = a.get("nodeId"), a.get("bandId")
        if nid in model_leaf_ids and bid in base_bands and nid not in seen:
            assigns.append({"nodeId": nid, "bandId": bid, "rank": None})
            seen.add(nid)
    missing = model_leaf_ids - seen
    if missing:
        raise LLMPlannerError(f"LLM plan left {len(missing)} node(s) unassigned")

    plan = dict(base_plan)
    plan["bands"] = new_bands
    plan["assignments"] = assigns
    plan["orderingSource"] = "hint"  # LLM-chosen order
    plan["diagnostics"] = list(base_plan.get("diagnostics", [])) + [
        "band order + assignments chosen by the LLM planner"
    ]
    return plan


def llm_build_layout_plan(model: dict, config: LayoutConfig, client) -> dict:
    """Produce a layout plan whose band order + assignments come from the LLM.

    `client` is a Bedrock runtime client (or any object exposing .converse()).
    Starts from the deterministic scaffold (bands/bandKind), then applies the
    LLM's choices. Raises LLMPlannerError on any failure.
    """
    from .config import get_settings

    base_plan = build_layout_plan(model, config)
    # Only ordered/grouped kinds benefit from LLM assignment; for containment /
    # none the deterministic plan is authoritative.
    if base_plan.get("bandKind") not in ("lane", "column", "layer", "group"):
        return base_plan

    settings = get_settings()
    resp = client.converse(
        modelId=settings.plan_model_id,
        system=[{"text": _PLANNER_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [
            {"text": _planner_user_message(model, base_plan)}
        ]}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.0, "topP": 0.9},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    llm = _extract_json(text)
    return _apply_llm_choices(base_plan, model, llm)


def build_plan(
    model: dict,
    config: LayoutConfig,
    provider: str = "deterministic",
    client=None,
) -> tuple[dict, str, list[str]]:
    """Produce a layout plan via the chosen provider, with deterministic fallback.

    Returns (plan, provider_used, diagnostics). 'nova' uses the LLM planner; any
    failure falls back to the deterministic planner and records WHY (never
    silent). 'deterministic' (default) uses the rule-based planner directly.
    """
    diagnostics: list[str] = []
    if provider == "nova":
        try:
            if client is None:
                from .aws import bedrock_runtime_client

                client = bedrock_runtime_client()
            plan = llm_build_layout_plan(model, config, client)
            # Validate the LLM plan; fall back if it fails the gates.
            validate_plan(plan, model)
            diagnostics.append("layout plan produced by the LLM planner ('nova')")
            return plan, "nova", diagnostics
        except Exception as exc:  # noqa: BLE001 - deterministic fallback
            diagnostics.append(
                f"LLM planner failed ({exc}); fell back to deterministic planner"
            )
    plan = build_layout_plan(model, config)
    return plan, "deterministic", diagnostics


# ===========================================================================
# Phase F: policy-specific geometry verification.
#
# The generic validator (validation.validate) already enforces bounds, sibling
# overlap, containment, and connector anchors. These checks additionally verify
# that the PLAN's intent was honored by the geometry: band ORDER on the correct
# axis (lane/column = x, layer = y), and that each band's members sit inside
# their band container. Returned as diagnostics/warnings - they do NOT mutate
# geometry and are advisory (the hard gates remain in validation.validate).
# ===========================================================================
def check_plan_geometry(plan: dict, layout: dict) -> list[str]:
    """Return human-readable warnings where the geometry diverges from the plan.

    Empty list == geometry honors the plan. Never raises; never mutates.
    """
    warnings: list[str] = []
    kind = plan.get("bandKind")
    if kind not in ("lane", "column", "layer", "group", "hub"):
        return warnings  # non-band strategies: nothing plan-specific to check

    by_id = {o["objectId"]: o for o in layout.get("objects", [])}
    bands = sorted(plan.get("bands", []), key=lambda b: b["order"])

    # 1. Band ordering honored on the primary axis.
    placed = [(b, by_id.get(b["bandId"])) for b in bands]
    placed = [(b, o) for b, o in placed if o is not None]
    if kind in ("lane", "column"):
        xs = [o["x"] for _, o in placed]
        if xs != sorted(xs):
            warnings.append(
                f"band x-order does not match plan order for bandKind '{kind}'"
            )
    elif kind == "layer":
        ys = [o["y"] for _, o in placed]
        if ys != sorted(ys):
            warnings.append("band y-order does not match plan order for 'layer'")

    # 2. Each assigned leaf sits inside its band container (when both placed).
    eps = 0.05
    for a in plan.get("assignments", []):
        leaf = by_id.get(a["nodeId"])
        band = by_id.get(a["bandId"])
        if not leaf or not band:
            continue
        inside = (
            leaf["x"] >= band["x"] - eps
            and leaf["y"] >= band["y"] - eps
            and leaf["x"] + leaf["w"] <= band["x"] + band["w"] + eps
            and leaf["y"] + leaf["h"] <= band["y"] + band["h"] + eps
        )
        if not inside:
            warnings.append(
                f"node '{a['nodeId']}' is outside its planned band '{a['bandId']}'"
            )
    return warnings
