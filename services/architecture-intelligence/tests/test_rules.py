"""Tests for Agent 1 semantic rule-pack loading, inheritance, and resolution.

No AWS/network access: these exercise the pure YAML loader + resolver.
"""
from __future__ import annotations

import pathlib
import sys

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app import rules  # noqa: E402


def test_lists_known_packs_including_multi_az():
    ids = rules.list_rule_packs()
    assert "deployment.multi_az" in ids
    assert "application.layered" in ids
    # common.yaml is the shared base, not a selectable pack family item but it
    # does carry an id; it should still resolve.
    assert all("." in pid for pid in ids)


def test_resolve_type_accepts_exact_id():
    assert rules.resolve_type("deployment.multi_az") == "deployment.multi_az"


def test_resolve_type_accepts_legacy_enum():
    assert rules.resolve_type("DEPLOYMENT") == "deployment.multi_az"
    assert rules.resolve_type("APPLICATION") == "application.layered"


def test_resolve_type_accepts_slashed_and_suffix():
    assert rules.resolve_type("deployment/multi_az") == "deployment.multi_az"
    assert rules.resolve_type("multi_az") == "deployment.multi_az"


def test_resolve_type_unknown_falls_back_to_deployment_default():
    assert rules.resolve_type("totally_unknown") == "deployment.multi_az"
    assert rules.resolve_type("") == "deployment.multi_az"


def test_extends_inheritance_merges_parent_fields():
    # multi_az extends deployment/logical which extends common. The resolved
    # pack should carry fields contributed by ancestors (prohibited_behavior is
    # list-unioned) plus its own.
    pack = rules.get_rule_pack("deployment.multi_az")
    assert pack["rule_pack"]["id"] == "deployment.multi_az"
    prohibited = pack.get("prohibited_behavior", [])
    assert "place_global_service_inside_vpc" in prohibited  # from multi_az
    # renderer_hints is deep-merged; child's preferred_layout wins.
    assert pack["renderer_hints"]["preferred_layout"] == "multi_az_columns"


def test_preferred_layout_reads_renderer_hint():
    assert rules.preferred_layout("deployment.multi_az") == "multi_az_columns"
    assert rules.preferred_layout("application.layered") == "layered_architecture"
