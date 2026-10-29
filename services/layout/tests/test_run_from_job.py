"""End-to-end-ish test of Agent 2's run_from_job selection + geometry.

AWS is fully stubbed: we monkeypatch app.store so no DynamoDB/S3 is touched.
Verifies that the multi-AZ hint drives stack-abs, diagnostics land on
RunResult, an explicit override wins, the layout passes geometry gates, and
multi-AZ subnet tiers align across AZ columns.
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

_FIXTURE = _SERVICE_ROOT / "tests" / "fixtures" / "multi_az_arch.json"


@pytest.fixture()
def arch_model() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def stub_store(monkeypatch, arch_model):
    """Replace all store I/O with in-memory stubs."""
    captured: dict = {}

    monkeypatch.setattr(agent_mod.store, "get_job", lambda p, j: {"jobId": j})
    monkeypatch.setattr(agent_mod.store, "update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_mod.store, "get_architecture_model",
        lambda t, p, j: json.loads(json.dumps(arch_model)),
    )

    def _put(tenant, p, j, model):
        captured["layout"] = model
        return f"s3://stub/{p}/{j}/layout-model.json"

    monkeypatch.setattr(agent_mod.store, "put_layout_model", _put)
    return captured


def test_hint_drives_stack_abs_and_records_diagnostics(stub_store):
    result = agent_mod.run_from_job("prj_fixture", "job_fixture", tenant_id="t")
    assert result.engine == "stack-abs"
    assert result.strategy == "multi_az_columns"
    assert result.engine_source == "hint"
    assert result.preferred_layout == "multi_az_columns"
    assert result.selection_diagnostics
    # Geometry gates must hold for the reference pattern.
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0


def test_explicit_override_wins(stub_store):
    result = agent_mod.run_from_job(
        "prj_fixture", "job_fixture", tenant_id="t", engine="grid"
    )
    assert result.engine == "grid"
    assert result.engine_source == "override"


def test_layout_persisted_and_contract_shaped(stub_store):
    agent_mod.run_from_job("prj_fixture", "job_fixture", tenant_id="t")
    layout = stub_store["layout"]
    assert layout["schemaVersion"] == "1.0"
    assert layout["objects"]
    assert "slide" in layout
    for o in layout["objects"]:
        for key in ("x", "y", "w", "h", "z"):
            assert key in o


def test_multi_az_subnet_tiers_align_across_azs(stub_store):
    """Same-role subnets in different AZs should share the same Y lane
    (absolute-swimlane guarantee of the multi-AZ reference pattern)."""
    agent_mod.run_from_job("prj_fixture", "job_fixture", tenant_id="t")
    layout = stub_store["layout"]
    by_id = {o["objectId"]: o for o in layout["objects"]}

    pairs = [("az1-pub", "az2-pub"), ("az1-app", "az2-app"), ("az1-data", "az2-data")]
    for a, b in pairs:
        if a in by_id and b in by_id:
            ya, yb = by_id[a]["y"], by_id[b]["y"]
            assert abs(ya - yb) < 0.05, (
                f"subnet tier {a} (y={ya}) not aligned with {b} (y={yb})"
            )
