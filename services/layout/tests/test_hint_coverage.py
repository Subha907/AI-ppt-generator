"""Cross-service consistency + typed-hints tests.

1. Every preferred_layout / alternate_layout declared by a shipped Agent 1 rule
   pack must be recognised by Agent 2's policy resolver (never silently
   unsupported).
2. The typed RendererHints normalizer handles well-formed and malformed input.
Scans the YAML files directly (no cross-service Python import).
"""
from __future__ import annotations

import pathlib
import sys

import yaml

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.policy import (  # noqa: E402
    _STRATEGY_TABLE,
    read_renderer_hints,
    resolve_layout_config,
)

# services/layout/tests -> services -> architecture-intelligence rules.
_SEMANTIC_DIR = (
    _SERVICE_ROOT.parent / "architecture-intelligence" / "rules" / "semantic"
)


def _declared_layouts() -> set[str]:
    """All preferred_layout AND alternate_layout values across every pack."""
    found: set[str] = set()
    for path in _SEMANTIC_DIR.rglob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        hints = data.get("renderer_hints") or {}
        for key in ("preferred_layout", "alternate_layout"):
            val = hints.get(key)
            if isinstance(val, str) and val.strip():
                found.add(val.strip())
    return found


# --- Policy coverage --------------------------------------------------------
def test_semantic_dir_exists():
    assert _SEMANTIC_DIR.is_dir(), _SEMANTIC_DIR


def test_every_declared_layout_is_recognised_by_resolver():
    declared = _declared_layouts()
    assert declared, "no layout hints found - scan path wrong?"
    unknown = sorted(h for h in declared if h not in _STRATEGY_TABLE)
    assert not unknown, (
        f"these rule-pack layout values are not in the Agent 2 strategy table "
        f"and would hit the silently-unsupported default path: {unknown}"
    )


def test_every_declared_preferred_layout_resolves_as_hint():
    for path in _SEMANTIC_DIR.rglob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pl = (data.get("renderer_hints") or {}).get("preferred_layout")
        if not (isinstance(pl, str) and pl.strip()):
            continue
        cfg = resolve_layout_config({"rendererHints": {"preferredLayout": pl}})
        assert cfg.source == "hint", (pl, cfg.diagnostics)
        assert not any(
            "unsupported layout hint" in d for d in cfg.diagnostics
        ), (pl, cfg.diagnostics)


# --- Typed normalizer -------------------------------------------------------
def test_read_renderer_hints_full():
    model = {
        "rendererHints": {
            "preferredLayout": "left_to_right_flow",
            "alternateLayout": "multi_az_columns",
            "laneOrder": ["sources", "ingestion", "storage"],
            "layerOrder": [],
            "columns": ["current", "target"],
            "emphasis": ["ingress", "egress"],
        }
    }
    h = read_renderer_hints(model)
    assert h.preferred_layout == "left_to_right_flow"
    assert h.alternate_layout == "multi_az_columns"
    assert h.lane_order == ["sources", "ingestion", "storage"]
    assert h.columns == ["current", "target"]
    assert h.emphasis == ["ingress", "egress"]
    assert not h.is_empty


def test_read_renderer_hints_absent_is_empty():
    assert read_renderer_hints({}).is_empty
    assert read_renderer_hints({"rendererHints": "bad"}).is_empty


def test_read_renderer_hints_filters_non_strings():
    model = {"rendererHints": {"laneOrder": ["ok", 3, None, "   ", "two"]}}
    assert read_renderer_hints(model).lane_order == ["ok", "two"]


# --- alternateLayout resolution path ---------------------------------------
def test_alternate_layout_used_when_preferred_unknown():
    model = {
        "rendererHints": {
            "preferredLayout": "unknown_future_strategy",
            "alternateLayout": "multi_az_columns",
        }
    }
    cfg = resolve_layout_config(model)
    assert cfg.engine == "stack-abs"
    assert cfg.source == "alternate"
    assert any("unsupported layout hint" in d for d in cfg.diagnostics)
