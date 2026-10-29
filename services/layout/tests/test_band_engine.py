"""Geometry tests for the Phase C reusable band engine (lane + layer).

Verifies band ordering on the correct axis, containment of leaves within their
band, no overlaps / in-bounds (via the real validator), and behavior across
small/medium/dense topologies. No AWS/network access.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.band_engine import build_layout_from_plan  # noqa: E402
from app.planner import build_layout_plan  # noqa: E402
from app.policy import LayoutConfig, RendererHints  # noqa: E402
from app.validation import validate  # noqa: E402

_SLIDE = {"widthIn": 13.333, "heightIn": 7.5, "safeMarginIn": 0.3}

# node type -> the band token the planner's heuristic assigns.
_TYPES = ["EXTERNAL", "INTEGRATION", "COMPUTE", "DATABASE", "SECURITY"]


def _model(n_per_type: int) -> dict:
    nodes = []
    edges = []
    prev = None
    for t in _TYPES:
        for k in range(n_per_type):
            nid = f"{t.lower()}{k}"
            nodes.append({
                "id": nid, "type": t, "label": nid,
                "parentId": None, "properties": {},
            })
            if prev:
                edges.append({"id": f"e-{prev}-{nid}", "source": prev,
                              "target": nid, "label": "x"})
            prev = nid
    return {"projectId": "p", "jobId": "j", "nodes": nodes, "edges": edges}


def _cfg(strategy: str) -> LayoutConfig:
    return LayoutConfig(
        strategy=strategy, engine="stack", fallbacks=(), source="hint",
        preferred_layout=strategy, hints=RendererHints(preferred_layout=strategy),
    )


def _build(strategy: str, n_per_type: int):
    model = _model(n_per_type)
    plan = build_layout_plan(model, _cfg(strategy))
    layout = build_layout_from_plan(model, plan, _SLIDE)
    layout.pop("_warnings", None)
    return model, plan, layout


# --- lane: bands ordered left-to-right --------------------------------------
def test_lane_bands_ordered_left_to_right():
    _, plan, layout = _build("left_to_right_flow", 1)
    containers = {o["objectId"]: o for o in layout["objects"] if o["kind"] == "CONTAINER"}
    ordered = [b["bandId"] for b in sorted(plan["bands"], key=lambda b: b["order"])]
    xs = [containers[bid]["x"] for bid in ordered]
    assert xs == sorted(xs), f"lane bands not left-to-right: {list(zip(ordered, xs))}"


# --- layer: bands ordered top-to-bottom -------------------------------------
def test_layer_bands_ordered_top_to_bottom():
    _, plan, layout = _build("layered_architecture", 1)
    containers = {o["objectId"]: o for o in layout["objects"] if o["kind"] == "CONTAINER"}
    ordered = [b["bandId"] for b in sorted(plan["bands"], key=lambda b: b["order"])]
    ys = [containers[bid]["y"] for bid in ordered]
    assert ys == sorted(ys), f"layer bands not top-to-bottom: {list(zip(ordered, ys))}"


# --- containment: every leaf inside its band container ----------------------
@pytest.mark.parametrize("strategy", ["left_to_right_flow", "layered_architecture"])
def test_leaves_contained_in_their_band(strategy):
    _, _, layout = _build(strategy, 2)
    by_id = {o["objectId"]: o for o in layout["objects"]}
    for o in layout["objects"]:
        if o["kind"] != "ICON_LABEL":
            continue
        band = by_id[o["parentObjectId"]]
        assert o["x"] >= band["x"] - 0.02
        assert o["y"] >= band["y"] - 0.02
        assert o["x"] + o["w"] <= band["x"] + band["w"] + 0.02
        assert o["y"] + o["h"] <= band["y"] + band["h"] + 0.02


# --- geometry gates across small / medium / dense ---------------------------
@pytest.mark.parametrize("strategy", ["left_to_right_flow", "layered_architecture"])
@pytest.mark.parametrize("n_per_type", [1, 3, 6])  # small, medium, dense
def test_geometry_gates_hold(strategy, n_per_type):
    _, _, layout = _build(strategy, n_per_type)
    validated = validate(layout)  # raises on overlap / out-of-bounds / schema
    v = validated["validation"]
    assert v["overlapCount"] == 0
    assert v["outOfBoundsCount"] == 0


# --- connectors reference placed leaves -------------------------------------
def test_connectors_reference_placed_objects():
    _, _, layout = _build("left_to_right_flow", 2)
    ids = {o["objectId"] for o in layout["objects"]}
    for c in layout["connectors"]:
        assert c["sourceObjectId"] in ids
        assert c["targetObjectId"] in ids


# --- containment/none rejected (routed to other engines) --------------------
def test_wrong_band_kind_rejected():
    model = _model(1)
    plan = build_layout_plan(model, _cfg("multi_az_columns"))  # containment
    with pytest.raises(ValueError):
        build_layout_from_plan(model, plan, _SLIDE)


# ===========================================================================
# Phase D: column_compare / grouped_grid / central_hub
# ===========================================================================
def _leaf(nid, ntype, props=None):
    return {"id": nid, "type": ntype, "label": nid, "parentId": None,
            "properties": props or {}}


# --- column_compare ---------------------------------------------------------
def test_column_bands_ordered_left_to_right():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [
            _leaf("a", "SERVICE", {"column": "current"}),
            _leaf("b", "SERVICE", {"column": "transition"}),
            _leaf("c", "SERVICE", {"column": "target"}),
        ],
        "edges": [{"id": "e", "source": "a", "target": "c", "label": "migrate"}],
    }
    cfg = LayoutConfig(
        strategy="current_to_target", engine="stack", fallbacks=(),
        source="hint", preferred_layout="current_to_target",
        hints=RendererHints(preferred_layout="current_to_target",
                            columns=["current", "transition", "target"]),
    )
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "column"
    layout = build_layout_from_plan(model, plan, _SLIDE)
    layout.pop("_warnings", None)
    validate(layout)
    containers = {o["objectId"]: o for o in layout["objects"] if o["kind"] == "CONTAINER"}
    xs = [containers[b]["x"] for b in ["current", "transition", "target"]]
    assert xs == sorted(xs)


# --- grouped_grid -----------------------------------------------------------
def _capability_model(n_caps_per_group: int):
    nodes = [{"id": "root", "type": "CONTAINER", "label": "Biz", "parentId": None, "properties": {}}]
    edges = []
    for g in ("core", "support"):
        gid = f"grp-{g}"
        nodes.append({"id": gid, "type": "CONTAINER", "label": g.title(),
                      "parentId": "root", "properties": {}})
        for k in range(n_caps_per_group):
            nodes.append({"id": f"{g}{k}", "type": "CAPABILITY", "label": f"{g}{k}",
                          "parentId": gid, "properties": {}})
    return {"projectId": "p", "jobId": "j", "nodes": nodes, "edges": edges}


@pytest.mark.parametrize("n", [1, 3, 6])
def test_group_grid_containment_and_gates(n):
    model = _capability_model(n)
    cfg = LayoutConfig(
        strategy="capability_map", engine="stack", fallbacks=(), source="hint",
        preferred_layout="capability_map",
        hints=RendererHints(preferred_layout="capability_map"),
    )
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "group"
    layout = build_layout_from_plan(model, plan, _SLIDE)
    layout.pop("_warnings", None)
    validated = validate(layout)
    assert validated["validation"]["overlapCount"] == 0
    assert validated["validation"]["outOfBoundsCount"] == 0
    # Each capability sits inside its group container.
    by_id = {o["objectId"]: o for o in layout["objects"]}
    for o in layout["objects"]:
        if o["kind"] == "ICON_LABEL":
            band = by_id[o["parentObjectId"]]
            assert o["x"] >= band["x"] - 0.02
            assert o["x"] + o["w"] <= band["x"] + band["w"] + 0.02


# --- central_hub ------------------------------------------------------------
def _rag_model():
    return {
        "projectId": "p", "jobId": "j",
        "nodes": [
            _leaf("orch", "SERVICE"), _leaf("user", "ACTOR"),
            _leaf("kb", "DATABASE"), _leaf("model", "COMPUTE"),
            _leaf("emb", "INTEGRATION"),
        ],
        "edges": [
            {"id": "e1", "source": "user", "target": "orch", "label": "ask"},
            {"id": "e2", "source": "orch", "target": "kb", "label": "retrieve"},
        ],
    }


def test_hub_is_centered_between_spokes():
    model = _rag_model()
    cfg = LayoutConfig(
        strategy="central_platform", engine="stack", fallbacks=(), source="hint",
        preferred_layout="central_platform",
        hints=RendererHints(preferred_layout="central_platform"),
    )
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "hub"
    layout = build_layout_from_plan(model, plan, _SLIDE)
    layout.pop("_warnings", None)
    validated = validate(layout)
    assert validated["validation"]["overlapCount"] == 0
    assert validated["validation"]["outOfBoundsCount"] == 0
    containers = [o for o in layout["objects"] if o["kind"] == "CONTAINER"]
    hub = next(c for c in containers if c["objectId"] == "hub")
    others = [c for c in containers if c["objectId"] != "hub"]
    # Hub center-x lies between the min and max center-x of the spoke boxes.
    hub_cx = hub["x"] + hub["w"] / 2
    spoke_cxs = [c["x"] + c["w"] / 2 for c in others]
    assert min(spoke_cxs) <= hub_cx <= max(spoke_cxs)
