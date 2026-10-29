"""Tests for the Agent 2 layout-policy resolver (Phase 2).

Covers: hint -> strategy -> engine mapping, caller override precedence,
unsupported/absent hint handling (never silent), and the multi-AZ reference
mapping. Pure-function tests, no AWS/network access.
"""
from __future__ import annotations

import pathlib
import sys

# Make the service package importable as `app` when run from anywhere.
_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.policy import (  # noqa: E402
    DEFAULT_ENGINE,
    DEFAULT_STRATEGY,
    SUPPORTED_ENGINES,
    resolve_layout_config,
)


def _model(preferred=None):
    m = {"schemaVersion": "1.0", "projectId": "p", "jobId": "j", "nodes": [], "edges": []}
    if preferred is not None:
        m["rendererHints"] = {"preferredLayout": preferred}
    return m


# --- Reference mapping: multi-AZ --------------------------------------------
def test_multi_az_hint_maps_to_stack_abs():
    cfg = resolve_layout_config(_model("multi_az_columns"))
    assert cfg.engine == "stack-abs"
    assert cfg.strategy == "multi_az_columns"
    assert cfg.source == "hint"
    assert cfg.preferred_layout == "multi_az_columns"
    # Fallbacks are real engines and do not repeat the primary.
    assert "stack-abs" not in cfg.fallbacks
    assert all(e in SUPPORTED_ENGINES for e in cfg.fallbacks)
    assert cfg.diagnostics  # never silent


def test_multi_region_hint_maps_to_stack_region():
    # multi_region uses the dedicated region-mirror pass (side-by-side region
    # columns), NOT the single-VPC stack-abs swimlane pass.
    cfg = resolve_layout_config(_model("multi_region_mirror"))
    assert cfg.engine == "stack-region"
    assert cfg.source == "hint"


# --- Application / other strategies are recognised (not "unsupported") ------
def test_layered_architecture_hint_recognised():
    cfg = resolve_layout_config(_model("layered_architecture"))
    assert cfg.source == "hint"
    assert cfg.strategy == "layered_architecture"
    assert cfg.engine in SUPPORTED_ENGINES


def test_from_subtype_rule_sentinel_uses_default():
    cfg = resolve_layout_config(_model("from_subtype_rule"))
    assert cfg.engine == DEFAULT_ENGINE
    assert cfg.strategy == DEFAULT_STRATEGY


# --- Absent hint: documented default, never silent --------------------------
def test_absent_hint_uses_documented_default_with_diagnostic():
    cfg = resolve_layout_config(_model(None))
    assert cfg.engine == DEFAULT_ENGINE
    assert cfg.strategy == DEFAULT_STRATEGY
    assert cfg.source == "default"
    assert any("no usable layout hint" in d for d in cfg.diagnostics)


def test_empty_hint_string_treated_as_absent():
    cfg = resolve_layout_config(_model("   "))
    assert cfg.source == "default"


def test_non_dict_renderer_hints_ignored():
    m = _model(None)
    m["rendererHints"] = "oops-not-a-dict"
    cfg = resolve_layout_config(m)
    assert cfg.source == "default"


# --- Unsupported hint: default + explicit diagnostic (never silent) ---------
def test_unknown_hint_falls_back_with_explicit_diagnostic():
    cfg = resolve_layout_config(_model("some_future_strategy"))
    assert cfg.engine == DEFAULT_ENGINE
    assert cfg.source == "default"
    assert any("unsupported layout hint" in d for d in cfg.diagnostics)
    assert cfg.preferred_layout == "some_future_strategy"  # raw hint retained


# --- Caller override precedence ---------------------------------------------
def test_valid_override_wins_over_hint():
    cfg = resolve_layout_config(_model("multi_az_columns"), requested_engine="grid")
    assert cfg.engine == "grid"
    assert cfg.source == "override"
    # The override diagnostic notes the hint it superseded.
    assert any("multi_az_columns" in d for d in cfg.diagnostics)


def test_unsupported_override_is_ignored_then_hint_applies():
    cfg = resolve_layout_config(
        _model("multi_az_columns"), requested_engine="bogus-engine"
    )
    # Bogus override ignored -> hint still drives the choice.
    assert cfg.engine == "stack-abs"
    assert cfg.source == "hint"
    assert any("unsupported requested engine" in d for d in cfg.diagnostics)


def test_unsupported_override_with_no_hint_uses_default():
    cfg = resolve_layout_config(_model(None), requested_engine="bogus-engine")
    assert cfg.engine == DEFAULT_ENGINE
    assert cfg.source == "default"
    assert any("unsupported requested engine" in d for d in cfg.diagnostics)


# --- Every mapped engine is actually implemented ----------------------------
def test_all_strategy_engines_are_supported():
    from app.policy import _STRATEGY_TABLE

    for spec in _STRATEGY_TABLE.values():
        assert spec.engine in SUPPORTED_ENGINES, spec
        for fb in spec.fallbacks:
            assert fb in SUPPORTED_ENGINES, spec
