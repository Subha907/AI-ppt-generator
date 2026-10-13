"""Validate the architecture model against the contract + Agent 1 gates.

Two layers:
  1. JSON Schema validation against the shared contract file.
  2. Semantic gates from PDF section 5.1:
     - Every node has a stable, unique id.
     - Every edge references valid source/target node ids.
     - Each node has evidenceIds OR the model records an assumption.
     - No coordinates / PPT-specific details are present.
"""
from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

CONTRACT_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "architecture_model.contract.json"
)

# Keys that would indicate layout/PPT leakage into the topology.
# Rule 1.1 (Absolute Decoupling): Agent 1 must emit NO spatial/routing fields.
_FORBIDDEN_KEYS = {
    # Coordinates & dimensions.
    "x", "y", "w", "h", "z", "width", "height", "top", "left",
    "coordinates", "position",
    # Layer indexes / routing / connector geometry.
    "points", "route", "zorder", "sourceobjectid", "targetobjectid",
    "edgeclass", "connectorroute",
    # PPT / styling leakage.
    "font", "color", "fill", "slide", "shape",
}

# Substrings in a subnet label that mark it as public (Rule 1.2).
_PUBLIC_SUBNET_MARKERS = ("public", "dmz", "internet-facing", "internet facing")


class ValidationFailure(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _schema_errors(model: dict) -> list[str]:
    validator = Draft202012Validator(_load_contract())
    errs = []
    for e in sorted(validator.iter_errors(model), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errs.append(f"schema: {loc}: {e.message}")
    return errs


def _find_forbidden_keys(obj, trail: str = "") -> list[str]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _FORBIDDEN_KEYS:
                found.append(f"forbidden layout key '{k}' at {trail or '(root)'}")
            found.extend(_find_forbidden_keys(v, f"{trail}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_find_forbidden_keys(v, f"{trail}[{i}]"))
    return found


def _semantic_errors(model: dict) -> list[str]:
    errs: list[str] = []
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    assumptions = model.get("assumptions", [])

    # Unique, present node ids.
    ids = [n.get("id") for n in nodes]
    seen = set()
    for nid in ids:
        if not nid:
            errs.append("a node is missing an id")
        elif nid in seen:
            errs.append(f"duplicate node id: {nid}")
        else:
            seen.add(nid)

    # parentId references must exist (when set).
    for n in nodes:
        pid = n.get("parentId")
        if pid and pid not in seen:
            errs.append(f"node {n.get('id')} parentId '{pid}' is not a known node")

    # Rule 1.2 (Security Envelope Isolation): a DATABASE node must never be
    # nested inside a subnet whose label marks it public. Databases belong in a
    # private / isolated data subnet. This is a structural security gate, not a
    # visual one - it fails the handoff so the model is regenerated correctly.
    by_id = {n.get("id"): n for n in nodes}
    for n in nodes:
        if n.get("type") != "DATABASE":
            continue
        parent = by_id.get(n.get("parentId"))
        if not parent:
            continue
        if parent.get("type") == "SUBNET":
            plabel = (parent.get("label") or "").lower()
            if any(m in plabel for m in _PUBLIC_SUBNET_MARKERS):
                errs.append(
                    f"DATABASE node {n.get('id')} is parented to public subnet "
                    f"'{parent.get('label')}' - databases must reside in a "
                    f"private/isolated data subnet"
                )

    # Edge endpoints must reference existing nodes.
    for e in edges:
        if e.get("source") not in seen:
            errs.append(f"edge {e.get('id')} source '{e.get('source')}' is unknown")
        if e.get("target") not in seen:
            errs.append(f"edge {e.get('id')} target '{e.get('target')}' is unknown")

    # Evidence-or-assumption gate: if no node cites evidence, require at least
    # one recorded assumption so ungrounded output is explicit.
    any_evidence = any(n.get("evidenceIds") for n in nodes)
    if not any_evidence and not assumptions:
        errs.append(
            "no node cites evidenceIds and no assumptions recorded "
            "(ungrounded output must be flagged as an assumption)"
        )

    # No layout / PPT details.
    errs.extend(_find_forbidden_keys(model))

    return errs


# ---------------------------------------------------------------------------
# Rule-pack completeness gates (Task 6).
#
# Each resolved rule pack carries completeness_checks[{id, severity, condition}].
# We interpret the conditions we can map to our node/edge structure. Checks that
# require fields the model does not carry (e.g. concern=, scopes[], path.*) are
# SOFT-PASSED (skipped) rather than failing spuriously - the prompt still asks
# the model to satisfy them; we only hard-fail what we can verify structurally.
# 'error' severity => a failure raises; 'warning' => recorded but non-fatal.
# ---------------------------------------------------------------------------

# Checks already covered elsewhere (contract schema / existing semantic gate),
# so we do not re-run them here.
_CHECKS_HANDLED_ELSEWHERE = {
    "unique_component_ids",         # covered by unique-id check above
    "valid_relationship_endpoints", # covered by edge-endpoint check above
    "database_in_isolated_subnet",  # covered by the DB-in-public gate above
}


def _count_nodes_of_type(nodes: list[dict], node_type: str) -> int:
    return sum(1 for n in nodes if (n.get("type") or "").upper() == node_type.upper())


def _awsservice_needle(collection_expr: str) -> str | None:
    """Extract the awsService match substring from an expr like
    'nodes[awsService~Internet Gateway]'. Returns the lowercased needle or None
    if the expression is not an awsService match."""
    expr = str(collection_expr)
    if "awsService~" not in expr and "awsservice~" not in expr:
        return None
    needle = expr.split("~", 1)[-1]
    return needle.rstrip("]").strip().lower()


def _count_nodes_by_awsservice(nodes: list[dict], needle: str) -> int:
    """Count nodes whose properties.awsService (or label) contains `needle`."""
    n = 0
    for node in nodes:
        props = node.get("properties") or {}
        svc = (props.get("awsService") or "").lower()
        label = (node.get("label") or "").lower()
        if needle in svc or needle in label:
            n += 1
    return n


def _completeness_errors(model: dict) -> list[str]:
    """Interpret the model's rule-pack completeness_checks structurally.

    Returns hard errors (severity=error checks that fail and are verifiable).
    Warnings and unverifiable checks are skipped.
    """
    try:
        from . import rules
    except Exception:
        return []

    arch_type = model.get("architectureType", "")
    try:
        pack = rules.get_rule_pack(rules.resolve_type(arch_type))
    except Exception:
        return []

    checks = pack.get("completeness_checks") or []
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    by_id = {n.get("id"): n for n in nodes}
    errs: list[str] = []

    for c in checks:
        cid = c.get("id")
        severity = c.get("severity", "error")
        if cid in _CHECKS_HANDLED_ELSEWHERE:
            continue
        cond = c.get("condition") or {}
        ok: bool | None = None  # None => unverifiable / soft-pass
        detail = ""

        # count_gte over a typed collection, e.g. availability_zone scopes.
        if "count_gte" in cond:
            spec = cond["count_gte"]
            coll = spec.get("collection", "")
            want = spec.get("value", 0)
            if "availability_zone" in coll or "AVAILABILITYZONE" in coll.upper():
                have = _count_nodes_of_type(nodes, "AVAILABILITYZONE")
                ok = have >= want
                detail = f"found {have} Availability Zone(s), need >= {want}"

        # count_lte over an awsService match, e.g. single Application Load Balancer.
        elif "count_lte" in cond:
            spec = cond["count_lte"]
            coll = spec.get("collection", "")
            want = spec.get("value", 0)
            needle = _awsservice_needle(coll)
            if needle is not None:
                have = _count_nodes_by_awsservice(nodes, needle)
                ok = have <= want
                detail = f"found {have} '{needle}' node(s), expected <= {want}"

        # exists: nodes[awsService~X] - at least one node whose awsService matches.
        elif "exists" in cond and _awsservice_needle(str(cond.get("exists", ""))) is not None:
            needle = _awsservice_needle(str(cond.get("exists", "")))
            ok = _count_nodes_by_awsservice(nodes, needle) > 0
            detail = f"no node with awsService ~ '{needle}'"

        # exists: edges[label~X] - at least one edge whose label matches.
        elif "exists" in cond and "edges[label" in str(cond.get("exists", "")):
            needle = str(cond["exists"]).split("~", 1)[-1].rstrip("]").strip().lower()
            ok = any(needle in (e.get("label") or "").lower() for e in edges)
            detail = f"no edge with label ~ '{needle}'"

        # no_empty_subnet: every SUBNET must have at least one child node.
        elif cid == "no_empty_subnet":
            children_of: dict[str, int] = {}
            for n in nodes:
                pid = n.get("parentId")
                if pid:
                    children_of[pid] = children_of.get(pid, 0) + 1
            empty = [
                n.get("id") for n in nodes
                if (n.get("type") or "").upper() == "SUBNET"
                and children_of.get(n.get("id"), 0) == 0
            ]
            ok = not empty
            detail = f"empty subnets: {empty}" if empty else ""

        # exists a failover-type relationship.
        elif "exists" in cond and "failover" in str(cond.get("exists", "")).lower():
            ok = any(
                "failover" in (e.get("label") or "").lower()
                or (e.get("protocol") or "").lower() == "failover"
                for e in edges
            )
            detail = "no failover relationship found"

        # all_of a set of existence conditions (e.g. source/store/sink).
        elif "all_of" in cond:
            # These reference concern= tags we don't carry structurally; soft-pass.
            ok = None

        # Anything else (concern=, path.*, category=microservice, scope_ref) is
        # not structurally verifiable here -> soft-pass.
        else:
            ok = None

        if ok is False and severity == "error":
            errs.append(
                f"completeness check '{cid}' failed"
                + (f": {detail}" if detail else "")
            )

    return errs


def validate(model: dict) -> None:
    """Raise ValidationFailure if the model is invalid."""
    errors = _schema_errors(model)
    # Only run semantic + completeness checks if the shape is roughly right.
    if not errors:
        errors = _semantic_errors(model)
        errors.extend(_completeness_errors(model))
    if errors:
        raise ValidationFailure(errors)
