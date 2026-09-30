"""Deterministic layout engine.

Turns an architecture topology (nodes with parentId + edges) into absolute
coordinates on a 16:9 slide, guaranteeing the geometry gates by construction:

  * Nested containers (Account > Region > VPC > AZ > subnet) are laid out
    recursively. Each container sizes itself to fit its children plus a title
    bar and padding, so children are always inside their parent.
  * Children of a container are arranged in a grid, spaced apart, so siblings
    never overlap.
  * The whole diagram is scaled to fit inside the slide's safe area.
  * z-order increases with depth, so leaf icons draw in front of the containers
    that hold them.
  * Connectors are routed orthogonally between the nearest anchor points of the
    source and target objects.

All units are inches (PowerPoint's native EMU maps cleanly from inches later).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .icons import CONTAINER_TYPES, icon_key

# --- Tunable geometry constants (inches) -----------------------------------
LEAF_W = 1.45
LEAF_H = 1.0
CHILD_GAP = 0.30          # gap between sibling boxes
CONTAINER_PAD = 0.26      # padding inside a container around its children
TITLE_BAR = 0.42          # space at top of a container for its label (taller
#                           so nested container titles never clip)

# Approx width per character of a 9pt title, used to guarantee a container is
# at least wide enough to show its own label without clipping.
TITLE_CHAR_W = 0.075
MIN_CONTAINER_W = 1.6     # containers never narrower than this
MIN_CONTAINER_H = 1.0

# Vertical space reserved at the top of the slide for the slide title, so the
# diagram never collides with it.
TITLE_STRIP_IN = 0.55

# Readability floor: after fitting to the slide, an ICON_LABEL leaf should not
# render smaller than this (PDF 11.2 "minimum readable label size").
MIN_LEAF_W_AFTER_SCALE = 0.5


@dataclass
class Box:
    node_id: str
    node_type: str
    label: str
    aws_service: str | None
    parent_id: str | None
    children: list["Box"] = field(default_factory=list)
    depth: int = 0
    # Assigned during layout (relative, then absolute):
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def is_container(self) -> bool:
        return self.node_type in CONTAINER_TYPES or bool(self.children)


def _build_forest(nodes: list[dict]) -> list[Box]:
    """Build the containment forest from parentId links."""
    by_id: dict[str, Box] = {}
    for n in nodes:
        by_id[n["id"]] = Box(
            node_id=n["id"],
            node_type=n.get("type", "COMPONENT"),
            label=n.get("label", n["id"]),
            aws_service=(n.get("properties") or {}).get("awsService"),
            parent_id=n.get("parentId"),
        )
    roots: list[Box] = []
    for box in by_id.values():
        parent = by_id.get(box.parent_id) if box.parent_id else None
        if parent and parent is not box:
            parent.children.append(box)
        else:
            roots.append(box)

    def set_depth(b: Box, d: int) -> None:
        b.depth = d
        for c in b.children:
            set_depth(c, d + 1)

    for r in roots:
        set_depth(r, 0)
    return roots


def _grid_dims(children: list[Box]) -> tuple[int, int]:
    """Choose columns x rows for children.

    Two goals:
      * Spread horizontally (favor more columns) so the diagram uses the wide
        16:9 canvas instead of stacking into a tall column.
      * When the children are themselves containers (e.g. AZs, subnets), lay
        them side-by-side in a single row so the structure reads clearly.
    """
    import math

    n = len(children)
    if n <= 0:
        return 1, 1

    all_containers = all(c.is_container for c in children)
    if all_containers and n <= 4:
        # AZs / subnets side by side in one row.
        return n, 1

    # Leaves: bias toward a wider-than-square grid (16:9-ish).
    cols = max(1, math.ceil(math.sqrt(n * 1.6)))
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    return cols, rows


def _rows_for(children: list[Box]) -> list[list[Box]]:
    """Group children into rows (row-major over the chosen grid)."""
    n = len(children)
    if n == 0:
        return []
    cols, _ = _grid_dims(children)
    rows: list[list[Box]] = []
    for i in range(0, n, cols):
        rows.append(children[i : i + cols])
    return rows


def _title_min_width(box: Box) -> float:
    """Minimum width so the container's own title text does not clip."""
    return len(box.label) * TITLE_CHAR_W + 2 * CONTAINER_PAD


def _equalize_siblings(children: list[Box]) -> None:
    """Make sibling CONTAINERS in the same layout uniform in size.

    AWS diagrams read best when peer containers (e.g. the AZs, or the subnets
    within an AZ) are the same size. We grow the smaller ones up to the largest
    peer so an AZ with fewer services still matches its sibling.
    """
    container_kids = [c for c in children if c.is_container]
    if len(container_kids) < 2:
        return
    max_w = max(c.w for c in container_kids)
    max_h = max(c.h for c in container_kids)
    for c in container_kids:
        c.w = max_w
        c.h = max_h


def _measure(box: Box) -> None:
    """Bottom-up: assign each box its intrinsic w/h (relative units).

    Children are packed into rows; each row's width is the sum of its child
    widths (plus gaps) and its height is that row's tallest child. The
    container width is the widest row; height is the sum of row heights.
    Sibling containers are equalized, and every container is at least large
    enough for its own title and the minimum size floor.
    """
    if not box.children:
        box.w, box.h = LEAF_W, LEAF_H
        return
    for c in box.children:
        _measure(c)

    # Peer containers (AZs, subnets) share a uniform size for a balanced look.
    _equalize_siblings(box.children)

    rows = _rows_for(box.children)
    inner_w = 0.0
    inner_h = 0.0
    for r, row in enumerate(rows):
        row_w = sum(c.w for c in row) + (len(row) - 1) * CHILD_GAP
        row_h = max(c.h for c in row)
        inner_w = max(inner_w, row_w)
        inner_h += row_h
        if r < len(rows) - 1:
            inner_h += CHILD_GAP

    box.w = max(
        inner_w + 2 * CONTAINER_PAD,
        _title_min_width(box),
        MIN_CONTAINER_W,
    )
    box.h = max(
        inner_h + 2 * CONTAINER_PAD + TITLE_BAR,
        MIN_CONTAINER_H,
    )


def _place_children(box: Box) -> None:
    """Top-down: given box.x/y set, place its children row by row.

    Each row is centered horizontally within the container's inner width so the
    content sits balanced rather than hugging the left edge.
    """
    if not box.children:
        return
    rows = _rows_for(box.children)
    inner_w = box.w - 2 * CONTAINER_PAD
    origin_x = box.x + CONTAINER_PAD
    cursor_y = box.y + TITLE_BAR + CONTAINER_PAD

    for row in rows:
        row_h = max(c.h for c in row)
        row_w = sum(c.w for c in row) + (len(row) - 1) * CHILD_GAP
        # Center this row within the inner width.
        cursor_x = origin_x + max(0.0, (inner_w - row_w) / 2)
        for c in row:
            c.x = cursor_x
            c.y = cursor_y + (row_h - c.h) / 2
            _place_children(c)
            cursor_x += c.w + CHILD_GAP
        cursor_y += row_h + CHILD_GAP


def _layout_roots(roots: list[Box]) -> tuple[float, float]:
    """Place root boxes left-to-right; return total (w, h) of the diagram."""
    _for_each_measure(roots)
    x = 0.0
    max_h = 0.0
    for r in roots:
        r.x = x
        r.y = 0.0
        _place_children(r)
        x += r.w + CHILD_GAP
        max_h = max(max_h, r.h)
    total_w = max(x - CHILD_GAP, LEAF_W)
    return total_w, max_h


def _for_each_measure(roots: list[Box]) -> None:
    for r in roots:
        _measure(r)


def _flatten(roots: list[Box]) -> list[Box]:
    out: list[Box] = []

    def walk(b: Box) -> None:
        out.append(b)
        for c in b.children:
            walk(c)

    for r in roots:
        walk(r)
    return out


def _anchor_points(a: dict, b: dict) -> list[dict]:
    """Orthogonal 3-segment route between the centers of two object rects."""
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    # Exit source on the side facing the target, enter target likewise.
    if abs(bx - ax) >= abs(by - ay):
        # Mostly horizontal: leave from left/right edges.
        sx = a["x"] + a["w"] if bx >= ax else a["x"]
        tx = b["x"] if bx >= ax else b["x"] + b["w"]
        midx = (sx + tx) / 2
        return [
            {"x": round(sx, 3), "y": round(ay, 3)},
            {"x": round(midx, 3), "y": round(ay, 3)},
            {"x": round(midx, 3), "y": round(by, 3)},
            {"x": round(tx, 3), "y": round(by, 3)},
        ]
    else:
        # Mostly vertical: leave from top/bottom edges.
        sy = a["y"] + a["h"] if by >= ay else a["y"]
        ty = b["y"] if by >= ay else b["y"] + b["h"]
        midy = (sy + ty) / 2
        return [
            {"x": round(ax, 3), "y": round(sy, 3)},
            {"x": round(ax, 3), "y": round(midy, 3)},
            {"x": round(bx, 3), "y": round(midy, 3)},
            {"x": round(bx, 3), "y": round(ty, 3)},
        ]


def build_layout(model: dict, slide: dict) -> dict:
    """Produce a layout-model dict from an architecture-model dict."""
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])

    roots = _build_forest(nodes)
    diagram_w, diagram_h = _layout_roots(roots)

    # Fit the diagram into the slide's safe area, reserving a strip at the top
    # for the slide title and a strip at the bottom for the legend, then
    # center the diagram in the remaining area (allowing scale-up to fill it).
    margin = slide["safeMarginIn"]
    legend_strip = 0.45
    avail_w = slide["widthIn"] - 2 * margin
    avail_h = slide["heightIn"] - 2 * margin - TITLE_STRIP_IN - legend_strip
    if diagram_w and diagram_h:
        scale = min(avail_w / diagram_w, avail_h / diagram_h)
    else:
        scale = 1.0
    scaled_w = diagram_w * scale
    scaled_h = diagram_h * scale
    offset_x = margin + (avail_w - scaled_w) / 2
    offset_y = margin + TITLE_STRIP_IN + (avail_h - scaled_h) / 2

    leaf_w_after = LEAF_W * scale
    readability_warning = (
        None
        if leaf_w_after >= MIN_LEAF_W_AFTER_SCALE
        else (
            f"leaf icons scale to {leaf_w_after:.2f}in wide (< "
            f"{MIN_LEAF_W_AFTER_SCALE}in); diagram is dense for one 16:9 slide"
        )
    )

    boxes = _flatten(roots)
    objects = []
    obj_by_id: dict[str, dict] = {}
    for b in boxes:
        obj = {
            "objectId": b.node_id,
            "kind": "CONTAINER" if b.is_container else "ICON_LABEL",
            "x": round(offset_x + b.x * scale, 3),
            "y": round(offset_y + b.y * scale, 3),
            "w": round(b.w * scale, 3),
            "h": round(b.h * scale, 3),
            "z": b.depth,  # outer containers behind, leaves in front
            "parentObjectId": b.parent_id,
            "iconKey": icon_key(b.node_type, b.aws_service),
            "label": b.label,
            "nodeType": b.node_type,
        }
        objects.append(obj)
        obj_by_id[b.node_id] = obj

    max_depth = max((b.depth for b in boxes), default=0)
    connectors = []
    for e in edges:
        src = obj_by_id.get(e.get("source"))
        tgt = obj_by_id.get(e.get("target"))
        if not src or not tgt:
            continue  # validator will flag missing endpoints
        connectors.append({
            "edgeId": e["id"],
            "sourceObjectId": e["source"],
            "targetObjectId": e["target"],
            "route": "ORTHOGONAL",
            "z": max_depth + 1,  # connectors above all boxes
            "label": e.get("label"),
            "points": _anchor_points(src, tgt),
        })

    layout = {
        "schemaVersion": "1.0",
        "projectId": model.get("projectId", ""),
        "jobId": model.get("jobId", ""),
        "title": model.get("title", ""),
        "slide": slide,
        "objects": objects,
        "connectors": connectors,
    }
    layout["_warnings"] = [readability_warning] if readability_warning else []
    return layout
