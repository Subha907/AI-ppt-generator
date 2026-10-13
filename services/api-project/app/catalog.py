"""Architecture-type catalog, derived live from the semantic rule packs.

The single source of truth for "which diagram types can a user pick" is the
semantic rules folder owned by the Architecture Intelligence service
(Agent 1): ``services/architecture-intelligence/rules/semantic/<family>/<type>.yaml``.

This module scans that folder and exposes a two-level catalog:

    category (family)  ->  [ sub-type { subCategory, packId, label, triggerTerms } ]

e.g.  deployment -> [ multi_az, multi_region, network_focused, logical ]

A user selects a category + sub-category; we resolve that to the canonical
rule-pack id (e.g. "deployment.network_focused") which is stored on the job and
consumed by Agent 1. Adding a new YAML pack automatically adds a selectable
option here - no code change required.

The common.yaml pack is NOT a user-selectable diagram type (it is the shared
base other packs extend), so it is excluded from the catalog.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

import yaml

# api-project/app/catalog.py -> services/ -> architecture-intelligence rules.
#   parents[0]=app  parents[1]=api-project  parents[2]=services
_SEMANTIC_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "architecture-intelligence"
    / "rules"
    / "semantic"
)

# Legacy family words still accepted for backward compatibility, each mapped to
# its default sub-type pack id.
LEGACY_FAMILY_DEFAULT = {
    "BUSINESS": "business.capability_map",
    "APPLICATION": "application.layered",
    "DEPLOYMENT": "deployment.multi_az",
}


def _titleize(token: str) -> str:
    """'multi_az' -> 'Multi Az', 'network_focused' -> 'Network Focused'."""
    return " ".join(w.capitalize() for w in token.replace("-", "_").split("_"))


@lru_cache(maxsize=1)
def _scan() -> dict[str, list[dict[str, Any]]]:
    """Return {category: [subtype dict, ...]} parsed from the rules folder."""
    catalog: dict[str, list[dict[str, Any]]] = {}
    if not _SEMANTIC_DIR.is_dir():
        return catalog
    for path in sorted(_SEMANTIC_DIR.rglob("*.yaml")):
        # Skip the shared base pack: it has no family folder (sits at root).
        rel = path.relative_to(_SEMANTIC_DIR)
        if len(rel.parts) < 2:
            continue  # e.g. common.yaml
        family = rel.parts[0]
        sub = path.stem
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        rp = data.get("rule_pack", {})
        pack_id = rp.get("id") or f"{family}.{sub}"
        classification = data.get("classification", {}) or {}
        catalog.setdefault(family, []).append(
            {
                "subCategory": sub,
                "packId": pack_id,
                "label": _titleize(sub),
                "triggerTerms": classification.get("trigger_terms", []) or [],
            }
        )
    # Stable ordering within each family.
    for subs in catalog.values():
        subs.sort(key=lambda s: s["subCategory"])
    return catalog


def catalog() -> list[dict[str, Any]]:
    """The full catalog as a list of families with their sub-types.

    [ { category, label, subTypes: [ {subCategory, packId, label, triggerTerms} ] } ]
    """
    scanned = _scan()
    out = []
    for family in sorted(scanned.keys()):
        out.append(
            {
                "category": family,
                "label": _titleize(family),
                "subTypes": scanned[family],
            }
        )
    return out


def all_pack_ids() -> set[str]:
    ids: set[str] = set()
    for subs in _scan().values():
        for s in subs:
            ids.add(s["packId"])
    return ids


def resolve(
    category: str | None,
    sub_category: str | None,
    architecture_type: str | None,
) -> str:
    """Resolve a user selection to a canonical rule-pack id.

    Priority:
      1. explicit architecture_type that is already a valid pack id -> use it
      2. legacy family word (BUSINESS/APPLICATION/DEPLOYMENT) -> its default pack
      3. category + sub_category -> '<category>.<sub_category>' if it exists
      4. category alone -> first sub-type in that family (or legacy default)

    Raises ValueError if the selection cannot be resolved to a known pack.
    """
    scanned = _scan()
    valid_ids = all_pack_ids()

    # 1. direct pack id.
    if architecture_type and architecture_type in valid_ids:
        return architecture_type

    # 2. legacy family word.
    if architecture_type:
        up = architecture_type.strip().upper()
        if up in LEGACY_FAMILY_DEFAULT:
            return LEGACY_FAMILY_DEFAULT[up]

    # 3. category + sub-category.
    if category:
        fam = category.strip().lower()
        # allow passing the legacy upper word as category too.
        if fam.upper() in LEGACY_FAMILY_DEFAULT and not sub_category:
            return LEGACY_FAMILY_DEFAULT[fam.upper()]
        subs = scanned.get(fam, [])
        if sub_category:
            sc = sub_category.strip().lower()
            for s in subs:
                if s["subCategory"] == sc:
                    return s["packId"]
            raise ValueError(
                f"Unknown sub-category '{sub_category}' for category '{category}'. "
                f"Valid: {[s['subCategory'] for s in subs]}"
            )
        # 4. category alone -> first sub-type.
        if subs:
            return subs[0]["packId"]
        raise ValueError(f"Unknown category '{category}'.")

    raise ValueError(
        "No architecture type selected: provide category (+ subCategory) "
        "or a valid architectureType/packId."
    )
