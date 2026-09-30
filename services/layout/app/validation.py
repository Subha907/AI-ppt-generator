"""Validate the layout model against the contract + PDF geometry gates.

Layers:
  1. JSON Schema validation against the shared contract file.
  2. Deterministic geometry checks (PDF sections 5.2 and 11.2):
     - Bounds: every object within the slide safe area.
     - Containment: each child fully inside its parent's padding box.
     - Overlap: sibling objects (same parent) must not intersect.
     - Anchors: every connector references existing objects.
  The overlap/out-of-bounds counts are written back into validation{}.
"""
from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

CONTRACT_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "layout_model.contract.json"
)

# Small tolerance for float rounding (inches). The engine scales the diagram to
# fit the slide, so containment is checked as "child rect inside parent rect"
# with a rounding epsilon rather than against fixed (pre-scale) padding values.
EPS = 0.02


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


def _rect(o: dict) -> tuple[float, float, float, float]:
    return o["x"], o["y"], o["x"] + o["w"], o["y"] + o["h"]


def _intersect(a: dict, b: dict) -> bool:
    ax1, ay1, ax2, ay2 = _rect(a)
    bx1, by1, bx2, by2 = _rect(b)
    return not (
        ax2 <= bx1 + EPS
        or bx2 <= ax1 + EPS
        or ay2 <= by1 + EPS
        or by2 <= ay1 + EPS
    )


def check_geometry(model: dict) -> tuple[list[str], int, int]:
    """Return (errors, overlapCount, outOfBoundsCount)."""
    errors: list[str] = []
    slide = model["slide"]
    margin = slide["safeMarginIn"]
    min_x, min_y = margin, margin
    max_x = slide["widthIn"] - margin
    max_y = slide["heightIn"] - margin

    objects = model["objects"]
    by_id = {o["objectId"]: o for o in objects}

    # Bounds.
    out_of_bounds = 0
    for o in objects:
        x1, y1, x2, y2 = _rect(o)
        if x1 < min_x - EPS or y1 < min_y - EPS or x2 > max_x + EPS or y2 > max_y + EPS:
            out_of_bounds += 1
            errors.append(
                f"object {o['objectId']} out of bounds "
                f"({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f})"
            )

    # Containment.
    for o in objects:
        pid = o.get("parentObjectId")
        if not pid:
            continue
        parent = by_id.get(pid)
        if not parent:
            errors.append(f"object {o['objectId']} parent '{pid}' not found")
            continue
        px1, py1, px2, py2 = _rect(parent)
        cx1, cy1, cx2, cy2 = _rect(o)
        # Child must sit within the parent's box (rounding tolerance only).
        if (
            cx1 < px1 - EPS
            or cy1 < py1 - EPS
            or cx2 > px2 + EPS
            or cy2 > py2 + EPS
        ):
            errors.append(
                f"object {o['objectId']} not fully inside parent {pid}"
            )

    # Overlap among siblings (same parent).
    overlap = 0
    from collections import defaultdict

    siblings: dict[str, list[dict]] = defaultdict(list)
    for o in objects:
        siblings[o.get("parentObjectId") or "__root__"].append(o)
    for group in siblings.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if _intersect(group[i], group[j]):
                    overlap += 1
                    errors.append(
                        f"overlap: {group[i]['objectId']} & {group[j]['objectId']}"
                    )

    # Connector anchors.
    for c in model.get("connectors", []):
        if c["sourceObjectId"] not in by_id:
            errors.append(f"connector {c['edgeId']} source not found")
        if c["targetObjectId"] not in by_id:
            errors.append(f"connector {c['edgeId']} target not found")

    return errors, overlap, out_of_bounds


MIN_PADDING = 0.15  # Ruleset section 2: min padding between parent border/child


def auto_correct(model: dict) -> None:
    """Ruleset section 8: grow any parent whose child breaches its bounds so the
    child (plus 0.15in padding) fits inside. Applied bottom-up, in place."""
    objects = model["objects"]
    by_id = {o["objectId"]: o for o in objects}
    # Process deepest children first so growth propagates outward.
    for o in sorted(objects, key=lambda o: -o.get("z", 0)):
        pid = o.get("parentObjectId")
        parent = by_id.get(pid) if pid else None
        if not parent:
            continue
        need_right = (o["x"] + o["w"] + MIN_PADDING) - (parent["x"] + parent["w"])
        need_bottom = (o["y"] + o["h"] + MIN_PADDING) - (parent["y"] + parent["h"])
        if need_right > 0:
            parent["w"] = round(parent["w"] + need_right, 3)
        if need_bottom > 0:
            parent["h"] = round(parent["h"] + need_bottom, 3)
        # If a child starts before the parent's padded top-left, nudge parent.
        left_breach = (parent["x"] + MIN_PADDING) - o["x"]
        top_breach = (parent["y"] + MIN_PADDING) - o["y"]
        if left_breach > 0:
            parent["x"] = round(parent["x"] - left_breach, 3)
            parent["w"] = round(parent["w"] + left_breach, 3)
        if top_breach > 0:
            parent["y"] = round(parent["y"] - top_breach, 3)
            parent["h"] = round(parent["h"] + top_breach, 3)


def validate(model: dict) -> dict:
    """Validate and return the model with validation{} populated.

    Runs Ruleset section 8 auto-correction (grow breached parents) before the
    geometry gates. Raises ValidationFailure on schema errors or violations that
    remain after correction.
    """
    schema_errs = _schema_errors(model)
    if schema_errs:
        raise ValidationFailure(schema_errs)

    auto_correct(model)

    geo_errs, overlap, oob = check_geometry(model)
    model["validation"] = {"overlapCount": overlap, "outOfBoundsCount": oob}
    if geo_errs:
        raise ValidationFailure(geo_errs)
    return model
