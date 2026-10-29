"""Agent 1 attaches the rule pack's preferred layout onto the model.

Tests the pure helper agent._attach_layout_hint (no AWS/network), and that the
resulting model still satisfies the architecture contract (additive field).
"""
from __future__ import annotations

import json
import pathlib
import sys

from jsonschema import Draft202012Validator

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.agent import _attach_layout_hint  # noqa: E402

_CONTRACT = (
    _SERVICE_ROOT.parents[1]
    / "packages"
    / "contracts"
    / "architecture_model.contract.json"
)


def _base_model(arch_type: str) -> dict:
    return {
        "schemaVersion": "1.0",
        "projectId": "prj_test",
        "jobId": "job_test",
        "architectureType": arch_type,
        "title": "Test Architecture",
        "nodes": [
            {
                "id": "n1",
                "type": "COMPUTE",
                "label": "Service",
                "parentId": None,
                "properties": {"awsService": "Amazon ECS"},
                "evidenceIds": ["ev-1"],
            }
        ],
        "edges": [],
    }


def test_attaches_multi_az_hint():
    model = _base_model("deployment.multi_az")
    _attach_layout_hint(model, "deployment.multi_az")
    assert model["rendererHints"]["preferredLayout"] == "multi_az_columns"


def test_attaches_application_hint():
    model = _base_model("application.layered")
    _attach_layout_hint(model, "application.layered")
    assert model["rendererHints"]["preferredLayout"] == "layered_architecture"


def test_does_not_overwrite_existing_hint():
    model = _base_model("deployment.multi_az")
    model["rendererHints"] = {"preferredLayout": "already_set"}
    _attach_layout_hint(model, "deployment.multi_az")
    assert model["rendererHints"]["preferredLayout"] == "already_set"


def test_model_with_hint_passes_contract_schema():
    model = _base_model("deployment.multi_az")
    _attach_layout_hint(model, "deployment.multi_az")
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(model))
    assert not errors, [e.message for e in errors]


def test_model_without_hint_still_valid_backward_compat():
    # A model that never had rendererHints must remain contract-valid.
    model = _base_model("deployment.multi_az")
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(model))
    assert not errors, [e.message for e in errors]


# --- Full standardized hint carry (Phase A) ---------------------------------
def test_carries_lane_order_for_data_flow():
    model = _base_model("data.data_flow")
    _attach_layout_hint(model, "data.data_flow")
    h = model["rendererHints"]
    assert h["preferredLayout"] == "left_to_right_flow"
    assert h["laneOrder"] == [
        "sources", "ingestion", "processing", "storage", "consumption"
    ]


def test_carries_layer_order_for_layered_application():
    model = _base_model("application.layered")
    _attach_layout_hint(model, "application.layered")
    h = model["rendererHints"]
    assert h["layerOrder"] == [
        "channels", "edge", "integration", "application_services", "data"
    ]


def test_carries_columns_for_modernization():
    model = _base_model("modernization.current_to_target")
    _attach_layout_hint(model, "modernization.current_to_target")
    h = model["rendererHints"]
    assert h["preferredLayout"] == "current_to_target"
    assert h["columns"] == ["current", "transition", "target"]


def test_carries_alternate_and_emphasis_for_network_focused():
    model = _base_model("deployment.network_focused")
    _attach_layout_hint(model, "deployment.network_focused")
    h = model["rendererHints"]
    assert h["preferredLayout"] == "hub_and_spoke"
    assert h["alternateLayout"] == "multi_az_columns"
    assert "ingress" in h["emphasis"] and "egress" in h["emphasis"]


def test_full_hints_model_passes_contract_schema():
    model = _base_model("data.data_flow")
    _attach_layout_hint(model, "data.data_flow")
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(model))
    assert not errors, [e.message for e in errors]
