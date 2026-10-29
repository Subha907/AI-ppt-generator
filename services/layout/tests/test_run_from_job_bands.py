"""Integration test: lane/layer models flow through run_from_job and the plan
DRIVES the band engine (Phase C), producing valid geometry. AWS is stubbed.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app import agent as agent_mod  # noqa: E402


def _flow_model() -> dict:
    return {
        "schemaVersion": "1.0",
        "projectId": "prj_flow",
        "jobId": "job_flow",
        "architectureType": "data.data_flow",
        "title": "Data Flow",
        "rendererHints": {
            "preferredLayout": "left_to_right_flow",
            "laneOrder": ["sources", "ingestion", "processing", "storage", "consumption"],
        },
        "nodes": [
            {"id": "src", "type": "EXTERNAL", "label": "Source",
             "parentId": None, "properties": {"lane": "sources"}},
            {"id": "ing", "type": "INTEGRATION", "label": "Glue",
             "parentId": None, "properties": {"lane": "ingestion"}},
            {"id": "proc", "type": "COMPUTE", "label": "EMR",
             "parentId": None, "properties": {"lane": "processing"}},
            {"id": "store", "type": "STORAGE", "label": "S3",
             "parentId": None, "properties": {"lane": "storage"}},
            {"id": "cons", "type": "SERVICE", "label": "QuickSight",
             "parentId": None, "properties": {"lane": "consumption"}},
        ],
        "edges": [
            {"id": "e1", "source": "src", "target": "ing", "label": "load"},
            {"id": "e2", "source": "ing", "target": "proc", "label": "etl"},
            {"id": "e3", "source": "proc", "target": "store", "label": "write"},
            {"id": "e4", "source": "store", "target": "cons", "label": "query"},
        ],
    }


def _layered_model() -> dict:
    return {
        "schemaVersion": "1.0",
        "projectId": "prj_lyr",
        "jobId": "job_lyr",
        "architectureType": "application.layered",
        "title": "Layered App",
        "rendererHints": {
            "preferredLayout": "layered_architecture",
            "layerOrder": ["channels", "application_services", "integration", "data"],
        },
        "nodes": [
            {"id": "web", "type": "COMPONENT", "label": "Web",
             "parentId": None, "properties": {"layer": "channels"}},
            {"id": "svc", "type": "SERVICE", "label": "Order Svc",
             "parentId": None, "properties": {"layer": "application_services"}},
            {"id": "bus", "type": "INTEGRATION", "label": "Event Bus",
             "parentId": None, "properties": {"layer": "integration"}},
            {"id": "db", "type": "DATABASE", "label": "Order DB",
             "parentId": None, "properties": {"layer": "data"}},
        ],
        "edges": [
            {"id": "e1", "source": "web", "target": "svc", "label": "calls"},
            {"id": "e2", "source": "svc", "target": "bus", "label": "publishes"},
            {"id": "e3", "source": "svc", "target": "db", "label": "reads/writes"},
        ],
    }


@pytest.fixture()
def stub_store(monkeypatch):
    captured: dict = {}
    model_holder: dict = {}

    monkeypatch.setattr(agent_mod.store, "get_job", lambda p, j: {"jobId": j})
    monkeypatch.setattr(agent_mod.store, "update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_mod.store, "get_architecture_model",
        lambda t, p, j: json.loads(json.dumps(model_holder["model"])),
    )

    def _put(tenant, p, j, model):
        captured["layout"] = model
        return f"s3://stub/{p}/{j}/layout-model.json"

    monkeypatch.setattr(agent_mod.store, "put_layout_model", _put)
    return captured, model_holder


def test_flow_model_uses_band_engine(stub_store):
    captured, holder = stub_store
    holder["model"] = _flow_model()
    result = agent_mod.run_from_job("prj_flow", "job_flow", tenant_id="t")
    assert result.engine == "band"
    assert result.band_kind == "lane"
    assert result.plan_valid is True
    assert result.band_count == 5
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0
    # Bands laid left-to-right in laneOrder.
    layout = captured["layout"]
    containers = {o["objectId"]: o for o in layout["objects"] if o["kind"] == "CONTAINER"}
    xs = [containers[b]["x"] for b in
          ["sources", "ingestion", "processing", "storage", "consumption"]]
    assert xs == sorted(xs)


def test_layered_model_uses_band_engine(stub_store):
    captured, holder = stub_store
    holder["model"] = _layered_model()
    result = agent_mod.run_from_job("prj_lyr", "job_lyr", tenant_id="t")
    assert result.engine == "band"
    assert result.band_kind == "layer"
    assert result.plan_valid is True
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0


def test_explicit_engine_override_bypasses_band_engine(stub_store):
    captured, holder = stub_store
    holder["model"] = _flow_model()
    result = agent_mod.run_from_job(
        "prj_flow", "job_flow", tenant_id="t", engine="stack"
    )
    # Caller forced stack -> plan does not drive geometry.
    assert result.engine == "stack"


# ===========================================================================
# Phase D integration: column / group / hub categories through run_from_job
# ===========================================================================
def _modernization_model() -> dict:
    return {
        "schemaVersion": "1.0", "projectId": "prj_mod", "jobId": "job_mod",
        "architectureType": "modernization.current_to_target",
        "title": "Current -> Target",
        "rendererHints": {
            "preferredLayout": "current_to_target",
            "columns": ["current", "transition", "target"],
        },
        "nodes": [
            {"id": "mono", "type": "COMPUTE", "label": "Monolith",
             "parentId": None, "properties": {"column": "current"}},
            {"id": "strangler", "type": "INTEGRATION", "label": "Strangler",
             "parentId": None, "properties": {"column": "transition"}},
            {"id": "svc", "type": "SERVICE", "label": "Microservice",
             "parentId": None, "properties": {"column": "target"}},
        ],
        "edges": [
            {"id": "e1", "source": "mono", "target": "strangler", "label": "wrap"},
            {"id": "e2", "source": "strangler", "target": "svc", "label": "extract"},
        ],
    }


def _capability_model() -> dict:
    return {
        "schemaVersion": "1.0", "projectId": "prj_cap", "jobId": "job_cap",
        "architectureType": "business.capability_map",
        "title": "Capability Map",
        "rendererHints": {"preferredLayout": "capability_map"},
        "nodes": [
            {"id": "root", "type": "CONTAINER", "label": "Business", "parentId": None, "properties": {}},
            {"id": "grp-core", "type": "CONTAINER", "label": "Core", "parentId": "root", "properties": {}},
            {"id": "grp-sup", "type": "CONTAINER", "label": "Supporting", "parentId": "root", "properties": {}},
            {"id": "cap1", "type": "CAPABILITY", "label": "Grading", "parentId": "grp-core", "properties": {}},
            {"id": "cap2", "type": "CAPABILITY", "label": "Certification", "parentId": "grp-core", "properties": {}},
            {"id": "cap3", "type": "CAPABILITY", "label": "Billing", "parentId": "grp-sup", "properties": {}},
        ],
        "edges": [],
    }


def _rag_model() -> dict:
    return {
        "schemaVersion": "1.0", "projectId": "prj_rag", "jobId": "job_rag",
        "architectureType": "genai.rag",
        "title": "RAG",
        "rendererHints": {"preferredLayout": "central_platform"},
        "nodes": [
            {"id": "orch", "type": "SERVICE", "label": "Orchestrator", "parentId": None, "properties": {}},
            {"id": "user", "type": "ACTOR", "label": "User", "parentId": None, "properties": {}},
            {"id": "kb", "type": "DATABASE", "label": "Vector Store", "parentId": None, "properties": {}},
            {"id": "model", "type": "COMPUTE", "label": "LLM", "parentId": None, "properties": {}},
        ],
        "edges": [
            {"id": "e1", "source": "user", "target": "orch", "label": "ask"},
            {"id": "e2", "source": "orch", "target": "kb", "label": "retrieve"},
            {"id": "e3", "source": "orch", "target": "model", "label": "generate"},
        ],
    }


def test_modernization_uses_column_band(stub_store):
    captured, holder = stub_store
    holder["model"] = _modernization_model()
    result = agent_mod.run_from_job("prj_mod", "job_mod", tenant_id="t")
    assert result.engine == "band"
    assert result.band_kind == "column"
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0


def test_capability_map_uses_group_band(stub_store):
    captured, holder = stub_store
    holder["model"] = _capability_model()
    result = agent_mod.run_from_job("prj_cap", "job_cap", tenant_id="t")
    assert result.engine == "band"
    assert result.band_kind == "group"
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0


def test_rag_uses_hub_band(stub_store):
    captured, holder = stub_store
    holder["model"] = _rag_model()
    result = agent_mod.run_from_job("prj_rag", "job_rag", tenant_id="t")
    assert result.engine == "band"
    assert result.band_kind == "hub"
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0
