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


def _auto_group_loose_services(nodes: list[dict]) -> list[dict]:
    """Safety net: if Agent 1 left services as direct children of a REGION or
    ACCOUNT (no group container), synthesize category group CONTAINERs and
    reparent those services into them. Guarantees a clean grouped layout
    regardless of LLM variability. Services already inside a subnet/VPC/group
    are left untouched.
    """
    by_id = {n["id"]: n for n in nodes}
    # A parent is a "bare" structural container (services here should be
    # grouped into a labeled box rather than floating).
    bare_parent_types = {"REGION", "ACCOUNT", "VPC"}
    new_groups: dict[tuple[str, str], dict] = {}  # (parentId, groupKey) -> node
    changed = False

    for n in nodes:
        if n.get("type") in _CONTAINER_TYPES_LOCAL:
            continue
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
        by_id[n["id"]] = Box(
            node_id=n["id"],
            node_type=n.get("type", "COMPONENT"),
            label=n.get("label", n["id"]),
            aws_service=(n.get("properties") or {}).get("awsService"),
            parent_id=n.get("parentId"),
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
_ABS_LEFT = 0.95         # left edge of the VPC cluster (account+region to its left)
_ABS_RIGHT = 12.5        # right edge available to child content
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

    # --- 1. VPC cluster occupies the left column in absolute coords ----------
    usable_w = _ABS_RIGHT - _ABS_LEFT
    vpc_x = _ABS_LEFT                # account/region wrappers grow to its left
    vpc_w = usable_w * _VPC_WIDTH_FRAC
    vpc_top = _LANE_TOP - 0.55       # room for VPC + AZ title strips above lanes
    vpc_bottom = _LANE_BOTTOM + 0.25
    vpc["x"], vpc["w"] = round(vpc_x, 3), round(vpc_w, 3)
    vpc["y"], vpc["h"] = round(vpc_top, 3), round(vpc_bottom - vpc_top, 3)

    # --- 2. AZs as equal-width columns spanning all lanes --------------------
    n_az = len(azs)
    az_top = _LANE_TOP - 0.3
    az_bottom = _LANE_BOTTOM + 0.15
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
            lane = _SWIMLANE[role]
            sn["x"], sn["w"] = round(sub_x, 3), round(sub_w, 3)
            sn["y"], sn["h"] = lane["y"], lane["h"]

            # --- 4. Rule 2.3: dynamic icon spacing inside the subnet ---------
            icons = [c for c in kids if c.get("kind") == "ICON_LABEL"]
            n = len(icons)
            if n == 0:
                continue
            spacing = max(0.05, (sub_w - n * ICON_SIZE) / (n + 1))
            icon_y = sn["y"] + (sn["h"] - ICON_SIZE) / 2
            for j, ic in enumerate(icons):
                ic["x"] = round(sub_x + spacing + j * (ICON_SIZE + spacing), 3)
                ic["y"] = round(icon_y, 3)
                ic["w"], ic["h"] = ICON_SIZE, ICON_SIZE

    # --- 5. Reflow sibling group CONTAINERs into the right-hand column -------
    region = next((o for o in objects if o.get("nodeType") == "REGION"), None)
    region_id = region["objectId"] if region else vpc.get("parentObjectId")
    groups = [c for c in children_of.get(region_id, [])
              if c.get("nodeType") == "CONTAINER"
              and c["objectId"] != vpc["objectId"]]
    if groups:
        # Leave a wide gap between the VPC and the group column so the
        # cross-column routing corridor has room for parallel vertical tracks
        # (remediation: EXCESSIVE_CROSSINGS). ~1.4in holds ~16 tracks legibly.
        col_x = vpc_x + vpc_w + 1.4
        col_w = _ABS_RIGHT - col_x
        n_g = len(groups)
        col_top = _LANE_TOP - 0.3
        col_h_total = _LANE_BOTTOM + 0.15 - col_top
        g_h = (col_h_total - 0.3 * (n_g - 1)) / n_g
        for i, g in enumerate(groups):
            g_y = col_top + i * (g_h + 0.3)
            old_x, old_y, old_w = g["x"], g["y"], g["w"]
            g["x"], g["w"] = round(col_x, 3), round(col_w, 3)
            g["y"], g["h"] = round(g_y, 3), round(g_h, 3)
            # Shift the group's icon children to follow the box (keep relative
            # offset within the box, then re-space horizontally if they overflow).
            gkids = [c for c in children_of.get(g["objectId"], [])
                     if c.get("kind") == "ICON_LABEL"]
            ng = len(gkids)
            if ng:
                sp = max(0.05, (col_w - ng * ICON_SIZE) / (ng + 1))
                icy = g_y + (g_h - ICON_SIZE) / 2
                for j, ic in enumerate(gkids):
                    ic["x"] = round(col_x + sp + j * (ICON_SIZE + sp), 3)
                    ic["y"] = round(icy, 3)
                    ic["w"], ic["h"] = ICON_SIZE, ICON_SIZE

    # --- 6. Grow Account/Region wrappers to contain everything (Rule 8) ------
    cluster = [vpc] + azs + groups if groups else [vpc] + azs
    for wrapper_type, inset in (("REGION", 0.12), ("ACCOUNT", 0.15)):
        w = next((o for o in objects if o.get("nodeType") == wrapper_type), None)
        if not w:
            continue
        left = min(c["x"] for c in cluster) - inset
        top = min(c["y"] for c in cluster) - inset - 0.3  # title strip
        right = max(c["x"] + c["w"] for c in cluster) + inset
        bottom = max(c["y"] + c["h"] for c in cluster) + inset
        w["x"], w["y"] = round(left, 3), round(top, 3)
        w["w"], w["h"] = round(right - left, 3), round(bottom - top, 3)
        cluster = cluster + [w]


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
    }
