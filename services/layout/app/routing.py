"""Deterministic orthogonal edge routing between placed boxes.

Given the final object rectangles, route each edge as a right-angle (orthogonal)
polyline: exit the source on the side facing the target, enter the target on its
facing side, with a single mid-bend. Parallel edges leaving the same object are
given small lane offsets so they do not perfectly overlap.

Grounded in AWS diagram convention: orthogonal links (right-angle connectors)
read cleaner than diagonal lines.
"""
from __future__ import annotations

from collections import defaultdict


def _center(o: dict) -> tuple[float, float]:
    return o["x"] + o["w"] / 2, o["y"] + o["h"] / 2


# Must mirror the compiler's leaf icon sizing: a 0.5in square at the box top,
# capped by the box width and 60% of its height. Connectors to/from a leaf
# should touch this icon rectangle, not the full (label-inclusive) box.
_ICON_SIZE_IN = 0.5

# Arrowhead back-off: pull the final point back from the target border by this
# much (inches) along the approach axis so the arrowhead GRAPHIC (not just the
# line tip) sits against the border instead of overshooting/clipping over it.
_ARROW_BACKOFF = 0.08


def _apply_backoff(points: list[dict]) -> list[dict]:
    """Shorten the last segment by _ARROW_BACKOFF so the arrowhead tip lands
    cleanly on the target's edge. Moves points[-1] back toward points[-2] along
    whichever axis the final segment runs (segments are orthogonal, so exactly
    one axis differs)."""
    if len(points) < 2:
        return points
    p_last, p_prev = points[-1], points[-2]
    dx = p_last["x"] - p_prev["x"]
    dy = p_last["y"] - p_prev["y"]
    if abs(dx) >= abs(dy) and dx != 0:
        step = _ARROW_BACKOFF if dx > 0 else -_ARROW_BACKOFF
        # Don't overshoot past the segment itself.
        if abs(dx) > _ARROW_BACKOFF:
            p_last["x"] = round(p_last["x"] - step, 3)
    elif dy != 0:
        step = _ARROW_BACKOFF if dy > 0 else -_ARROW_BACKOFF
        if abs(dy) > _ARROW_BACKOFF:
            p_last["y"] = round(p_last["y"] - step, 3)
    return points


def _icon_bottom(o: dict) -> float:
    """Y coordinate of the bottom edge of a leaf's icon (box top + icon height).

    For containers (no icon) this is just the box bottom, so behavior is
    unchanged for structural boxes.
    """
    if o.get("kind") == "ICON_LABEL":
        icon_h = min(_ICON_SIZE_IN, o["w"], o["h"] * 0.6)
        return o["y"] + icon_h
    return o["y"] + o["h"]


def _orthogonal_route(a: dict, b: dict, lane: float) -> list[dict]:
    """A 2-bend orthogonal route between rects a and b.

    Connectors anchor on the BORDER-MIDPOINT of the facing side of each box
    (Ruleset section 7 / remediation Issue 3) - never the geometric center - so
    arrowheads terminate at the shape edge and do not pierce the icon or its
    label. Chooses horizontal-first or vertical-first by dominant axis, and
    applies a small `lane` offset to the mid corridor so parallel edges separate.
    """
    ax, ay = _center(a)
    bx, by = _center(b)
    dx = bx - ax
    dy = by - ay

    if abs(dx) >= abs(dy):
        # Horizontal dominant: exit the right/left FACE, enter the target's
        # opposite face. Anchor Y at each box's vertical mid (edge midpoint).
        sx = a["x"] + a["w"] if dx >= 0 else a["x"]
        tx = b["x"] if dx >= 0 else b["x"] + b["w"]
        sy = ay + lane
        ty = by + lane
        midx = (sx + tx) / 2 + lane
        return _apply_backoff([
            {"x": round(sx, 3), "y": round(sy, 3)},
            {"x": round(midx, 3), "y": round(sy, 3)},
            {"x": round(midx, 3), "y": round(ty, 3)},
            {"x": round(tx, 3), "y": round(ty, 3)},
        ])
    else:
        # Vertical dominant: exit the bottom/top of the source and enter the
        # top/bottom of the target. Defect 2 fix: a leaf box is taller than its
        # 0.5in icon (it reserves label space BELOW the icon). Anchoring to the
        # full box bottom makes the line pass up through the source's own label;
        # instead anchor to the ICON extent (top 0.5in of a leaf box) so the
        # line touches the icon border cleanly and never crosses the caption.
        s_icon_bottom = _icon_bottom(a)
        t_icon_bottom = _icon_bottom(b)
        if dy >= 0:
            # target is below source: leave source icon bottom, enter target top.
            sy = s_icon_bottom
            ty = b["y"]
        else:
            # target is above source: leave source top, enter target icon bottom.
            sy = a["y"]
            ty = t_icon_bottom
        sx = ax + lane
        tx = bx + lane
        midy = (sy + ty) / 2 + lane
        return _apply_backoff([
            {"x": round(sx, 3), "y": round(sy, 3)},
            {"x": round(sx, 3), "y": round(midy, 3)},
            {"x": round(tx, 3), "y": round(midy, 3)},
            {"x": round(tx, 3), "y": round(ty, 3)},
        ])


# Ruleset section 7: functional connector classes. Each maps to a style the
# compiler renders (color/width/dash). Classification is by edge label keywords
# and/or the source/target node types.
_REPLICATION_KW = ("replicat", "sync", "failover", "standby", "mirror")
_SECURITY_KW = (
    "encrypt", "kms", "iam", "auth", "token", "secret", "certificate", "tls",
    "grants", "role", "permission",
)


def classify_edge(label: str | None, src_type: str, tgt_type: str) -> str:
    """Return the functional class: 'replication' | 'security' | 'request'."""
    text = (label or "").lower()
    if any(k in text for k in _REPLICATION_KW):
        return "replication"
    if any(k in text for k in _SECURITY_KW):
        return "security"
    # Security services as the source usually indicate an authz/encryption edge.
    if src_type in ("SECURITY",):
        return "security"
    if src_type in ("DATABASE", "DATASTORE") and tgt_type in ("DATABASE", "DATASTORE"):
        return "replication"
    return "request"


# Remediation Issue 2: the progressive channel offset must be large enough to
# clear PowerPoint line-stroke widths (1-1.5pt) - 0.05in collapsed parallel
# lines into one unreadable string. 0.12in gives each parallel path its own
# visible track.
_CHANNEL_OFFSET = 0.12


def _side_of_gap(o: dict, gap_x0: float, gap_x1: float) -> int:
    """-1 if the box is fully left of the corridor gap, +1 if fully right, 0 if
    it straddles/inside (treated as not a cross-column edge)."""
    cx = o["x"] + o["w"] / 2
    if o["x"] + o["w"] <= gap_x0 + 0.01:
        return -1
    if o["x"] >= gap_x1 - 0.01:
        return 1
    return -1 if cx < (gap_x0 + gap_x1) / 2 else 1


def _corridor_route(
    a: dict, b: dict, corridor_x: float, tgt_entry_frac: float = 0.5
) -> list[dict]:
    """Route a cross-column edge through a shared vertical corridor.

    Exit the source's right (or left) face, run horizontally to `corridor_x`,
    travel vertically in the corridor, then run horizontally into the target's
    facing face. All cross-column edges use the SAME corridor band but distinct
    `corridor_x` tracks, so they read as neat parallel verticals.

    `tgt_entry_frac` (0..1) is WHERE along the target's facing edge this edge
    enters. When several edges target the same (often large) container, each
    gets a distinct fraction so they no longer stack on the container's center
    line (fixes the "bar-code" collision on IAM/Secrets/KMS -> subnet edges).
    """
    ay = a["y"] + a["h"] / 2
    # Distinct entry point spread across the target's facing edge, clamped to a
    # small inset so it stays on the border and not at the very corner.
    frac = min(0.85, max(0.15, tgt_entry_frac))
    by = b["y"] + b["h"] * frac
    # Source exits toward the corridor.
    sx = a["x"] + a["w"] if corridor_x >= a["x"] + a["w"] else a["x"]
    # Target is entered on its facing (perimeter) side.
    tx = b["x"] if corridor_x <= b["x"] else b["x"] + b["w"]
    return _apply_backoff([
        {"x": round(sx, 3), "y": round(ay, 3)},
        {"x": round(corridor_x, 3), "y": round(ay, 3)},
        {"x": round(corridor_x, 3), "y": round(by, 3)},
        {"x": round(tx, 3), "y": round(by, 3)},
    ])


def _find_corridor(obj_by_id: dict[str, dict]) -> tuple[float, float] | None:
    """Locate the clear vertical band between the VPC cluster and the group
    column, if the deployment structure is present. Returns (gap_x0, gap_x1)."""
    vpc = next((o for o in obj_by_id.values() if o.get("nodeType") == "VPC"), None)
    if not vpc:
        return None
    # Right-column group containers sit to the right of the VPC.
    groups = [
        o for o in obj_by_id.values()
        if o.get("nodeType") == "CONTAINER" and o["x"] > vpc["x"] + vpc["w"] - 0.5
    ]
    if not groups:
        return None
    gap_x0 = vpc["x"] + vpc["w"]
    gap_x1 = min(g["x"] for g in groups)
    if gap_x1 - gap_x0 < 0.2:
        return None  # not enough room for a corridor
    return gap_x0, gap_x1


def route_edges(edges: list[dict], obj_by_id: dict[str, dict], z: int) -> list[dict]:
    """Return connector dicts with orthogonal routed points + functional class.

    Two routing regimes:
      * CROSS-COLUMN edges (VPC side <-> group-column side) are routed through a
        shared vertical corridor in the gap between the two columns, each on its
        own vertical track. This eliminates the diagonal crossing bundle.
      * SAME-SIDE edges use the local 2-bend orthogonal route, with parallel
        edges separated by source fan-out + target fan-in lanes (0.12in each).
    """
    corridor = _find_corridor(obj_by_id)

    per_source: dict[str, int] = defaultdict(int)
    per_target: dict[str, int] = defaultdict(int)
    seen_src: dict[str, int] = defaultdict(int)
    seen_tgt: dict[str, int] = defaultdict(int)
    for e in edges:
        per_source[e.get("source")] += 1
        per_target[e.get("target")] += 1

    # Pre-identify cross-column edges so we can assign each a corridor track.
    cross_ids: list[str] = []
    if corridor:
        gx0, gx1 = corridor
        for e in edges:
            src = obj_by_id.get(e.get("source"))
            tgt = obj_by_id.get(e.get("target"))
            if not src or not tgt:
                continue
            if _side_of_gap(src, gx0, gx1) != _side_of_gap(tgt, gx0, gx1):
                cross_ids.append(e["id"])

    # Evenly space corridor tracks across the gap (leave a small margin).
    track_x: dict[str, float] = {}
    if corridor and cross_ids:
        gx0, gx1 = corridor
        m = 0.12
        lo, hi = gx0 + m, gx1 - m
        n = len(cross_ids)
        if n == 1:
            track_x[cross_ids[0]] = (lo + hi) / 2
        else:
            step = (hi - lo) / (n - 1)
            for i, eid in enumerate(cross_ids):
                track_x[eid] = lo + i * step

    # Defect 1 fix: when several corridor edges enter the SAME target container,
    # spread their entry points down that container's facing edge so they don't
    # stack into one thick bar. Assign each such edge a distinct fraction.
    entry_frac: dict[str, float] = {}
    cross_by_target: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e["id"] in track_x:
            cross_by_target[e.get("target")].append(e["id"])
    for tgt_id, eids in cross_by_target.items():
        k = len(eids)
        if k == 1:
            entry_frac[eids[0]] = 0.5
        else:
            # Even fractions across the middle 70% of the target edge.
            for j, eid in enumerate(eids):
                entry_frac[eid] = 0.15 + (0.70 * j / (k - 1))

    connectors = []
    for e in edges:
        src = obj_by_id.get(e.get("source"))
        tgt = obj_by_id.get(e.get("target"))
        if not src or not tgt:
            continue

        edge_class = classify_edge(
            e.get("label"), src.get("nodeType", ""), tgt.get("nodeType", "")
        )

        if e["id"] in track_x:
            # Cross-column: route through its assigned corridor track, entering
            # the target at its distinct fraction (Defect 1 anti-stacking).
            points = _corridor_route(
                src, tgt, track_x[e["id"]], entry_frac.get(e["id"], 0.5)
            )
        else:
            # Same-side: local 2-bend route with fan-out/fan-in lane separation.
            ns = per_source[e.get("source")]
            si = seen_src[e.get("source")]
            seen_src[e.get("source")] += 1
            src_lane = (si - (ns - 1) / 2) * _CHANNEL_OFFSET if ns > 1 else 0.0

            nt = per_target[e.get("target")]
            ti = seen_tgt[e.get("target")]
            seen_tgt[e.get("target")] += 1
            tgt_lane = (ti - (nt - 1) / 2) * _CHANNEL_OFFSET if nt > 1 else 0.0

            lane = src_lane + 0.5 * tgt_lane
            points = _orthogonal_route(src, tgt, lane)

        connectors.append({
            "edgeId": e["id"],
            "sourceObjectId": e["source"],
            "targetObjectId": e["target"],
            "route": "ORTHOGONAL",
            "edgeClass": edge_class,
            "z": z,
            "label": e.get("label"),
            "points": points,
        })
    return connectors
