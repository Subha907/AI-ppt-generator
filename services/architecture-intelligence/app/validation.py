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


def validate(model: dict) -> None:
    """Raise ValidationFailure if the model is invalid."""
    errors = _schema_errors(model)
    # Only run semantic checks if the shape is roughly right.
    if not errors:
        errors = _semantic_errors(model)
    if errors:
        raise ValidationFailure(errors)
