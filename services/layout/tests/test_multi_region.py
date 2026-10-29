"""Multi-region layout correctness (region-mirror pass).

multi_region_mirror must render each REGION subtree side-by-side with its nested
VPC/AZ/subnet containment preserved, and must survive validate()'s auto_correct
without sibling regions overlapping (the defect that broke the naive mappings),
including when an account-level global service (Route 53) is present.
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
from app.policy import resolve_layout_config  # noqa: E402
from app.stack_engine import build_layout_stack  # noqa: E402
from app.validation import validate  # noqa: E402

_FIXTURE = _SERVICE_ROOT / "tests" / "fixtures" / "multi_region_arch.json"
_SLIDE = {"widthIn": 13.333, "heightIn": 7.5, "safeMarginIn": 0.3}


@pytest.fixture()
def model() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_resolves_to_region_mirror_engine(model):
    cfg = resolve_layout_config(model)
    assert cfg.engine == "stack-region"
    assert cfg.strategy == "multi_region_mirror"


def test_two_regions_side_by_side_no_overlap(model):
    layout = build_layout_stack(model, _SLIDE, region_mirror=True)
    validated = validate(layout)  # includes auto_correct
    assert validated["validation"]["overlapCount"] == 0
    assert validated["validation"]["outOfBoundsCount"] == 0

    by_id = {o["objectId"]: o for o in layout["objects"]}
    assert "reg-primary" in by_id and "reg-recovery" in by_id
    p, s = by_id["reg-primary"], by_id["reg-recovery"]
    assert (p["x"] + p["w"] <= s["x"] + 0.05) or (s["x"] + s["w"] <= p["x"] + 0.05)


def test_each_region_keeps_its_own_vpc_and_db(model):
    layout = build_layout_stack(model, _SLIDE, region_mirror=True)
    by_id = {o["objectId"]: o for o in layout["objects"]}
    for reg, rds in (("reg-primary", "p-rds"), ("reg-recovery", "s-rds")):
        r, d = by_id[reg], by_id[rds]
        assert r["x"] - 0.05 <= d["x"] and d["x"] + d["w"] <= r["x"] + r["w"] + 0.05
        assert r["y"] - 0.05 <= d["y"] and d["y"] + d["h"] <= r["y"] + r["h"] + 0.05


def test_global_route53_does_not_overlap_regions(model):
    layout = build_layout_stack(model, _SLIDE, region_mirror=True)
    by_id = {o["objectId"]: o for o in layout["objects"]}
    r53 = by_id["r53"]

    def _rect(o):
        return o["x"], o["y"], o["x"] + o["w"], o["y"] + o["h"]

    def _intersect(a, b):
        ax1, ay1, ax2, ay2 = _rect(a)
        bx1, by1, bx2, by2 = _rect(b)
        return not (ax2 <= bx1 + 0.02 or bx2 <= ax1 + 0.02
                    or ay2 <= by1 + 0.02 or by2 <= ay1 + 0.02)

    # r53 sits in the top band; it must not visually collide with either region.
    assert not _intersect(r53, by_id["reg-primary"])
    assert not _intersect(r53, by_id["reg-recovery"])


def test_multi_region_through_run_from_job(monkeypatch, model):
    captured: dict = {}
    monkeypatch.setattr(agent_mod.store, "get_job", lambda p, j: {"jobId": j})
    monkeypatch.setattr(agent_mod.store, "update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_mod.store, "get_architecture_model",
        lambda t, p, j: json.loads(json.dumps(model)),
    )
    monkeypatch.setattr(
        agent_mod.store, "put_layout_model",
        lambda t, p, j, m: captured.setdefault("layout", m) or "s3://stub/x",
    )
    result = agent_mod.run_from_job("prj_mr", "job_mr", tenant_id="t")
    assert result.engine == "stack-region"
    assert result.strategy == "multi_region_mirror"
    assert result.overlap_count == 0
    assert result.out_of_bounds_count == 0
