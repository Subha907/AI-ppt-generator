"""Semantic rule-pack loader for Agent 1.

Rule packs live in ``rules/semantic/<family>/<type>.yaml`` and declare, per
diagram type, WHAT a correct/complete diagram must contain (required concerns),
what to include conditionally, what is prohibited, and structural completeness
checks. They also carry ``renderer_hints.preferred_layout`` (the layout strategy
Agent 2 should use) and ``classification.trigger_terms`` (for future intent
detection).

This module:
  * discovers and parses every pack,
  * resolves the ``extends`` inheritance chain (a pack may extend common.yaml or
    another pack, e.g. multi_az extends deployment/logical),
  * merges parent + child into a single resolved pack,
  * exposes lookup by pack id and resolution from a loose type name/alias.

Merge semantics (child wins / accumulates over parent):
  * dict fields (classification, conditional_concerns, renderer_hints,
    grounding, aws_vocabulary, authoring_rules) -> deep-merged.
  * list fields (required_concerns, prohibited_behavior, completeness_checks,
    required_concerns, etc.) -> unioned, preserving order, de-duplicated.
  * scalars -> child overrides parent.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

import yaml

# rules/semantic lives next to the app package: <service>/rules/semantic
_SEMANTIC_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "rules" / "semantic"
)

# List-valued fields that should be UNIONED across the extends chain.
_LIST_UNION_FIELDS = {
    "required_concerns",
    "prohibited_behavior",
    "completeness_checks",
    "required_concerns",
}
# Dict-valued fields that should be DEEP-MERGED across the extends chain.
_DICT_MERGE_FIELDS = {
    "classification",
    "conditional_concerns",
    "renderer_hints",
    "grounding",
    "aws_vocabulary",
    "authoring_rules",
    "node_vocabulary",
    "completeness_checks_by_id",
}


class RulePackError(RuntimeError):
    pass


def _deep_merge(parent: dict, child: dict) -> dict:
    """Merge child into parent per the field semantics above. Returns a new dict."""
    out: dict[str, Any] = dict(parent)
    for key, cval in child.items():
        pval = out.get(key)
        if key in _LIST_UNION_FIELDS and isinstance(cval, list):
            merged: list = list(pval) if isinstance(pval, list) else []
            for item in cval:
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        elif key in _DICT_MERGE_FIELDS and isinstance(cval, dict):
            base = dict(pval) if isinstance(pval, dict) else {}
            base.update(cval)
            out[key] = base
        else:
            out[key] = cval
    return out


@lru_cache(maxsize=1)
def _raw_packs() -> dict[str, dict]:
    """Parse every *.yaml under the semantic dir, keyed by rule_pack.id.

    common.yaml has no rule_pack.id path family but does carry rule_pack.id
    ('common.semantic'); it is keyed by that id and also under the relative
    path 'semantic/common.yaml' so ``extends`` targets resolve either way.
    """
    if not _SEMANTIC_DIR.is_dir():
        raise RulePackError(f"Semantic rules dir not found: {_SEMANTIC_DIR}")
    packs: dict[str, dict] = {}
    by_relpath: dict[str, dict] = {}
    for path in sorted(_SEMANTIC_DIR.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rp = data.get("rule_pack", {})
        pack_id = rp.get("id")
        rel = "semantic/" + path.relative_to(_SEMANTIC_DIR).as_posix()
        data["_relpath"] = rel
        if pack_id:
            packs[pack_id] = data
        by_relpath[rel] = data
    # Fold path-addressed packs in so extends can reference either form.
    for rel, data in by_relpath.items():
        packs.setdefault(rel, data)
    return packs


def _resolve_extends(pack_id: str, _seen: tuple[str, ...] = ()) -> dict:
    """Return the fully merged pack: parents (in extends order) then this pack."""
    packs = _raw_packs()
    if pack_id not in packs:
        raise RulePackError(f"Unknown rule pack: {pack_id}")
    if pack_id in _seen:
        raise RulePackError(
            f"Circular extends chain: {' -> '.join(_seen + (pack_id,))}"
        )
    data = packs[pack_id]
    rp = data.get("rule_pack", {})
    parents = rp.get("extends", []) or []

    merged: dict[str, Any] = {}
    for parent_ref in parents:
        # extends refs are like 'semantic/common.yaml' or 'semantic/deployment/logical.yaml'
        parent_pack = _resolve_extends(parent_ref, _seen + (pack_id,))
        merged = _deep_merge(merged, parent_pack)
    merged = _deep_merge(merged, data)
    return merged


@lru_cache(maxsize=64)
def get_rule_pack(pack_id: str) -> dict:
    """Return the fully resolved (extends-merged) rule pack for an id or relpath."""
    return _resolve_extends(pack_id)


def list_rule_packs() -> list[str]:
    """All known rule-pack ids (dotted ids only, not the relpath aliases)."""
    return sorted(
        pid for pid, data in _raw_packs().items()
        if data.get("rule_pack", {}).get("id") == pid
    )


# ---------------------------------------------------------------------------
# Type resolution: map a loose type name / legacy enum / alias -> rule-pack id.
# ---------------------------------------------------------------------------
# Legacy architectureType values (from existing job records) map to a default
# pack within that family.
_LEGACY_TYPE_MAP = {
    "BUSINESS": "business.capability_map",
    "APPLICATION": "application.layered",
    "DEPLOYMENT": "deployment.multi_az",
}


def resolve_type(name: str) -> str:
    """Resolve a caller-supplied type string to a canonical rule-pack id.

    Accepts, in priority order:
      1. an exact rule-pack id (e.g. 'deployment.multi_az')
      2. a legacy enum value (BUSINESS/APPLICATION/DEPLOYMENT)
      3. a dotted or slashed variant (e.g. 'deployment/multi_az')
    Falls back to 'deployment.multi_az' if nothing matches.
    """
    if not name:
        return _LEGACY_TYPE_MAP["DEPLOYMENT"]
    raw = name.strip()
    packs = _raw_packs()
    # 1. exact id
    if raw in packs and packs[raw].get("rule_pack", {}).get("id") == raw:
        return raw
    # 2. legacy enum (case-insensitive)
    upper = raw.upper()
    if upper in _LEGACY_TYPE_MAP:
        return _LEGACY_TYPE_MAP[upper]
    # 3. normalise slashes/dots and retry
    dotted = raw.replace("/", ".").lower()
    if dotted in packs and packs[dotted].get("rule_pack", {}).get("id") == dotted:
        return dotted
    # 4. suffix match (e.g. 'multi_az' -> 'deployment.multi_az')
    for pid in list_rule_packs():
        if pid.split(".")[-1] == dotted or pid.endswith("." + dotted):
            return pid
    return _LEGACY_TYPE_MAP["DEPLOYMENT"]


def preferred_layout(pack_id: str) -> str | None:
    """The layout strategy Agent 2 should use for this pack, if declared."""
    return get_rule_pack(pack_id).get("renderer_hints", {}).get("preferred_layout")
