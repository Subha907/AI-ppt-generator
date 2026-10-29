"""Tests for the Phase B deterministic layout planner.

Covers: strategy -> bandKind mapping, hint-driven ordering (lane/layer/column),
topology containment bands (multi-AZ), node assignment coverage, unassigned
reporting, determinism, and plan schema + structural validation.
No AWS/network access.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.planner import (  # noqa: E402
    PlanValidationError,
    band_kind_for,
    build_layout_plan,
    validate_plan,
)
from app.policy import LayoutConfig, RendererHints  # noqa: E402

_FIXTURE = _SERVICE_ROOT / "tests" / "fixtures" / "multi_az_arch.json"


def _cfg(strategy, **hint_kwargs):
    return LayoutConfig(
        strategy=strategy,
        engine="stack",
        fallbacks=(),
        source="hint",
        preferred_layout=strategy,
        hints=RendererHints(**hint_kwargs),
    )


def _leaf(nid, ntype, props=None):
    return {"id": nid, "type": ntype, "label": nid, "parentId": None,
            "properties": props or {}}


# --- strategy -> bandKind ---------------------------------------------------
@pytest.mark.parametrize("strategy,kind", [
    ("left_to_right_flow", "lane"),
    ("swimlane", "lane"),
    ("layered_architecture", "layer"),
    ("layered_controls", "layer"),
    ("current_to_target", "column"),
    ("capability_map", "group"),
    ("domain_grouped_layers", "group"),
    ("central_platform", "hub"),
    ("hub_and_spoke", "hub"),
    ("multi_az_columns", "containment"),
    ("multi_region_mirror", "containment"),
    ("generic_stack", "none"),
    ("totally_unknown", "none"),
])
def test_band_kind_mapping(strategy, kind):
    assert band_kind_for(strategy) == kind


# --- lane ordering follows laneOrder hint -----------------------------------
def test_lane_order_follows_hint():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [
            _leaf("db1", "DATABASE"),
            _leaf("ing1", "INTEGRATION"),
            _leaf("src1", "EXTERNAL"),
        ],
    }
    cfg = _cfg("left_to_right_flow",
              preferred_layout="left_to_right_flow",
              lane_order=["external", "integration", "data"])
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "lane"
    assert [b["bandId"] for b in plan["bands"]] == ["external", "integration", "data"]
    assert plan["orderingSource"] == "hint"
    # Every leaf assigned to the matching band.
    by_node = {a["nodeId"]: a["bandId"] for a in plan["assignments"]}
    assert by_node == {"src1": "external", "ing1": "integration", "db1": "data"}
    validate_plan(plan, model)


def test_explicit_node_property_overrides_type_heuristic():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [_leaf("n1", "COMPUTE", {"lane": "processing"})],
    }
    cfg = _cfg("left_to_right_flow", lane_order=["processing"])
    plan = build_layout_plan(model, cfg)
    assert plan["assignments"][0]["bandId"] == "processing"


# --- layer ordering ---------------------------------------------------------
def test_layer_order_follows_hint():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [_leaf("svc", "SERVICE"), _leaf("ui", "COMPONENT", {"layer": "channels"})],
    }
    cfg = _cfg("layered_architecture",
              layer_order=["channels", "application_services", "data"])
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "layer"
    assert [b["bandId"] for b in plan["bands"]][0] == "channels"


# --- column comparison ------------------------------------------------------
def test_columns_follow_hint():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [
            _leaf("a", "SERVICE", {"column": "current"}),
            _leaf("b", "SERVICE", {"column": "target"}),
        ],
    }
    cfg = _cfg("current_to_target", columns=["current", "transition", "target"])
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "column"
    assert [b["bandId"] for b in plan["bands"]] == ["current", "transition", "target"]


# --- containment (multi-AZ) -------------------------------------------------
def test_multi_az_uses_containment_bands():
    model = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    cfg = _cfg("multi_az_columns", preferred_layout="multi_az_columns")
    plan = build_layout_plan(model, cfg)
    assert plan["bandKind"] == "containment"
    # Top-level 'acct' is the only root -> single containment band holding all.
    assert [b["bandId"] for b in plan["bands"]] == ["acct"]
    assigned = {a["nodeId"] for a in plan["assignments"]}
    # Every node descends from acct, so all are assigned.
    assert assigned == {n["id"] for n in model["nodes"]}
    validate_plan(plan, model)


# --- unassigned reporting (never silent) ------------------------------------
def test_unassigned_nodes_reported():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [_leaf("mystery", "PROCESS")],  # 'process' token...
    }
    # ...but lane_order omits it -> appended with a diagnostic, still assigned.
    cfg = _cfg("left_to_right_flow", lane_order=["sources"])
    plan = build_layout_plan(model, cfg)
    assert any("not in policy ordering" in d for d in plan["diagnostics"])


# --- determinism ------------------------------------------------------------
def test_plan_is_deterministic():
    model = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    cfg = _cfg("multi_az_columns")
    a = build_layout_plan(model, cfg)
    b = build_layout_plan(model, cfg)
    assert a == b


# --- validation gates -------------------------------------------------------
def test_validate_plan_rejects_unknown_band():
    model = {"projectId": "p", "jobId": "j", "nodes": [_leaf("n1", "SERVICE")]}
    plan = build_layout_plan(model, _cfg("generic_stack"))
    plan["assignments"].append({"nodeId": "n1", "bandId": "ghost", "rank": None})
    with pytest.raises(PlanValidationError):
        validate_plan(plan, model)


def test_validate_plan_rejects_unassigned_leaf():
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [_leaf("n1", "SERVICE"), _leaf("n2", "SERVICE")],
    }
    plan = build_layout_plan(model, _cfg("generic_stack"))
    plan["assignments"] = [a for a in plan["assignments"] if a["nodeId"] != "n2"]
    with pytest.raises(PlanValidationError):
        validate_plan(plan, model)


def test_plan_has_no_coordinates():
    model = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    plan = build_layout_plan(model, _cfg("multi_az_columns"))
    blob = json.dumps(plan)
    for geom_key in ('"x"', '"y"', '"w"', '"h"', '"points"', '"route"'):
        assert geom_key not in blob
