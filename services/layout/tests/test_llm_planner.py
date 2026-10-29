"""Tests for the Phase E optional LLM layout planner.

Uses a FAKE Bedrock client (no network). Verifies: a valid LLM response drives
band order + assignments; malformed / geometry-leaking / incomplete responses
fall back to the deterministic planner with a diagnostic (never silent); the LLM
never influences coordinates.
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
    LLMPlannerError,
    build_plan,
    llm_build_layout_plan,
    validate_plan,
)
from app.policy import LayoutConfig, RendererHints  # noqa: E402


class FakeClient:
    """Minimal Bedrock-converse stand-in returning a canned text payload."""

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self._text}]}}}


def _model():
    def leaf(i, t):
        return {"id": i, "type": t, "label": i, "parentId": None, "properties": {}}
    return {
        "projectId": "p", "jobId": "j",
        "nodes": [leaf("s", "EXTERNAL"), leaf("i", "INTEGRATION"),
                  leaf("d", "DATABASE")],
        "edges": [],
    }


def _cfg():
    return LayoutConfig(
        strategy="left_to_right_flow", engine="stack", fallbacks=(),
        source="hint", preferred_layout="left_to_right_flow",
        hints=RendererHints(preferred_layout="left_to_right_flow",
                            lane_order=["external", "integration", "data"]),
    )


# --- valid LLM plan ---------------------------------------------------------
def test_valid_llm_plan_is_applied():
    # Reassign every leaf and reorder bands using only known ids.
    payload = json.dumps({
        "bandOrder": ["data", "integration", "external"],
        "assignments": [
            {"nodeId": "s", "bandId": "external"},
            {"nodeId": "i", "bandId": "integration"},
            {"nodeId": "d", "bandId": "data"},
        ],
    })
    plan = llm_build_layout_plan(_model(), _cfg(), FakeClient(payload))
    assert [b["bandId"] for b in plan["bands"]] == ["data", "integration", "external"]
    assert {a["nodeId"] for a in plan["assignments"]} == {"s", "i", "d"}
    validate_plan(plan, _model())  # still contract-valid


# --- geometry leakage rejected ---------------------------------------------
def test_llm_plan_with_coordinates_is_rejected():
    payload = json.dumps({
        "bandOrder": ["external", "integration", "data"],
        "assignments": [{"nodeId": "s", "bandId": "external", "x": 1.0}],
    })
    with pytest.raises(LLMPlannerError):
        llm_build_layout_plan(_model(), _cfg(), FakeClient(payload))


# --- incomplete assignment rejected ----------------------------------------
def test_llm_plan_missing_node_is_rejected():
    payload = json.dumps({
        "bandOrder": ["external", "integration", "data"],
        "assignments": [{"nodeId": "s", "bandId": "external"}],  # i, d missing
    })
    with pytest.raises(LLMPlannerError):
        llm_build_layout_plan(_model(), _cfg(), FakeClient(payload))


# --- build_plan dispatch: nova success --------------------------------------
def test_build_plan_nova_success():
    payload = json.dumps({
        "bandOrder": ["external", "integration", "data"],
        "assignments": [
            {"nodeId": "s", "bandId": "external"},
            {"nodeId": "i", "bandId": "integration"},
            {"nodeId": "d", "bandId": "data"},
        ],
    })
    plan, provider, diags = build_plan(
        _model(), _cfg(), provider="nova", client=FakeClient(payload)
    )
    assert provider == "nova"
    assert any("LLM planner" in d for d in diags)


# --- build_plan dispatch: malformed -> deterministic fallback ---------------
def test_build_plan_nova_fallback_on_garbage():
    plan, provider, diags = build_plan(
        _model(), _cfg(), provider="nova", client=FakeClient("not json at all")
    )
    assert provider == "deterministic"
    assert any("fell back to deterministic" in d for d in diags)
    # Fallback plan is still valid.
    validate_plan(plan, _model())


# --- build_plan dispatch: deterministic default -----------------------------
def test_build_plan_deterministic_default_does_not_call_client():
    client = FakeClient("{}")
    plan, provider, diags = build_plan(_model(), _cfg(), provider="deterministic",
                                       client=client)
    assert provider == "deterministic"
    assert client.calls == 0


# --- containment plans are not sent to the LLM ------------------------------
def test_llm_planner_passthrough_for_containment():
    cfg = LayoutConfig(
        strategy="multi_az_columns", engine="stack-abs", fallbacks=(),
        source="hint", preferred_layout="multi_az_columns",
        hints=RendererHints(preferred_layout="multi_az_columns"),
    )
    model = {
        "projectId": "p", "jobId": "j",
        "nodes": [{"id": "acct", "type": "ACCOUNT", "label": "A",
                   "parentId": None, "properties": {}}],
        "edges": [],
    }
    client = FakeClient("{}")
    plan = llm_build_layout_plan(model, cfg, client)
    assert plan["bandKind"] == "containment"
    assert client.calls == 0  # containment is authoritative; LLM not consulted
