"""Reusable band-based layout engine (Phase C + D).

Consumes a validated layout PLAN (see planner.build_layout_plan) and the
architecture model, and produces layout-model geometry for the band strategies:

  * bandKind == "lane"   -> ordered_lanes: bands are VERTICAL columns placed
                            left-to-right (flow / swimlane diagrams).
  * bandKind == "column" -> column_compare: same as lanes but ordered by the
                            policy `columns` hint (e.g. current/transition/
                            target modernization views).
  * bandKind == "layer"  -> banded_tiers: bands are HORIZONTAL rows placed
                            top-to-bottom (layered / tiered diagrams).
  * bandKind == "group"  -> grouped_grid: labeled group boxes arranged in a
                            grid of containers; each group packs its members
                            (capability maps, microservice domains).
  * bandKind == "hub"    -> central_hub: a central element with its periphery
                            arranged around it (RAG, agentic, context, network).

One shared set of primitives drives all of these: a 1-D band tiler (lane/column/
layer), a 2-D container-grid (group), and a hub placement (hub). This is
deliberately NOT a per-policy engine - every strategy in a category reuses the
same code, driven entirely by the plan's band order + assignments.

Geometry guarantees (so the validator's gates hold by construction):
  * bands/groups tile or grid the slide safe area with fixed gaps, so siblings
    never overlap and stay in-bounds;
  * each band's assigned leaves are grid-packed inside the band's inner box with
    gaps, so leaves never overlap and sit within their band (containment);
  * a readability warning is emitted when leaf icons pack below a minimum.

Connector routing reuses routing.route_edges (orthogonal, shared with the other
engines), so Agent 3 is unaffected.
"""
from __future__ import annotations

import math

from .icons import icon_key
from .routing import route_edges

# --- Geometry constants (inches) -------------------------------------------
TITLE_STRIP_IN = 0.55      # reserved at top for the slide title
LEGEND_STRIP_IN = 0.45     # reserved at bottom for the legend
BAND_GAP = 0.30            # gap between sibling bands
BAND_TITLE_H = 0.40        # band title strip
BAND_PAD = 0.22            # inner padding inside a band
LEAF_W = 1.35
LEAF_H = 1.15
LEAF_GAP = 0.28
MIN_LEAF_W = 0.5           # readability floor after fit


def _leaves_by_band(model: dict, plan: dict) -> dict[str, list[dict]]:
    """Group the model's leaf nodes by their assigned bandId, preserving band
    order and intra-band model order (stable + deterministic)."""
    by_id = {n["id"]: n for n in model.get("nodes", [])}
    buckets: dict[str, list[dict]] = {b["bandId"]: [] for b in plan.get("bands", [])}
    # Rank hint (if any) then model order.
    order_index = {n["id"]: i for i, n in enumerate(model.get("nodes", []))}
    assigns = sorted(
        plan.get("assignments", []),
        key=lambda a: (a.get("rank") if a.get("rank") is not None else 10**9,
                       order_index.get(a["nodeId"], 10**9)),
    )
    for a in assigns:
        node = by_id.get(a["nodeId"])
        if node is None:
            continue
        buckets.setdefault(a["bandId"], []).append(node)
    return buckets


def _grid_dims(n: int, wide: bool) -> tuple[int, int]:
    """Columns x rows for n leaves.

    A layer band is WIDE and SHORT, so its leaves prefer a single row (many
    columns); only when the row would exceed ~8 cells do we wrap to a 2nd row.
    A lane band is NARROW and TALL, so its leaves prefer a single column (many
    rows); only when the column exceeds ~8 cells do we add a 2nd column. This
    keeps dense bands from overflowing their short/narrow extent.
    """
    if n <= 0:
        return 1, 1
    cap = 8
    if wide:  # layer: fill the row first
        rows = max(1, math.ceil(n / cap))
        cols = math.ceil(n / rows)
    else:     # lane: fill the column first
        cols = max(1, math.ceil(n / cap))
        rows = math.ceil(n / cols)
    return cols, rows


def _pack_band_leaves(
    leaves: list[dict],
    bx: float,
    by: float,
    bw: float,
    bh: float,
    wide: bool,
    band_id: str,
) -> list[dict]:
    """Grid-pack leaves inside a band's inner rectangle (bx,by,bw,bh). Returns
    layout objects. Leaves are centered; sizes clamp to fit with gaps so they
    never overflow the band (containment) nor overlap siblings."""
    objs: list[dict] = []
    n = len(leaves)
    if n == 0:
        return objs
    cols, rows = _grid_dims(n, wide)

    # Cell size that STRICTLY fits the band inner box with gaps, capped at the
    # natural leaf size so small bands don't blow icons up. No lower floor is
    # imposed here: cells shrink to guarantee containment (a readability warning
    # is emitted by the caller when they get small). A tiny epsilon keeps the
    # packed grid inside the inner box after rounding.
    eps = 0.01
    cell_w = min(LEAF_W, (bw - (cols - 1) * LEAF_GAP) / cols - eps)
    cell_h = min(LEAF_H, (bh - (rows - 1) * LEAF_GAP) / rows - eps)
    cell_w = max(cell_w, 0.05)
    cell_h = max(cell_h, 0.05)

    grid_w = cols * cell_w + (cols - 1) * LEAF_GAP
    grid_h = rows * cell_h + (rows - 1) * LEAF_GAP
    ox = bx + max(0.0, (bw - grid_w) / 2)
    oy = by + max(0.0, (bh - grid_h) / 2)

    for i, node in enumerate(leaves):
        r, c = divmod(i, cols)
        x = ox + c * (cell_w + LEAF_GAP)
        y = oy + r * (cell_h + LEAF_GAP)
        objs.append({
            "objectId": node["id"],
            "kind": "ICON_LABEL",
            "x": round(x, 3), "y": round(y, 3),
            "w": round(cell_w, 3), "h": round(cell_h, 3),
            "z": 10,
            "parentObjectId": band_id,  # leaf is contained by its band
            "iconKey": icon_key(
                node.get("type", "COMPONENT"),
                (node.get("properties") or {}).get("awsService"),
            ),
            "label": node.get("label", node["id"]),
            "nodeType": node.get("type", "COMPONENT"),
        })
    return objs


SUPPORTED_BAND_KINDS = ("lane", "column", "layer", "group", "hub")


def build_layout_from_plan(model: dict, plan: dict, slide: dict) -> dict:
    """Produce a layout-model dict from a band plan.

    Dispatches on plan['bandKind']:
      lane / column -> vertical bands, left-to-right
      layer         -> horizontal bands, top-to-bottom
      group         -> grid of labeled group boxes
      hub           -> central hub + periphery
    Raises ValueError for unsupported band kinds (e.g. 'containment'/'none',
    which the caller routes to other engines).
    """
    kind = plan.get("bandKind")
    if kind not in SUPPORTED_BAND_KINDS:
        raise ValueError(
            f"band_engine handles {SUPPORTED_BAND_KINDS}, got '{kind}'"
        )

    margin = slide["safeMarginIn"]
    area = (
        margin,                                               # x
        margin + TITLE_STRIP_IN,                              # y
        slide["widthIn"] - 2 * margin,                        # w
        slide["heightIn"] - 2 * margin - TITLE_STRIP_IN - LEGEND_STRIP_IN,  # h
    )

    bands = sorted(plan.get("bands", []), key=lambda b: b["order"])
    leaves_by_band = _leaves_by_band(model, plan)

    if kind in ("lane", "column"):
        objects = _build_vertical_bands(bands, leaves_by_band, area)
    elif kind == "layer":
        objects = _build_horizontal_bands(bands, leaves_by_band, area)
    elif kind == "group":
        objects = _build_group_grid(bands, leaves_by_band, area)
    else:  # hub
        objects = _build_hub(bands, leaves_by_band, area)

    warnings: list[str] = []
    min_leaf = min((o["w"] for o in objects if o["kind"] == "ICON_LABEL"), default=LEAF_W)
    if min_leaf < MIN_LEAF_W:
        warnings.append(
            f"leaf icons pack to {min_leaf:.2f}in wide (< {MIN_LEAF_W}in); "
            f"diagram is dense for a single 16:9 slide"
        )

    # Connectors (orthogonal, shared router). Edges whose endpoints were not
    # placed (e.g. container-only ids) are skipped by the router.
    obj_by_id = {o["objectId"]: o for o in objects}
    max_z = max((o["z"] for o in objects), default=0)
    connectors = route_edges(model.get("edges", []), obj_by_id, z=max_z + 1)

    layout = {
        "schemaVersion": "1.0",
        "projectId": model.get("projectId", ""),
        "jobId": model.get("jobId", ""),
        "title": model.get("title", ""),
        "slide": slide,
        "objects": objects,
        "connectors": connectors,
    }
    layout["_warnings"] = warnings
    return layout


def _build_vertical_bands(bands, leaves_by_band, area) -> list[dict]:
    """lane / column: vertical bands tiled left-to-right, each full height."""
    area_x, area_y, area_w, area_h = area
    n = max(1, len(bands))
    band_w = (area_w - (n - 1) * BAND_GAP) / n
    objects: list[dict] = []
    for i, band in enumerate(bands):
        bx = area_x + i * (band_w + BAND_GAP)
        objects.append(_band_container(band, bx, area_y, band_w, area_h))
        inner = _inner_box(bx, area_y, band_w, area_h)
        objects.extend(
            _pack_band_leaves(
                leaves_by_band.get(band["bandId"], []), *inner,
                wide=False, band_id=band["bandId"],
            )
        )
    return objects


def _build_horizontal_bands(bands, leaves_by_band, area) -> list[dict]:
    """layer: horizontal bands tiled top-to-bottom, each full width."""
    area_x, area_y, area_w, area_h = area
    n = max(1, len(bands))
    band_h = (area_h - (n - 1) * BAND_GAP) / n
    objects: list[dict] = []
    for i, band in enumerate(bands):
        by = area_y + i * (band_h + BAND_GAP)
        objects.append(_band_container(band, area_x, by, area_w, band_h))
        inner = _inner_box(area_x, by, area_w, band_h)
        objects.extend(
            _pack_band_leaves(
                leaves_by_band.get(band["bandId"], []), *inner,
                wide=True, band_id=band["bandId"],
            )
        )
    return objects


def _build_group_grid(bands, leaves_by_band, area) -> list[dict]:
    """group: labeled group boxes arranged in a grid of containers.

    Groups tile the area in a roughly-square grid (favoring the wide canvas),
    each cell equal-sized so peer groups read uniformly. Members pack inside.
    """
    area_x, area_y, area_w, area_h = area
    n = max(1, len(bands))
    cols = max(1, math.ceil(math.sqrt(n * 1.6)))  # bias wide for 16:9
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    cell_w = (area_w - (cols - 1) * BAND_GAP) / cols
    cell_h = (area_h - (rows - 1) * BAND_GAP) / rows
    objects: list[dict] = []
    for i, band in enumerate(bands):
        r, c = divmod(i, cols)
        bx = area_x + c * (cell_w + BAND_GAP)
        by = area_y + r * (cell_h + BAND_GAP)
        objects.append(_band_container(band, bx, by, cell_w, cell_h))
        inner = _inner_box(bx, by, cell_w, cell_h)
        objects.extend(
            _pack_band_leaves(
                leaves_by_band.get(band["bandId"], []), *inner,
                wide=True, band_id=band["bandId"],
            )
        )
    return objects


def _build_hub(bands, leaves_by_band, area) -> list[dict]:
    """hub: the 'hub' band occupies a central column; the 'spoke' band wraps
    its members in a surrounding group box on each side.

    Deterministic + overlap-free: the area is split into left-spoke | hub |
    right-spoke thirds. Spoke members are split evenly between the two side
    bands; the hub band holds the central element(s). If there is no spoke band
    (or no hub band), it degrades to vertical bands.
    """
    area_x, area_y, area_w, area_h = area
    hub_band = next((b for b in bands if b.get("role") == "hub"), None)
    spoke_band = next((b for b in bands if b.get("role") == "spoke"), None)
    if hub_band is None or spoke_band is None:
        return _build_vertical_bands(bands, leaves_by_band, area)

    spokes = leaves_by_band.get(spoke_band["bandId"], [])
    half = (len(spokes) + 1) // 2
    left_members, right_members = spokes[:half], spokes[half:]

    # Three equal columns: left spoke | hub | right spoke.
    col_w = (area_w - 2 * BAND_GAP) / 3
    left_x = area_x
    hub_x = area_x + col_w + BAND_GAP
    right_x = area_x + 2 * (col_w + BAND_GAP)

    objects: list[dict] = []

    # Left spoke box (reuse the spoke band container, left half of its members).
    left_box = dict(spoke_band)
    left_box["bandId"] = spoke_band["bandId"]
    objects.append(_band_container(spoke_band, left_x, area_y, col_w, area_h))
    objects.extend(
        _pack_band_leaves(left_members, *_inner_box(left_x, area_y, col_w, area_h),
                          wide=False, band_id=spoke_band["bandId"])
    )

    # Hub box (center).
    objects.append(_band_container(hub_band, hub_x, area_y, col_w, area_h))
    objects.extend(
        _pack_band_leaves(leaves_by_band.get(hub_band["bandId"], []),
                          *_inner_box(hub_x, area_y, col_w, area_h),
                          wide=False, band_id=hub_band["bandId"])
    )

    # Right spoke box: a SECOND container for the remaining spoke members. Give
    # it a distinct id so it never collides with the left spoke container.
    if right_members:
        right_id = f"{spoke_band['bandId']}-2"
        right_band = {"bandId": right_id, "label": spoke_band.get("label"),
                      "order": spoke_band["order"], "role": "spoke"}
        objects.append(_band_container(right_band, right_x, area_y, col_w, area_h))
        objects.extend(
            _pack_band_leaves(right_members, *_inner_box(right_x, area_y, col_w, area_h),
                              wide=False, band_id=right_id)
        )
    return objects


def _band_container(band: dict, x: float, y: float, w: float, h: float) -> dict:
    return {
        "objectId": band["bandId"],
        "kind": "CONTAINER",
        "x": round(x, 3), "y": round(y, 3),
        "w": round(w, 3), "h": round(h, 3),
        "z": 0,
        "parentObjectId": None,
        "iconKey": None,
        "label": band.get("label") or band["bandId"],
        "nodeType": "CONTAINER",
    }


def _inner_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    """The content rectangle inside a band (below its title, inset by padding)."""
    ix = x + BAND_PAD
    iy = y + BAND_TITLE_H + BAND_PAD
    iw = max(0.2, w - 2 * BAND_PAD)
    ih = max(0.2, h - BAND_TITLE_H - 2 * BAND_PAD)
    return ix, iy, iw, ih
