"""Typed layout-policy resolver for Agent 2.

Phase 2 goal: let the architecture's semantic *intent* drive the layout engine,
instead of relying only on a caller-supplied `engine` parameter.

The intent travels on the architecture model as
``rendererHints.preferredLayout`` - a strategy id copied by Agent 1 from the
semantic rule pack's ``renderer_hints.preferred_layout`` (e.g.
``multi_az_columns``). This module maps that strategy id to a normalized,
typed :class:`LayoutConfig` describing which engine to use and the ordered
fallback engines.

Design rules (from the improvement plan):
  * SINGLE SOURCE OF TRUTH: the hint is read from the model that Agent 2 already
    loads; this service does NOT re-parse the semantic YAML files, so there is
    no second, independently-edited policy copy.
  * NEVER SILENT: an unknown/absent/invalid strategy does not quietly pick an
    engine. It falls back to a *documented* default and records a human-readable
    reason in the returned config's ``diagnostics``.
  * TYPED + VALIDATED: only engines this service actually implements may be
    selected; the strategy table is validated against ``SUPPORTED_ENGINES``.
  * CALLER OVERRIDE: if a caller explicitly forces an engine, that wins over the
    resolved strategy (the override is still recorded in diagnostics).

Supported engines (must match services/layout/app/agent.py::_compute_layout):
    stack | stack-abs | graphviz | grid
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Engines this service can actually run (keep in sync with agent._compute_layout).
SUPPORTED_ENGINES: tuple[str, ...] = (
    "stack", "stack-abs", "stack-region", "graphviz", "grid",
)

# Documented defaults used when no usable hint is present.
DEFAULT_ENGINE = "stack"
DEFAULT_STRATEGY = "generic_stack"
DEFAULT_FALLBACKS: tuple[str, ...] = ("stack", "graphviz", "grid")


@dataclass
class StrategySpec:
    """A supported layout strategy and the engines that realize it."""

    strategy: str
    engine: str
    fallbacks: tuple[str, ...]


# Strategy id (from a rule pack's renderer_hints.preferred_layout) -> engine
# mapping. Every strategy declared by a shipped rule pack is listed so an
# authored hint is always recognised. Strategies that have no bespoke engine yet
# map to the generic stacker (an honest, documented default) rather than being
# treated as "unsupported".
#
# Multi-AZ is the Phase 2 reference: it maps to the absolute-swimlane engine,
# which aligns subnet tiers across AZ columns.
_STRATEGY_TABLE: dict[str, StrategySpec] = {
    # --- Deployment -------------------------------------------------------
    # multi_az: ONE region + multiple AZs -> stack-abs locks subnet tiers to
    # absolute swimlanes aligned across AZ columns.
    "multi_az_columns": StrategySpec(
        "multi_az_columns", "stack-abs", ("stack", "graphviz", "grid")
    ),
    # multi_region: N REGION subtrees placed as side-by-side non-overlapping
    # columns by the region-mirror post-pass (stack-region). Each region keeps
    # its internal VPC/AZ/subnet containment; the ACCOUNT wrapper is sized to
    # enclose both so the validator's auto_correct never grows one region into
    # its neighbour (the defect that broke the naive stack/stack-abs mappings).
    "multi_region_mirror": StrategySpec(
        "multi_region_mirror", "stack-region", ("stack", "graphviz", "grid")
    ),
    # --- Application / business / data / genai / etc. ---------------------
    # No bespoke engine yet (future phases); use the generic stacker so intent
    # is recognised and recorded rather than silently defaulted.
    "layered_architecture": StrategySpec(
        "layered_architecture", "stack", ("graphviz", "grid")
    ),
    "domain_grouped_layers": StrategySpec(
        "domain_grouped_layers", "stack", ("graphviz", "grid")
    ),
    "hub_and_spoke": StrategySpec("hub_and_spoke", "graphviz", ("stack", "grid")),
    "left_to_right_flow": StrategySpec(
        "left_to_right_flow", "graphviz", ("stack", "grid")
    ),
    "swimlane": StrategySpec("swimlane", "graphviz", ("stack", "grid")),
    "capability_map": StrategySpec("capability_map", "stack", ("graphviz", "grid")),
    "central_platform": StrategySpec(
        "central_platform", "graphviz", ("stack", "grid")
    ),
    "current_to_target": StrategySpec(
        "current_to_target", "stack", ("graphviz", "grid")
    ),
    "layered_controls": StrategySpec(
        "layered_controls", "stack", ("graphviz", "grid")
    ),
    # Sentinel declared by common.yaml: means "defer to the subtype pack". When
    # it reaches here, no concrete subtype hint was present -> documented default.
    "from_subtype_rule": StrategySpec(
        DEFAULT_STRATEGY, DEFAULT_ENGINE, DEFAULT_FALLBACKS
    ),
}


@dataclass
class RendererHints:
    """Typed, normalized view of the model's advisory rendererHints block.

    Mirrors the architecture contract's rendererHints fields. Lists default to
    empty; scalars to None. Built defensively from an arbitrary model dict so a
    malformed or absent block yields an empty-but-valid instance (never raises).
    """

    preferred_layout: str | None = None
    alternate_layout: str | None = None
    lane_order: list[str] = field(default_factory=list)
    layer_order: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    emphasis: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.preferred_layout
            or self.alternate_layout
            or self.lane_order
            or self.layer_order
            or self.columns
            or self.emphasis
        )


def _clean_str(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_str_list(value) -> list[str]:
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def read_renderer_hints(model: dict) -> RendererHints:
    """Normalize model['rendererHints'] into a typed RendererHints (defensive)."""
    raw = model.get("rendererHints")
    if not isinstance(raw, dict):
        return RendererHints()
    return RendererHints(
        preferred_layout=_clean_str(raw.get("preferredLayout")),
        alternate_layout=_clean_str(raw.get("alternateLayout")),
        lane_order=_clean_str_list(raw.get("laneOrder")),
        layer_order=_clean_str_list(raw.get("layerOrder")),
        columns=_clean_str_list(raw.get("columns")),
        emphasis=_clean_str_list(raw.get("emphasis")),
    )


@dataclass
class LayoutConfig:
    """Normalized, typed layout configuration selected for a job."""

    strategy: str
    engine: str
    fallbacks: tuple[str, ...]
    source: str  # how the engine was chosen: 'override' | 'hint' | 'alternate' | 'default'
    preferred_layout: str | None = None  # the raw hint seen on the model (if any)
    hints: RendererHints = field(default_factory=RendererHints)
    diagnostics: list[str] = field(default_factory=list)


def _read_hint(model: dict) -> str | None:
    """Return rendererHints.preferredLayout from the model, if present/typed."""
    return read_renderer_hints(model).preferred_layout


def resolve_layout_config(
    model: dict, requested_engine: str | None = None
) -> LayoutConfig:
    """Resolve the layout strategy/engine for an architecture model.

    Priority:
      1. ``requested_engine`` - an explicit caller override (must be supported).
      2. ``rendererHints.preferredLayout`` on the model -> strategy table.
      3. Documented default (generic stacker).

    An unsupported override or an unknown hint is never applied silently: it is
    recorded in ``diagnostics`` and resolution continues to the next source.
    """
    diagnostics: list[str] = []
    hints = read_renderer_hints(model)
    preferred = hints.preferred_layout

    # 1. Explicit caller override wins (if it is a real engine).
    if requested_engine:
        if requested_engine in SUPPORTED_ENGINES:
            diag = f"engine '{requested_engine}' selected by caller override"
            if preferred:
                diag += f" (model hint was '{preferred}')"
            diagnostics.append(diag)
            return LayoutConfig(
                strategy=f"override:{requested_engine}",
                engine=requested_engine,
                fallbacks=tuple(e for e in DEFAULT_FALLBACKS if e != requested_engine),
                source="override",
                preferred_layout=preferred,
                hints=hints,
                diagnostics=diagnostics,
            )
        diagnostics.append(
            f"ignored unsupported requested engine '{requested_engine}' "
            f"(supported: {', '.join(SUPPORTED_ENGINES)})"
        )

    # 2. Hint-driven strategy (preferredLayout).
    if preferred:
        spec = _STRATEGY_TABLE.get(preferred)
        if spec is not None:
            diagnostics.append(
                f"strategy '{spec.strategy}' -> engine '{spec.engine}' "
                f"from model hint preferredLayout='{preferred}'"
            )
            return LayoutConfig(
                strategy=spec.strategy,
                engine=spec.engine,
                fallbacks=spec.fallbacks,
                source="hint",
                preferred_layout=preferred,
                hints=hints,
                diagnostics=diagnostics,
            )
        diagnostics.append(
            f"unsupported layout hint '{preferred}' - no strategy mapping"
        )

    # 2b. Alternate strategy (alternateLayout) before the generic default.
    if hints.alternate_layout:
        alt_spec = _STRATEGY_TABLE.get(hints.alternate_layout)
        if alt_spec is not None:
            diagnostics.append(
                f"strategy '{alt_spec.strategy}' -> engine '{alt_spec.engine}' "
                f"from model hint alternateLayout='{hints.alternate_layout}'"
            )
            return LayoutConfig(
                strategy=alt_spec.strategy,
                engine=alt_spec.engine,
                fallbacks=alt_spec.fallbacks,
                source="alternate",
                preferred_layout=preferred,
                hints=hints,
                diagnostics=diagnostics,
            )
        diagnostics.append(
            f"unsupported alternateLayout '{hints.alternate_layout}' - "
            f"no strategy mapping"
        )

    if not preferred and not hints.alternate_layout:
        diagnostics.append("no usable layout hint on model")
    diagnostics.append(f"using documented default engine '{DEFAULT_ENGINE}'")

    # 3. Documented default.
    return LayoutConfig(
        strategy=DEFAULT_STRATEGY,
        engine=DEFAULT_ENGINE,
        fallbacks=DEFAULT_FALLBACKS,
        source="default",
        preferred_layout=preferred,
        hints=hints,
        diagnostics=diagnostics,
    )
