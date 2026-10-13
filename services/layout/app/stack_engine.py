"""Generic recursive stack layout engine (AWS diagram-as-code style).

One general rule, applied to EVERY container recursively:

    A container lays its children as either a HORIZONTAL stack (row) or a
    VERTICAL stack (column), chosen by a deterministic policy, and sizes itself
    to fit. Peer siblings of the same kind are given a UNIFORM size.

This single rule reproduces the clean, aligned grids AWS uses - VPC (vertical
tiers) of AZs (horizontal, equal columns) of subnets (vertical tiers) of
services - and applies equally to every other component (Security /
Management / Application groups, tiers, etc.). No component-specific hacks.

Grounded in awslabs/diagram-as-code conventions: HorizontalStack for peer
redundancy, VerticalStack for tiers, North-South orientation.

Units are inches; origin top-left (PowerPoint-friendly).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .icons import CONTAINER_TYPES, icon_key

# --- Geometry constants (inches) -------------------------------------------
LEAF_W = 1.35
LEAF_H = 1.15            # room for a square icon + a 2-line label below
GAP = 0.32              # gap between siblings
PAD = 0.24              # inner padding inside a container
TITLE_H = 0.34          # container title strip height
TITLE_STRIP_IN = 0.55   # reserved for the slide title at the very top
LEGEND_STRIP_IN = 0.45  # reserved for the legend at the bottom

# Node types that AWS lays out as horizontal peers (redundancy).
_HORIZONTAL_PEER_TYPES = {"AVAILABILITYZONE"}
# Node types that AWS stacks vertically (tiers).
_VERTICAL_TIER_TYPES = {"SUBNET"}

# Ruleset section 5: architectural tier lanes. Lower rank = higher in the box.
# NETWORK/EDGE (ingress) at top, COMPUTE (business logic) middle, DATA at bottom.
_TIER_RANK = {
    "NETWORK": 0, "EDGE": 0,
    "COMPUTE": 1, "SERVICE": 1, "COMPONENT": 1, "INTEGRATION": 1,
    "DATABASE": 2, "DATASTORE": 2, "STORAGE": 2,
}
# Fractional Y position of each tier lane's CENTER within the subnet body.
_TIER_Y_FRAC = {0: 0.15, 1: 0.45, 2: 0.75}

# Rule 2.2 (Absolute Swimlane Matrix): locked absolute Y/H for subnet roles on
# the 7.5in canvas. Same-role subnets across every AZ share these EXACT values
# so tiers line up perfectly across the page. Used only by the "stack-abs"
# engine mode; the default "stack" mode keeps the fractional/scale-fit layout.
ICON_SIZE = 0.5                      # Rule 2.3: locked square icon size
_SWIMLANE = {
    "public":  {"y": 2.1, "h": 1.2},   # ingress / routing subnets
    "compute": {"y": 3.5, "h": 1.2},   # private application/compute subnets
    "data":    {"y": 4.9, "h": 1.2},   # isolated database/data subnets
}
_PUBLIC_MARKERS = ("public", "dmz", "internet-facing", "internet facing", "edge")
_DATA_MARKERS = ("isolated", "data", "database", "db", "persistence")
_COMPUTE_MARKERS = ("private", "app", "application", "compute", "workload")


def _tier_rank(node_type: str) -> int:
    return _TIER_RANK.get(node_type, 1)


def _subnet_role(label: str, child_types: list[str]) -> str:
    """Classify a subnet as 'public' | 'compute' | 'data' for absolute laning.

    Label wins first (explicit intent), then falls back to the dominant tier
    rank of the subnet's leaf children (DB children => data, etc.).
    """
    text = (label or "").lower()
    # Data markers checked before compute because "private data" contains both.
    if any(m in text for m in _DATA_MARKERS):
        return "data"
    if any(m in text for m in _PUBLIC_MARKERS):
        return "public"
    if any(m in text for m in _COMPUTE_MARKERS):
        return "compute"
    # Fall back to child node tiers.
    ranks = [_tier_rank(t) for t in child_types] or [1]
    dominant = max(set(ranks), key=ranks.count)
    return {0: "public", 1: "compute", 2: "data"}[dominant]


@dataclass
class Box:
    node_id: str
    node_type: str
    label: str
    aws_service: str | None
    parent_id: str | None
    cidr: str | None = None   # illustrative network block, e.g. '10.0.0.0/16'
    spec: str | None = None   # deployment spec, e.g. 'm6i.xlarge | Auto Scaling 2-4'
    children: list["Box"] = field(default_factory=list)
    depth: int = 0
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def is_container(self) -> bool:
        return self.node_type in CONTAINER_TYPES or bool(self.children)


# Category grouping for loose services: nodeType -> (group id suffix, label).
_GROUP_FOR_TYPE = {
    "SECURITY": ("security", "Security & Identity"),
    "OBSERVABILITY": ("management", "Management & Governance"),
    "INTEGRATION": ("application", "Application Services"),
    "STORAGE": ("application", "Application Services"),
    "NETWORK": ("application", "Application Services"),
    "EDGE": ("application", "Application Services"),
    "SERVICE": ("application", "Application Services"),
    "COMPONENT": ("application", "Application Services"),
}
_CONTAINER_TYPES_LOCAL = {
    "CONTAINER", "ACCOUNT", "REGION", "VPC", "AVAILABILITYZONE", "SUBNET",
}
# External actors / systems AND global edge services (Route 53, CloudFront,
# WAF) are the far-LEFT column. They must NOT be bucketed into an "Application
# Services" group - the absolute-swimlane pass places them in a dedicated left
# column outside the region (users -> edge -> VPC left-to-right order).
_EXTERNAL_LEFT_TYPES = {"ACTOR", "EXTERNAL", "EDGE"}


def _auto_group_loose_services(nodes: list[dict]) -> list[dict]:
    """Safety net: if Agent 1 left services as direct children of a REGION or
    ACCOUNT (no group container), synthesize category group CONTAINERs and
    reparent those services into them. Guarantees a clean grouped layout
    regardless of LLM variability. Services already inside a subnet/VPC/group
    are left untouched.
    """
    by_id = {n["id"]: n for n in nodes}
    # A parent is a "bare" structural container (services here should be
    # grouped into a labeled box rather than floating). NOTE: VPC is NOT bare -
    # VPC-direct leaves (Internet Gateway, the single multi-AZ ALB, VPC
    # endpoints) are legitimate VPC-scoped infrastructure and must stay inside
    # the VPC boundary, placed in the VPC-leaf band by the absolute-swimlane
    # pass - not pulled out into a right-hand "Application Services" group.
    bare_parent_types = {"REGION", "ACCOUNT"}
    new_groups: dict[tuple[str, str], dict] = {}  # (parentId, groupKey) -> node
    changed = False

    for n in nodes:
        if n.get("type") in _CONTAINER_TYPES_LOCAL:
            continue
        if n.get("type") in _EXTERNAL_LEFT_TYPES:
            continue  # external actors go to the left column, not a group
        pid = n.get("parentId")
        parent = by_id.get(pid) if pid else None
        if not parent or parent.get("type") not in bare_parent_types:
            continue
        # This service is loose under a region/account -> bucket it.
        group_suffix, group_label = _GROUP_FOR_TYPE.get(
            n.get("type", "SERVICE"), ("application", "Application Services")
        )
        key = (pid, group_suffix)
        if key not in new_groups:
            gid = f"grp-{group_suffix}-{pid}"
            new_groups[key] = {
                "id": gid,
                "type": "CONTAINER",
                "label": group_label,
                "parentId": pid,
                "properties": {},
                "evidenceIds": [],
            }
        n["parentId"] = new_groups[key]["id"]
        changed = True

    if not changed:
        return nodes
    return nodes + list(new_groups.values())


def _children_of(nodes: list[dict]) -> dict[str, list[dict]]:
    kids: dict[str, list[dict]] = {}
    for n in nodes:
        kids.setdefault(n.get("parentId"), []).append(n)
    return kids


def _leaf_descendant_count(nid: str, kids: dict[str, list[dict]]) -> int:
    """Count non-container leaf descendants under a node."""
    total = 0
    for c in kids.get(nid, []):
        if c.get("type") in _CONTAINER_TYPES_LOCAL:
            total += _leaf_descendant_count(c["id"], kids)
        else:
            total += 1
    return total


def _mirror_empty_azs(nodes: list[dict]) -> list[dict]:
    """Fill empty Availability Zones by cloning a populated peer AZ's subtree.

    Nova often details ONE AZ and leaves peer AZs (and their subnets) empty,
    treating them as implicit HA mirrors. We make that redundancy explicit:
    for each VPC, if some sibling AZ has workload services and a peer AZ is
    empty, clone the populated AZ's subnet/service subtree into the empty peer
    (with fresh ids), preserving the empty AZ's own label.
    """
    by_id = {n["id"]: n for n in nodes}
    kids = _children_of(nodes)
    added: list[dict] = []

    # Find VPCs, then their AZ children.
    vpcs = [n for n in nodes if n.get("type") == "VPC"]
    for vpc in vpcs:
        azs = [c for c in kids.get(vpc["id"], []) if c.get("type") == "AVAILABILITYZONE"]
        if len(azs) < 2:
            continue
        populated = [az for az in azs if _leaf_descendant_count(az["id"], kids) > 0]
        empty = [az for az in azs if _leaf_descendant_count(az["id"], kids) == 0]
        if not populated or not empty:
            continue
        template = max(populated, key=lambda az: _leaf_descendant_count(az["id"], kids))

        for target_az in empty:
            # Clone the template AZ's entire subtree under target_az.
            def clone_subtree(src_parent_id: str, dst_parent_id: str, suffix: str):
                for child in kids.get(src_parent_id, []):
                    new_id = f"{child['id']}-{suffix}"
                    clone = {
                        "id": new_id,
                        "type": child.get("type"),
                        "label": child.get("label"),
                        "parentId": dst_parent_id,
                        "properties": dict(child.get("properties") or {}),
                        "evidenceIds": list(child.get("evidenceIds") or []),
                    }
                    added.append(clone)
                    clone_subtree(child["id"], new_id, suffix)

            suffix = f"mir-{target_az['id']}"
            clone_subtree(template["id"], target_az["id"], suffix)

    return nodes + added


def _prune_empty_containers(nodes: list[dict]) -> list[dict]:
    """Remove structural containers that have no leaf descendants (blank boxes).

    Runs AFTER mirroring, so only genuinely-empty leftovers are dropped (e.g.
    an empty subnet Nova created but never used, with no peer to mirror from).
    Keeps the top-level structure; only prunes SUBNET/AVAILABILITYZONE/CONTAINER
    that are truly empty.
    """
    prunable = {"SUBNET", "AVAILABILITYZONE", "CONTAINER"}
    changed = True
    result = list(nodes)
    while changed:
        changed = False
        kids = _children_of(result)
        drop_ids = set()
        for n in result:
            if n.get("type") in prunable and _leaf_descendant_count(n["id"], kids) == 0:
                drop_ids.add(n["id"])
        if drop_ids:
            result = [n for n in result if n["id"] not in drop_ids]
            changed = True
    return result


def _build_forest(nodes: list[dict]) -> list[Box]:
    # Deterministic normalization (fixes Nova's empty-AZ / blank-subnet habit):
    # 1) mirror a populated AZ into empty peer AZs so HA is drawn explicitly,
    # 2) prune any containers still empty after mirroring,
    # 3) group any loose services into labeled boxes.
    nodes = _mirror_empty_azs(nodes)
    nodes = _prune_empty_containers(nodes)
    nodes = _auto_group_loose_services(nodes)
    by_id: dict[str, Box] = {}
    for n in nodes:
        props = n.get("properties") or {}
        by_id[n["id"]] = Box(
            node_id=n["id"],
            node_type=n.get("type", "COMPONENT"),
            label=n.get("label", n["id"]),
            aws_service=props.get("awsService"),
            parent_id=n.get("parentId"),
            cidr=props.get("cidr"),
            spec=props.get("spec"),
        )
    roots: list[Box] = []
    for b in by_id.values():
        parent = by_id.get(b.parent_id) if b.parent_id else None
        if parent and parent is not b:
            parent.children.append(b)
        else:
            roots.append(b)

    def set_depth(b: Box, d: int) -> None:
        b.depth = d
        for c in b.children:
            set_depth(c, d + 1)

    for r in roots:
        set_depth(r, 0)
    return roots


# --- Orientation policy ----------------------------------------------------
def _orientation(container: Box) -> str:
    """Decide how a container stacks its children: 'row', 'col', or 'grid'.

    Policy (general, AWS-style, tuned for a wide 16:9 canvas):
      * AZs (peer redundancy)            -> row (equal columns, side by side).
      * Subnets (tiers)                  -> col (public above private).
      * All leaves                       -> grid (balanced rows).
      * Several sub-CONTAINERS (e.g. a REGION holding a VPC + Security +
        Management + Application groups) -> grid of containers, so the diagram
        spreads across the wide canvas instead of forming a tall column.
      * A single child or mixed leaf/container -> col.
    """
    kids = container.children
    if not kids:
        return "col"

    if all(c.node_type in _HORIZONTAL_PEER_TYPES for c in kids):
        return "row"
    if all(c.node_type in _VERTICAL_TIER_TYPES for c in kids):
        return "col"
    # Ruleset section 5: a SUBNET's workload leaves are laid in tier lanes
    # (network top / compute middle / data bottom) so compute and DB never
    # share a horizontal lane.
    if container.node_type == "SUBNET" and all(not c.is_container for c in kids):
        return "tiered"
    if all(not c.is_container for c in kids):
        return "grid"

    container_kids = [c for c in kids if c.is_container]
    # Several large sub-containers: arrange them in a container-grid (rows of
    # roughly-equal cells) to use the wide canvas. This is what turns the
    # tall Region column into a balanced 2-up / 3-up arrangement.
    if len(container_kids) >= 2:
        return "cgrid"
    return "col"


def _grid_dims(n: int) -> tuple[int, int]:
    import math
    if n <= 0:
        return 1, 1
    cols = max(1, math.ceil(math.sqrt(n * 1.4)))  # slightly wide for 16:9
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    return cols, rows


def _cgrid_cols(children: list) -> int:
    """Columns for a container-grid: favor 2 columns so big blocks sit side by
    side and the diagram fills the wide canvas (e.g. VPC | groups)."""
    n = len(children)
    if n <= 1:
        return 1
    if n <= 4:
        return 2
    return 3


def _rows(children: list, cols: int) -> list[list]:
    return [children[i : i + cols] for i in range(0, len(children), cols)]


# --- Measure (bottom-up): assign intrinsic w/h -----------------------------
def _measure(box: Box) -> None:
    if not box.children:
        box.w, box.h = LEAF_W, LEAF_H
        return
    for c in box.children:
        _measure(c)

    # Uniform sizing for peer containers (clean AWS grid look).
    _equalize_peers(box.children)

    orient = _orientation(box)
    if orient == "tiered":
        # Group children into up to 3 tier lanes; each lane is a horizontal row.
        lanes: dict[int, list[Box]] = {}
        for c in box.children:
            lanes.setdefault(_tier_rank(c.node_type), []).append(c)
        used = sorted(lanes)
        # Width = widest lane; height = sum of lane heights with gaps between
        # the 3 tier positions (we reserve a full 3-lane body so 15/45/75%
        # fractions line up even if a tier is empty).
        lane_w = max(
            sum(c.w for c in lanes[r]) + GAP * (len(lanes[r]) - 1) for r in used
        )
        lane_h = max(c.h for c in box.children)
        inner_w = lane_w
        # Reserve 3 vertical slots so the fractional tier positions are stable.
        inner_h = 3 * lane_h + 2 * GAP
    elif orient == "row":
        inner_w = sum(c.w for c in box.children) + GAP * (len(box.children) - 1)
        inner_h = max(c.h for c in box.children)
    elif orient == "grid":
        cols, rows = _grid_dims(len(box.children))
        cw = max(c.w for c in box.children)
        ch = max(c.h for c in box.children)
        inner_w = cols * cw + GAP * (cols - 1)
        inner_h = rows * ch + GAP * (rows - 1)
    elif orient == "cgrid":
        # Container grid: pack sub-containers into rows/cols using each cell's
        # ACTUAL size (they differ), row height = tallest in the row.
        cols = _cgrid_cols(box.children)
        rows = _rows(box.children, cols)
        inner_w = max(
            sum(c.w for c in row) + GAP * (len(row) - 1) for row in rows
        )
        inner_h = sum(max(c.h for c in row) for row in rows) + GAP * (len(rows) - 1)
    else:  # col
        inner_w = max(c.w for c in box.children)
        inner_h = sum(c.h for c in box.children) + GAP * (len(box.children) - 1)

    box.w = inner_w + 2 * PAD
    box.h = inner_h + 2 * PAD + TITLE_H


def _equalize_peers(children: list[Box]) -> None:
    """Peer containers of the same type get a uniform size (grid alignment)."""
    from collections import defaultdict
    groups: dict[str, list[Box]] = defaultdict(list)
    for c in children:
        if c.is_container:
            groups[c.node_type].append(c)
    for peers in groups.values():
        if len(peers) >= 2:
            mw = max(p.w for p in peers)
            mh = max(p.h for p in peers)
            for p in peers:
                p.w, p.h = mw, mh


# --- Place (top-down): assign absolute x/y ---------------------------------
def _place(box: Box) -> None:
    if not box.children:
        return
    orient = _orientation(box)
    ox = box.x + PAD
    oy = box.y + TITLE_H + PAD
    inner_w = box.w - 2 * PAD

    if orient == "tiered":
        # Ruleset section 5: place each tier lane at its fractional Y (15/45/75%)
        # of the subnet body; within a lane, center the row horizontally.
        body_y = box.y + TITLE_H
        body_h = box.h - TITLE_H - PAD
        lanes: dict[int, list[Box]] = {}
        for c in box.children:
            lanes.setdefault(_tier_rank(c.node_type), []).append(c)
        for rank, members in lanes.items():
            frac = _TIER_Y_FRAC.get(rank, 0.45)
            lane_cy = body_y + body_h * frac
            total = sum(c.w for c in members) + GAP * (len(members) - 1)
            cx = ox + max(0.0, (inner_w - total) / 2)
            for c in members:
                c.x = cx
                c.y = lane_cy - c.h / 2
                _place(c)
                cx += c.w + GAP
    elif orient == "row":
        # Center the row of children within the inner width.
        total = sum(c.w for c in box.children) + GAP * (len(box.children) - 1)
        cx = ox + max(0.0, (inner_w - total) / 2)
        row_h = max(c.h for c in box.children)
        for c in box.children:
            c.x = cx
            c.y = oy + (row_h - c.h) / 2
            _place(c)
            cx += c.w + GAP
    elif orient == "grid":
        cols, _ = _grid_dims(len(box.children))
        cw = max(c.w for c in box.children)
        ch = max(c.h for c in box.children)
        for i, c in enumerate(box.children):
            r, q = divmod(i, cols)
            c.x = ox + q * (cw + GAP) + (cw - c.w) / 2
            c.y = oy + r * (ch + GAP) + (ch - c.h) / 2
            _place(c)
    elif orient == "cgrid":
        cols = _cgrid_cols(box.children)
        rows = _rows(box.children, cols)
        cy = oy
        for row in rows:
            row_h = max(c.h for c in row)
            cx = ox
            for c in row:
                c.x = cx
                c.y = cy + (row_h - c.h) / 2
                _place(c)
                cx += c.w + GAP
            cy += row_h + GAP
    else:  # col
        cy = oy
        for c in box.children:
            # Center each child horizontally within the inner width.
            c.x = ox + max(0.0, (inner_w - c.w) / 2)
            c.y = cy
            _place(c)
            cy += c.h + GAP


def _flatten(roots: list[Box]) -> list[Box]:
    out: list[Box] = []

    def walk(b: Box) -> None:
        out.append(b)
        for c in b.children:
            walk(c)

    for r in roots:
        walk(r)
    return out


# Rule 2.1 (Ingestion Scrub): spatial/routing fields that must never survive
# from Agent 1 into the layout engine. Agent 2 is the sole authority on geometry.
_SPATIAL_FIELDS = ("x", "y", "w", "h", "z", "points", "route", "edgeClass")


def _scrub_spatial(model: dict) -> dict:
    """Defensively strip any spatial/routing coordinates that leaked from Agent 1.

    The architecture contract already forbids these, but Agent 2 treats its
    input defensively: it recomputes ALL geometry, so any x/y/w/h/z/points/route
    present on incoming nodes or edges are deleted before layout begins.
    """
    for obj in model.get("nodes", []):
        for f in _SPATIAL_FIELDS:
            obj.pop(f, None)
    for e in model.get("edges", []):
        for f in _SPATIAL_FIELDS:
            e.pop(f, None)
    return model


# Absolute-swimlane canvas budget (Rule 2.2). The deployment VPC cluster is
# authored directly in absolute canvas inches (NOT scale-fit), so the locked
# subnet lanes at y=2.1/3.5/4.9 line up with real slide coordinates.
# Inner content bounds: the slide safe area (0.3..13.033 / 0.3..7.2) minus room
# for the ACCOUNT + REGION wrapper borders (each insets ~0.15-0.3) and the
# auto-correct 0.15 padding, so grown wrappers still land inside the safe area.
_ABS_LEFT = 0.6          # left edge of the VPC cluster (account+region to its left)
# The right strip (~3.2in) is reserved for the description-panel column (Key
# Assumptions / Request Flow / Legend) that Agent 3 draws on the right. Diagram
# content must stop short of it so nothing overlaps the panels.
_PANEL_COL_W = 2.85      # Request Flow rail (2.65) + right margin (0.15) + small gap
_ABS_RIGHT = 13.333 - _PANEL_COL_W   # ~10.48: right edge available to diagram content
_ABS_TOP = 0.85          # below the slide-title strip
_ABS_BOTTOM = 7.2        # slide safe-margin bottom
_VPC_WIDTH_FRAC = 0.52   # VPC cluster ~52%; leaves a wide gap for the routing
                         # corridor + a group column on the right
_AZ_GAP = 0.25
# Remediation Issue 1: nested boxes must leave room for the PARENT's border
# stroke (VPC is 2.0pt ~= 0.028in, drawn centered on the edge) plus rounding, or
# the child's right edge visually bleeds over the parent border. 0.18in inset
# on each side gives clear breathing room at every nesting level.
_SUBNET_INSET = 0.18
_LANE_TOP = 2.1          # first (public) lane Y  -> must match _SWIMLANE
_LANE_BOTTOM = 4.9 + 1.2 # bottom of the data lane


def _apply_absolute_swimlanes(objects: list[dict]) -> None:
    """Rule 2.2 + 2.3: author the VPC/AZ/subnet cluster in ABSOLUTE coordinates.

    When a VPC->AZ->SUBNET deployment structure is present, this replaces the
    scale-fit geometry of that cluster with absolute canvas coordinates so the
    subnet role lanes (public 2.1 / compute 3.5 / data 4.9, H 1.2) sit at real
    slide inches and align across every AZ. AZs become equal-width columns
    spanning all three lanes; subnets lock to their role lane; icons are placed
    at 0.5x0.5 with the dynamic spacing formula. The Account/Region wrappers are
    grown to contain the cluster, and sibling group containers (Security /
    Management / Application) are reflowed into the right-hand column.

    No-op when there is no VPC/AZ/SUBNET structure (non-deployment diagrams).
    """
    by_id = {o["objectId"]: o for o in objects}
    children_of: dict[str, list[dict]] = {}
    for o in objects:
        children_of.setdefault(o.get("parentObjectId"), []).append(o)

    def first(t: str) -> dict | None:
        return next((o for o in objects if o.get("nodeType") == t), None)

    vpc = first("VPC")
    if not vpc:
        return
    # Find AZs wherever Nova put them: ideally under the VPC, but it often
    # parents them to the REGION (leaving the VPC empty). Accept both, and
    # re-parent stray AZs INTO the VPC so containment/wrappers stay correct.
    azs = [o for o in objects if o.get("nodeType") == "AVAILABILITYZONE"]
    if not azs:
        return
    for az in azs:
        if az.get("parentObjectId") != vpc["objectId"]:
            az["parentObjectId"] = vpc["objectId"]
    # Rebuild the child index after re-parenting so subnets resolve correctly.
    children_of = {}
    for o in objects:
        children_of.setdefault(o.get("parentObjectId"), []).append(o)

    # VPC-level LEAF nodes (Internet Gateway, the single multi-AZ ALB, VPC
    # endpoints) are direct children of the VPC, not inside any AZ. They get a
    # dedicated horizontal band at the top of the VPC, above the AZ columns.
    vpc_leaves = [
        c for c in children_of.get(vpc["objectId"], [])
        if c.get("kind") == "ICON_LABEL"
        and c.get("nodeType") not in _EXTERNAL_LEFT_TYPES
    ]
    # The VPC-leaf band sits between the VPC title and the first (public) lane.
    # It occupies a slim strip; the three subnet lanes are COMPRESSED to make
    # room (keeping their alignment) rather than overflowing the canvas.
    # With interface endpoints now moved into the private subnets (ENIs) and
    # edge services on the left, the band holds only a few nodes (IGW, ALB,
    # gateway endpoints) so it can be a touch taller/clearer without crowding.
    # Band height must clear the icon (0.36) + its title gap (0.30) + the label
    # text drawn BELOW the icon (~0.26) so band labels never overlap the AZ
    # title strips directly beneath. 1.05in gives that clearance.
    vpc_band_h = 0.92 if vpc_leaves else 0.0
    vpc_leaf_size = 0.36

    # External-left column: ACTOR/EXTERNAL (users, on-prem) AND global EDGE
    # services (Route 53, CloudFront, WAF) sit in a dedicated column on the far
    # left, in request order (users first, then edge services) so the primary
    # flow reads strictly left-to-right into the VPC.
    def _left_order(o: dict) -> int:
        # Users first, then edge services roughly in DNS->CDN->WAF order.
        nt = o.get("nodeType")
        lbl = (o.get("label") or "").lower()
        if nt in ("ACTOR", "EXTERNAL"):
            return 0
        if "route 53" in lbl or "route53" in lbl or "dns" in lbl:
            return 1
        if "cloudfront" in lbl or "cdn" in lbl:
            return 2
        if "waf" in lbl:
            return 3
        return 4

    external_left = sorted(
        [o for o in objects
         if o.get("nodeType") in _EXTERNAL_LEFT_TYPES
         and o.get("kind") == "ICON_LABEL"],
        key=_left_order,
    )
    # Wide enough for the 'External Actors (Outside AWS)' title to render in
    # full inside its dashed box (the box spans this column).
    _ACTORS_BOX_W = 1.85
    # Gap must clear the account/region wrapper insets (~0.3in total) that grow
    # leftward toward the VPC, plus a visible margin, so the box never overlaps
    # the account after the white-space scale compresses the gap.
    ext_col_w = (_ACTORS_BOX_W + 0.85) if external_left else 0.0

    # --- 1. VPC cluster occupies the center zone in absolute coords ---------
    # Supporting services now live in a BOTTOM strip (Stage B), not a right
    # column, so the VPC uses nearly the full usable width to the right of the
    # external-actors column.
    usable_w = _ABS_RIGHT - _ABS_LEFT - ext_col_w
    vpc_x = _ABS_LEFT + ext_col_w    # leave room for the external-actors column
    vpc_w = usable_w * 0.97

    # Place external-left nodes in their column, vertically distributed in
    # request order (top to bottom): users, then Route 53, CloudFront, WAF.
    if external_left:
        # Centre each actor icon horizontally within the actors box column so
        # the wider titled box reads cleanly (box spans _ABS_LEFT..+box_w).
        ex_x = _ABS_LEFT + (_ACTORS_BOX_W - ICON_SIZE) / 2
        n_e = len(external_left)
        band_top = _LANE_TOP - 0.2
        band_h = (_LANE_BOTTOM - _LANE_TOP) + 0.4
        step = band_h / max(1, n_e)
        for i, ex in enumerate(external_left):
            ex["w"], ex["h"] = ICON_SIZE, ICON_SIZE
            ex["x"] = round(ex_x, 3)
            ex["y"] = round(band_top + i * step + (step - ICON_SIZE) / 2, 3)
    # Clamp the VPC top so account/region wrappers never breach the top margin.
    vpc_top = max(_ABS_TOP + 0.25, _LANE_TOP - 0.3)
    vpc["x"], vpc["w"] = round(vpc_x, 3), round(vpc_w, 3)
    vpc["y"] = round(vpc_top, 3)  # height set after lanes are positioned below

    # --- 1b. Place VPC-level leaf nodes in the top band ----------------------
    if vpc_leaves:
        band_y = vpc_top + 0.30  # below the VPC title strip, above the AZ columns
        n_l = len(vpc_leaves)
        band_inner = vpc_w - 2 * _SUBNET_INSET
        sp = max(0.12, (band_inner - n_l * vpc_leaf_size) / (n_l + 1))
        for j, lf in enumerate(vpc_leaves):
            lf["x"] = round(vpc_x + _SUBNET_INSET + sp + j * (vpc_leaf_size + sp), 3)
            lf["y"] = round(band_y, 3)
            lf["w"], lf["h"] = vpc_leaf_size, vpc_leaf_size

    # Does the region have supporting groups that will become a bottom strip?
    _has_support_strip = any(
        o.get("nodeType") == "CONTAINER"
        and o["objectId"] != vpc["objectId"]
        and o.get("parentObjectId") in (
            (next((r["objectId"] for r in objects if r.get("nodeType") == "REGION"), None)),
            vpc["objectId"],
        )
        for o in objects
    )

    # When a VPC band exists, the three lanes are repositioned into the space
    # below the band and above the canvas bottom (compressed but still aligned).
    # _lane_y maps a role -> absolute lane Y, computed once here.
    az_top = (_LANE_TOP - 0.3) + vpc_band_h
    # Leave room below the AZ box for wrapper insets + auto-correct padding, and
    # for the bottom supporting-services strip (~1.35in) when present.
    _strip_reserve = 1.5 if _has_support_strip else 0.0
    canvas_bottom = _ABS_BOTTOM - 0.65 - _strip_reserve
    # A subnet lane must be tall enough to stack, inside its box, WITHOUT
    # overlap: the title strip, the icon, and the caption drawn below it.
    # Budget it compactly - the global white-space scale shrinks everything, so
    # an over-tall lane here just forces a smaller overall scale (tiny icons +
    # colliding labels). Keep the floor tight; the spec sub-line is optional and
    # only on a couple of nodes, so it is NOT reserved for every lane.
    #   title (0.24) + top gap (0.06) + icon (0.5) + label gap (0.04)
    #   + 2-line label (~0.30) + bottom pad (0.06) ~= 1.20in
    # Icon sits well below the subnet title strip. This offset is applied in
    # PRE-scale space and the compiler's title strip is a FIXED 0.22in; the
    # cluster is later shrunk to ~0.7x, so the icon's post-scale clearance is
    # ~0.38*0.7 = 0.27 > 0.22 and the strip never overlaps the icon/caption.
    _SUBNET_TITLE_H = 0.4
    _ICON_TOP_GAP = 0.06
    _LABEL_BLOCK_H = 0.30           # 2 label lines
    _LANE_MIN_H = round(
        _SUBNET_TITLE_H + _ICON_TOP_GAP + ICON_SIZE + 0.04 + _LABEL_BLOCK_H + 0.06, 3
    )  # ~1.20
    if vpc_band_h:
        first_cy = az_top + 0.32          # first lane top, just below AZ title
        # Fit 3 lanes + 2 inter-lane gaps into the available vertical space.
        # Derive lane height from the space so lanes never overlap after the
        # later white-space scale (which shrinks uniformly, preserving gaps),
        # but never below the min needed for icon + label to fit inside the box.
        _LANE_GAP = 0.18
        avail = canvas_bottom - first_cy
        lane_h = max(_LANE_MIN_H, (avail - 2 * _LANE_GAP) / 3)
        step = lane_h + _LANE_GAP
        _lane_y = {
            "public": round(first_cy, 3),
            "compute": round(first_cy + step, 3),
            "data": round(first_cy + 2 * step, 3),
        }
        az_bottom = first_cy + 2 * step + lane_h + 0.1
    else:
        _lane_y = {r: _SWIMLANE[r]["y"] for r in ("public", "compute", "data")}
        lane_h = max(_LANE_MIN_H, _SWIMLANE["public"]["h"])
        az_bottom = _LANE_BOTTOM + 0.15

    # Now that the lane extent is known, size the VPC to enclose everything.
    vpc["h"] = round((az_bottom + 0.15) - vpc["y"], 3)

    # --- 2. AZs as equal-width columns spanning all lanes --------------------
    n_az = len(azs)
    inner_left = vpc_x + _SUBNET_INSET
    inner_w = vpc_w - 2 * _SUBNET_INSET
    az_w = (inner_w - _AZ_GAP * (n_az - 1)) / n_az
    for i, az in enumerate(sorted(azs, key=lambda a: a.get("x", 0))):
        az_x = inner_left + i * (az_w + _AZ_GAP)
        az["x"], az["w"] = round(az_x, 3), round(az_w, 3)
        az["y"], az["h"] = round(az_top, 3), round(az_bottom - az_top, 3)

        # --- 3. Subnets in this AZ lock to their role lane -------------------
        sub_x = az_x + _SUBNET_INSET
        sub_w = az_w - 2 * _SUBNET_INSET
        for sn in [c for c in children_of.get(az["objectId"], [])
                   if c.get("nodeType") == "SUBNET"]:
            kids = children_of.get(sn["objectId"], [])
            role = _subnet_role(
                sn.get("label", ""), [c.get("nodeType", "") for c in kids]
            )
            sn["x"], sn["w"] = round(sub_x, 3), round(sub_w, 3)
            sn["y"], sn["h"] = _lane_y[role], round(lane_h, 3)

            # --- 4. Rule 2.3: dynamic icon spacing inside the subnet ---------
            icons = [c for c in kids if c.get("kind") == "ICON_LABEL"]
            n = len(icons)
            if n == 0:
                continue
            # Shrink icons to fit when a lane is crowded (e.g. ECS + several
            # interface endpoints in one private subnet) so they never overflow
            # the subnet width and trigger auto-correct growth / AZ overlap.
            isz = ICON_SIZE
            min_gap = 0.08
            if (n * isz + (n + 1) * min_gap) > sub_w:
                isz = max(0.3, (sub_w - (n + 1) * min_gap) / n)
            spacing = max(min_gap, (sub_w - n * isz) / (n + 1))
            # Place the icon just BELOW the subnet title strip (not vertically
            # centred) so the caption drawn beneath the icon still lands inside
            # the subnet box instead of spilling over the bottom border.
            icon_y = sn["y"] + _SUBNET_TITLE_H + _ICON_TOP_GAP
            # Per-icon horizontal slot: the caption drawn under each icon must
            # NOT exceed this width or it collides with the neighbouring icon's
            # caption when a lane holds 2+ icons. The compiler/preview clamp the
            # label box to labelMaxW (slot pitch, minus a small gutter).
            slot_pitch = isz + spacing
            label_max_w = round(max(isz, slot_pitch - 0.08), 3)
            for j, ic in enumerate(icons):
                ic["x"] = round(sub_x + spacing + j * (isz + spacing), 3)
                ic["y"] = round(icon_y, 3)
                ic["w"], ic["h"] = round(isz, 3), round(isz, 3)
                ic["labelMaxW"] = label_max_w

    # --- 5. Reflow sibling group CONTAINERs into the right-hand column -------
    # Groups can be parented to the REGION (standard) OR synthesized by the
    # engine and parented to the VPC (loose-service auto-grouping). Collect
    # BOTH, re-parent any VPC-level groups to the region, and reflow them all
    # into the right column so they never overlap the AZ columns.
    region = next((o for o in objects if o.get("nodeType") == "REGION"), None)
    region_id = region["objectId"] if region else vpc.get("parentObjectId")
    groups = [
        o for o in objects
        if o.get("nodeType") == "CONTAINER"
        and o["objectId"] != vpc["objectId"]
        and o.get("parentObjectId") in (region_id, vpc["objectId"])
    ]
    for g in groups:
        if g.get("parentObjectId") == vpc["objectId"]:
            g["parentObjectId"] = region_id
    # Cross-cutting groups (Security & Identity, Management/Observability, etc.)
    # are laid out as SIDE-BY-SIDE titled boxes along the BOTTOM of the region
    # (below the AZ columns), matching the reference's 'Security Group' +
    # 'Observability Group' pair. Each group keeps its own container + icons;
    # empty groups are dropped. Up to 3 across; icons wrap to rows within a box.
    strip_boxes: list[dict] = []
    if groups:
        # Keep only groups that actually have leaf icons.
        populated = []
        for g in groups:
            icons = [c for c in children_of.get(g["objectId"], [])
                     if c.get("kind") == "ICON_LABEL"]
            if icons:
                populated.append((g, icons))
        # Drop empty group containers from the object list entirely.
        keep_ids = {g["objectId"] for g, _ in populated}
        drop_ids = {g["objectId"] for g in groups if g["objectId"] not in keep_ids}
        if drop_ids:
            objects[:] = [o for o in objects if o["objectId"] not in drop_ids]

        if populated:
            # Give the standard groups cleaner cross-cutting titles.
            _TITLE_MAP = {
                "security": "Security Group (Cross-Cutting)",
                "management": "Observability Group (Cross-Cutting)",
            }
            for g, _ in populated:
                oid = g["objectId"].lower()
                lbl = (g.get("label") or "").lower()
                if "secur" in oid or "secur" in lbl or "identity" in lbl:
                    g["label"] = _TITLE_MAP["security"]
                elif ("manage" in oid or "observ" in lbl or "governance" in lbl
                      or "monitor" in lbl):
                    g["label"] = _TITLE_MAP["management"]

            n_g = len(populated)
            # Clear gap below the VPC bottom (vpc ends at az_bottom+0.15) so the
            # group boxes never touch the VPC border after the white-space scale.
            row_top = az_bottom + 0.5
            row_h = 1.05
            band_w = vpc_w
            ggap = 0.5
            box_w = (band_w - ggap * (n_g - 1)) / n_g
            for gi, (g, icons) in enumerate(populated):
                gx = vpc_x + gi * (box_w + ggap)
                g["x"], g["w"] = round(gx, 3), round(box_w, 3)
                g["y"], g["h"] = round(row_top, 3), round(row_h, 3)
                strip_boxes.append(g)
                # Lay this group's icons in a centred horizontal row (wrapping
                # to a second row if there are many).
                inner = box_w - 2 * _SUBNET_INSET
                per_row = max(1, min(len(icons), int(inner / 0.72)))
                isz = min(ICON_SIZE, max(0.3,
                          (inner - (per_row + 1) * 0.12) / per_row))
                sp = max(0.1, (inner - per_row * isz) / (per_row + 1))
                slot_pitch = isz + sp
                # Clamp captions to the per-icon slot so adjacent group-icon
                # labels never collide, and drop the spec sub-line here (these
                # boxes are dense - name only keeps them readable).
                label_max_w = round(max(isz, slot_pitch - 0.06), 3)
                icy0 = row_top + 0.32
                for k, ic in enumerate(icons):
                    r, cidx = divmod(k, per_row)
                    ic["x"] = round(gx + _SUBNET_INSET + sp + cidx * (isz + sp), 3)
                    ic["y"] = round(icy0 + r * (isz + 0.34), 3)
                    ic["w"], ic["h"] = round(isz, 3), round(isz, 3)
                    ic["labelMaxW"] = label_max_w
                    ic.pop("spec", None)   # name-only in dense group boxes
        az_bottom_with_strip = (strip_boxes[0]["y"] + strip_boxes[0]["h"]
                                if strip_boxes else az_bottom)
    else:
        az_bottom_with_strip = az_bottom

    # --- 5b. External Actors titled box (Stage C) ---------------------------
    # Wrap the left-column ACTOR/EXTERNAL/EDGE nodes in a synthetic dashed
    # 'External Actors (Outside AWS)' container so they read as a labeled zone.
    actors_box = None
    if external_left:
        # Box spans the fixed-width actors column so its title fits in full.
        ax0 = _ABS_LEFT
        ay0 = min(e["y"] for e in external_left) - 0.45  # room for the title
        ax1 = _ABS_LEFT + _ACTORS_BOX_W
        ay1 = max(e["y"] + e["h"] for e in external_left) + 0.18
        actors_box = {
            "objectId": "external-actors-box",
            "kind": "CONTAINER",
            "nodeType": "EXTERNAL_GROUP",
            "label": "External Actors (Outside AWS)",
            "parentObjectId": None,
            "x": round(ax0, 3), "y": round(ay0, 3),
            "w": round(ax1 - ax0, 3), "h": round(ay1 - ay0, 3),
            "z": 0,
        }
        objects.append(actors_box)
        # Reparent the actor icons INTO the box so the validator treats them as
        # contained children (not overlapping siblings).
        for e in external_left:
            e["parentObjectId"] = "external-actors-box"

    # --- 6. Grow Account/Region wrappers to contain everything (Rule 8) ------
    # The REGION/ACCOUNT wrap the VPC + the bottom cross-cutting group boxes.
    # The external actors box sits OUTSIDE both boundaries, to the left.
    region_members = [vpc] + azs + strip_boxes
    region_cluster = region_members
    # Pre-scale insets are slightly larger than the validator's MIN_PADDING
    # (0.15) because the cluster is shrunk by the white-space scale afterwards.
    for wrapper_type, inset in (("REGION", 0.2), ("ACCOUNT", 0.22)):
        w = next((o for o in objects if o.get("nodeType") == wrapper_type), None)
        if not w:
            continue
        cluster = region_cluster
        left = min(c["x"] for c in cluster) - inset
        top = min(c["y"] for c in cluster) - inset - 0.3  # title strip
        right = max(c["x"] + c["w"] for c in cluster) + inset
        bottom = max(c["y"] + c["h"] for c in cluster) + inset
        w["x"], w["y"] = round(left, 3), round(top, 3)
        w["w"], w["h"] = round(right - left, 3), round(bottom - top, 3)
        region_cluster = region_cluster + [w]

    # --- 7. 30% white-space rule -------------------------------------------
    # Professional presentation design wants >= 30% of the slide empty. The
    # absolute layout above fills most of the canvas, so shrink the whole
    # deployment cluster uniformly about the slide centre until it occupies at
    # most WHITE_SPACE_FILL of each dimension, then re-centre it. This keeps the
    # locked-lane proportions and alignment intact - it only scales + centres.
    _apply_white_space_margin(objects)


# Target: content occupies at most this fraction of each axis.
WHITE_SPACE_FILL = 0.98
_SLIDE_W = 13.333
_SLIDE_H = 7.5
_CONTENT_LEFT = 0.35   # left slide margin (just inside the 0.3 safe area)
# The diagram cluster is confined to the LEFT of the right-hand description
# panel column (Agent 3 draws panels from ~10.4in rightward). Centre the
# cluster within [_CONTENT_LEFT, _CONTENT_RIGHT] so it never overlaps panels.
_CONTENT_RIGHT = 13.333 - _PANEL_COL_W + 0.05   # ~10.43
_CONTENT_TOP = 0.52   # below the slide title
# Keep the scaled cluster's bottom within the validator safe area (7.2). Leave
# ~0.35in headroom because validate()'s auto_correct grows the REGION then the
# ACCOUNT wrapper by MIN_PADDING (0.15 each) to contain the bottom group boxes,
# which would otherwise push the account past the safe area.
_CONTENT_BOTTOM = 6.6


def _apply_white_space_margin(objects: list[dict]) -> None:
    """Scale + centre all objects so the diagram leaves professional margins.

    Scales uniformly so the bounding box of all objects fits within the usable
    content band - which is the slide MINUS the right-hand description-panel
    column - then re-centres it there. Never scales UP (only shrinks).
    """
    if not objects:
        return
    minx = min(o["x"] for o in objects)
    maxx = max(o["x"] + o["w"] for o in objects)
    miny = min(o["y"] for o in objects)
    maxy = max(o["y"] + o["h"] for o in objects)
    cur_w = maxx - minx
    cur_h = maxy - miny
    if cur_w <= 0 or cur_h <= 0:
        return

    content_w = _CONTENT_RIGHT - _CONTENT_LEFT
    avail_w = content_w            # fill the full left column (two-column split)
    avail_h = (_CONTENT_BOTTOM - _CONTENT_TOP)
    # Two-column layout: the diagram cluster should FILL the left ~75% band, not
    # shrink into a small centred block. Scale uniformly to the binding
    # dimension (allowing modest scale-UP to fill), capped so neither dimension
    # overflows its band and icons don't blow up absurdly.
    fit_w = avail_w / cur_w
    fit_h = avail_h / cur_h
    scale = min(fit_w, fit_h, 1.35)

    # LEFT-aligned origin (hug the far-left margin), vertically centred in band.
    new_w = cur_w * scale
    new_h = cur_h * scale
    off_x = _CONTENT_LEFT
    off_y = _CONTENT_TOP + ((_CONTENT_BOTTOM - _CONTENT_TOP) - new_h) / 2
    # Guard: never let the top-left origin drift above the content band.
    off_y = max(off_y, _CONTENT_TOP)
    off_x = max(off_x, _CONTENT_LEFT)

    for o in objects:
        o["x"] = round(off_x + (o["x"] - minx) * scale, 3)
        o["y"] = round(off_y + (o["y"] - miny) * scale, 3)
        o["w"] = round(o["w"] * scale, 3)
        o["h"] = round(o["h"] * scale, 3)

    # Hard safety clamp: if rounding/geometry still pushes the cluster past the
    # validator safe area (bottom 7.2 / right content edge), apply ONE more
    # corrective shrink about the content top-left so everything lands inside.
    _HARD_BOTTOM = 7.15
    _HARD_RIGHT = _CONTENT_RIGHT + 0.1
    bx = max(o["x"] + o["w"] for o in objects)
    by = max(o["y"] + o["h"] for o in objects)
    ox = min(o["x"] for o in objects)
    oy = min(o["y"] for o in objects)
    corr = min(
        (_HARD_BOTTOM - oy) / (by - oy) if by > _HARD_BOTTOM and by > oy else 1.0,
        (_HARD_RIGHT - ox) / (bx - ox) if bx > _HARD_RIGHT and bx > ox else 1.0,
        1.0,
    )
    if corr < 1.0:
        for o in objects:
            o["x"] = round(ox + (o["x"] - ox) * corr, 3)
            o["y"] = round(oy + (o["y"] - oy) * corr, 3)
            o["w"] = round(o["w"] * corr, 3)
            o["h"] = round(o["h"] * corr, 3)

    _fill_horizontal(objects)


def _fill_horizontal(objects: list[dict]) -> None:
    """Consume leftover horizontal slack so the AWS Account/VPC box stretches to
    the right edge of the content band instead of leaving dead space next to it.

    Uniform scaling fits the (tall, narrow) cluster to the band HEIGHT, which
    leaves horizontal slack because the band is wide. We close that slack with a
    horizontal-only stretch about the left origin:
      * CONTAINERS (account/region/vpc/az/subnet/groups) have their x AND width
        scaled -> the boxes get wider (flex fill).
      * LEAF icons keep their square w/h; only their CENTRE x is scaled so they
        stay centred in their now-wider parent without distorting the glyph.
    This preserves containment and aspect ratios while filling the width.
    """
    if not objects:
        return
    ox = min(o["x"] for o in objects)
    bx = max(o["x"] + o["w"] for o in objects)
    cur_w = bx - ox
    if cur_w <= 0:
        return
    target_w = _CONTENT_RIGHT - ox
    hs = target_w / cur_w
    # Only ever stretch (fill), never squeeze; cap so we don't over-stretch.
    if hs <= 1.01:
        return
    hs = min(hs, 1.6)
    for o in objects:
        if o.get("kind") == "ICON_LABEL":
            # Keep the icon square; move its centre to follow the stretch.
            cx = o["x"] + o["w"] / 2
            new_cx = ox + (cx - ox) * hs
            o["x"] = round(new_cx - o["w"] / 2, 3)
        else:
            o["x"] = round(ox + (o["x"] - ox) * hs, 3)
            o["w"] = round(o["w"] * hs, 3)


def build_layout_stack(
    model: dict, slide: dict, absolute_swimlanes: bool = False
) -> dict:
    """Produce a layout-model dict using the recursive stack engine.

    absolute_swimlanes=True applies the Rule 2.2/2.3 post-pass: subnet tiers are
    snapped to locked absolute Y lanes (aligned across AZs) and icons are spaced
    by the dynamic formula. Default False keeps the fractional/scale-fit layout.
    """
    from .routing import route_edges  # local import to avoid cycles

    model = _scrub_spatial(model)
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    roots = _build_forest(nodes)

    # Lay out roots left-to-right (each root is typically the AWS Account).
    for r in roots:
        _measure(r)
    x = 0.0
    max_h = 0.0
    for r in roots:
        r.x = x
        r.y = 0.0
        _place(r)
        x += r.w + GAP
        max_h = max(max_h, r.h)
    diagram_w = max(x - GAP, LEAF_W)
    diagram_h = max(max_h, LEAF_H)

    # Fit into the slide safe area (reserve title + legend strips).
    margin = slide["safeMarginIn"]
    avail_w = slide["widthIn"] - 2 * margin
    avail_h = slide["heightIn"] - 2 * margin - TITLE_STRIP_IN - LEGEND_STRIP_IN
    scale = min(avail_w / diagram_w, avail_h / diagram_h)
    off_x = margin + (avail_w - diagram_w * scale) / 2
    off_y = margin + TITLE_STRIP_IN + (avail_h - diagram_h * scale) / 2

    boxes = _flatten(roots)
    objects = []
    obj_by_id: dict[str, dict] = {}
    for b in boxes:
        obj = {
            "objectId": b.node_id,
            "kind": "CONTAINER" if b.is_container else "ICON_LABEL",
            "x": round(off_x + b.x * scale, 3),
            "y": round(off_y + b.y * scale, 3),
            "w": round(b.w * scale, 3),
            "h": round(b.h * scale, 3),
            "z": b.depth,
            "parentObjectId": b.parent_id,
            "iconKey": icon_key(b.node_type, b.aws_service),
            "label": b.label,
            "nodeType": b.node_type,
        }
        # Carry presentation metadata through for the compiler to render.
        if b.cidr:
            obj["cidr"] = b.cidr
        if b.spec:
            obj["spec"] = b.spec
        objects.append(obj)
        obj_by_id[b.node_id] = obj

    # Rule 2.2/2.3: optional absolute-swimlane post-pass BEFORE routing, so
    # connectors anchor to the final locked positions.
    if absolute_swimlanes:
        _apply_absolute_swimlanes(objects)

    max_depth = max((b.depth for b in boxes), default=0)
    connectors = route_edges(edges, obj_by_id, z=max_depth + 1)

    return {
        "schemaVersion": "1.0",
        "projectId": model.get("projectId", ""),
        "jobId": model.get("jobId", ""),
        "title": model.get("title", ""),
        "slide": slide,
        "objects": objects,
        "connectors": connectors,
        # Carry the model's assumptions through for the compiler to render the
        # 'Key Assumptions' box (Stage E).
        "assumptions": [
            a.get("text", "") for a in (model.get("assumptions") or [])
            if a.get("text")
        ][:5],
    }
