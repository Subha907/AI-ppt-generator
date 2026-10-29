"""Tests for the Phase F bounded visual-review correction loop.

Pure-function tests with injected review/relayout callables (no AWS/network).
"""
from __future__ import annotations

import pathlib
import sys

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.correction_loop import (  # noqa: E402
    issues_to_constraints,
    run_correction_loop,
)


def _report(decision, score, issues=None, provider="nova", is_stub=False):
    return {
        "schemaVersion": "1.0", "projectId": "p", "jobId": "j",
        "decision": decision, "score": score, "issues": issues or [],
        "provider": provider, "isStub": is_stub,
    }


def _noop_relayout(constraints, attempt):
    pass


# --- issue -> constraint translation ----------------------------------------
def test_issues_to_constraints_maps_known_rules():
    issues = [
        {"rule": "OVERLAP", "objectIds": ["a", "b"], "severity": "HIGH",
         "recommendedAction": {"type": "RESPACE", "delta": {"gap": 0.2}}},
        {"rule": "TEXT_CLIPPED", "objectIds": ["c"], "severity": "LOW"},
    ]
    cons = issues_to_constraints(issues)
    assert cons[0]["action"] == "increase_spacing"
    assert cons[0]["delta"] == {"gap": 0.2}
    assert cons[1]["action"] == "enlarge_label_box"


def test_unknown_rule_passed_through_not_dropped():
    cons = issues_to_constraints([{"rule": "FUTURE_RULE", "objectIds": ["x"]}])
    assert cons[0]["action"] == "review_hint"
    assert cons[0]["rule"] == "FUTURE_RULE"


# --- stops on PASS ----------------------------------------------------------
def test_stops_on_real_pass():
    result = run_correction_loop(
        lambda a: _report("PASS", 90), _noop_relayout, max_iterations=3
    )
    assert result.final_decision == "PASS"
    assert result.stopped_reason == "passed"
    assert result.iterations == 1
    assert result.reviewer_is_stub is False


# --- stub PASS is reported honestly -----------------------------------------
def test_stub_pass_is_flagged_not_a_real_approval():
    result = run_correction_loop(
        lambda a: _report("PASS", 85, provider="stub", is_stub=True),
        _noop_relayout, max_iterations=3,
    )
    assert result.final_decision == "PASS"
    assert result.stopped_reason == "stub"
    assert result.reviewer_is_stub is True
    assert any("STUB" in d for d in result.diagnostics)


# --- iteration cap ----------------------------------------------------------
def test_iteration_cap_respected():
    issues = [{"rule": "OVERLAP", "objectIds": ["a", "b"]}]
    scores = iter([10, 20, 30, 40, 50])  # improving but never PASS

    def review(attempt):
        return _report("REVISE", next(scores), issues)

    result = run_correction_loop(review, _noop_relayout, max_iterations=3)
    assert result.iterations == 3
    assert result.stopped_reason == "iteration_cap"
    assert len(result.history) == 3


# --- no-progress guard prevents oscillation ---------------------------------
def test_stops_when_no_score_improvement():
    issues = [{"rule": "OVERLAP", "objectIds": ["a", "b"]}]

    def review(attempt):
        return _report("REVISE", 50, issues)  # flat score

    result = run_correction_loop(review, _noop_relayout, max_iterations=5)
    assert result.stopped_reason == "no_progress"
    assert result.iterations == 2  # 1st sets baseline, 2nd detects no gain


# --- every iteration is recorded --------------------------------------------
def test_history_records_each_iteration():
    issues = [{"rule": "OVERLAP", "objectIds": ["a"]}]
    scores = iter([10, 40, 90])

    def review(attempt):
        s = next(scores)
        return _report("PASS" if s >= 80 else "REVISE", s, issues)

    result = run_correction_loop(review, _noop_relayout, max_iterations=5)
    assert result.final_decision == "PASS"
    assert [r.score for r in result.history] == [10, 40, 90]
    assert all(r.applied_constraints for r in result.history[:-1])


# --- relayout failure surfaces as ERROR, does not loop ----------------------
def test_relayout_failure_surfaces():
    def review(attempt):
        return _report("REVISE", 30, [{"rule": "OVERLAP", "objectIds": ["a"]}])

    def bad_relayout(constraints, attempt):
        raise RuntimeError("compile failed")

    result = run_correction_loop(review, bad_relayout, max_iterations=3)
    assert result.final_decision == "REVISE"
    assert result.stopped_reason == "error"
    assert any("re-layout failed" in d for d in result.diagnostics)
