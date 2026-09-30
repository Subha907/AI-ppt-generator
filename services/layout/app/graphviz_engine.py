"""Graphviz-backed layout engine.

Uses the `dot` binary (already installed) to compute professional node
positions, nested cluster (container) bounding boxes, and edge routes that
avoid boxes. We then convert Graphviz's output into the SAME layout-model.json
that the hand-rolled engine produces, so Agent 3 is unaffected.

Coordinate systems:
  * Graphviz works in POINTS (72 pt = 1 inch) with a BOTTOM-LEFT origin.
  * Our layout model is in INCHES with a TOP-LEFT origin.
So we scale by 1/72 and flip Y using the graph's overall height.

Containers become nested `subgraph cluster_*` blocks (Account > Region > VPC >
AZ > subnet); services become nodes; edges become dot edges with labels.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from .icons import CONTAINER_TYPES, icon_key

PT_PER_IN = 72.0

# Fixed node size (inches) for service icons; matches Agent 3's icon+label box.
# Taller than wide so there is always room for the icon PLUS a two-line label
# beneath it (prevents TEXT_CLIPPED from the visual reviewer).
NODE_W_IN = 1.6
NODE_H_IN = 1.45


class GraphvizUnavailable(RuntimeError):
    pass


def _ensure_dot() -> None:
    if shutil.which("dot") is None:
        raise GraphvizUnavailable("Graphviz 'dot' binary not found on PATH")


def _build_tree(nodes: list[dict]) -> tuple[dict, dict, list]:
    """Return (by_id, children_of, roots) for the containment forest."""
    by_id = {n["id"]: n for n in nodes}
    children_of: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    roots: list[str] = []
    for n in nodes:
        pid = n.get("parentId")
        if pid and pid in by_id and pid != n["id"]:
            children_of[pid].append(n["id"])
        else:
            roots.append(n["id"])
    return by_id, children_of, roots


def _esc(text: str) -> str:
    return text.replace('"', '\\"')


def _is_container(node: dict, children_of: dict) -> bool:
    return node.get("type") in CONTAINER_TYPES or bool(children_of.get(node["id"]))


def _emit_leaf(nid: str, by_id: dict, lines: list[str], indent: str) -> None:
    node = by_id[nid]
    lines.append(
        f'{indent}"{nid}" [shape=box,fixedsize=true,'
        f'width={NODE_W_IN},height={NODE_H_IN},label="{_esc(node.get("label", nid))}"];'
    )


def _emit_node_or_cluster(
    nid: str, by_id: dict, children_of: dict, lines: list[str], depth: int
) -> None:
    node = by_id[nid]
    indent = "  " * (depth + 1)
    kids = children_of.get(nid, [])
    if _is_container(node, children_of):
        # Nested cluster. Graphviz requires the name to start with "cluster".
        lines.append(f'{indent}subgraph "cluster_{nid}" {{')
        lines.append(f'{indent}  label="{_esc(node.get("label", nid))}";')
        lines.append(f'{indent}  labeljust="l";')
        lines.append(f'{indent}  style="rounded";')
        lines.append(f'{indent}  margin=12;')
        if not kids:
            # Empty container still needs an invisible anchor node to get a bb.
            lines.append(f'{indent}  "_anchor_{nid}" [style=invis,width=1.2,height=0.8,label=""];')

        # Separate container-children from loose leaf-children.
        container_kids = [k for k in kids if _is_container(by_id[k], children_of)]
        leaf_kids = [k for k in kids if not _is_container(by_id[k], children_of)]

        for k in container_kids:
            _emit_node_or_cluster(k, by_id, children_of, lines, depth + 1)

        # Loose leaves (e.g. account-level IAM/KMS/CloudWatch) are packed into a
        # compact VERTICAL column via invisible ordering edges, so they don't
        # spread horizontally across empty space beside the sub-clusters.
        if leaf_kids:
            if container_kids and len(leaf_kids) > 1:
                # Mixed container + loose leaves (e.g. account has a Region
                # cluster PLUS account-level IAM/KMS/CloudWatch). Wrap the loose
                # leaves in their own borderless sub-cluster so Graphviz packs
                # them as one tight group instead of scattering them across the
                # empty space beside the sub-clusters.
                lines.append(f'{indent}  subgraph "cluster_{nid}__loose" {{')
                lines.append(f'{indent}    style=invis;')
                lines.append(f'{indent}    margin=6;')
                for k in leaf_kids:
                    _emit_leaf(k, by_id, lines, indent + "    ")
                lines.append(f"{indent}  }}")
            else:
                for k in leaf_kids:
                    _emit_leaf(k, by_id, lines, indent + "  ")
        lines.append(f"{indent}}}")
    else:
        _emit_leaf(nid, by_id, lines, indent)


def _find_leaf_descendant(nid: str, by_id: dict, children_of: dict) -> str | None:
    """A concrete (non-cluster) node to anchor edges that reference a container."""
    node = by_id[nid]
    if not _is_container(node, children_of):
        return nid
    for k in children_of.get(nid, []):
        found = _find_leaf_descendant(k, by_id, children_of)
        if found:
            return found
    return f"_anchor_{nid}"  # invisible anchor of an empty container


def build_dot(model: dict) -> str:
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    by_id, children_of, roots = _build_tree(nodes)

    lines = ["digraph arch {"]
    lines.append("  compound=true;")            # allow edges to/from clusters
    lines.append("  rankdir=TB;")               # top-to-bottom (North-South)
    lines.append("  splines=ortho;")            # STRAIGHT right-angle edges
    #                                             (like the AWS sample), no curves
    lines.append("  nodesep=0.6;")
    lines.append("  ranksep=0.9;")
    lines.append('  node [fontname="Arial",fontsize=10];')
    lines.append('  edge [fontname="Arial",fontsize=8];')
    lines.append('  graph [fontname="Arial",fontsize=11];')

    for r in roots:
        _emit_node_or_cluster(r, by_id, children_of, lines, 0)

    # Edges: if an endpoint is a container, attach to a descendant node and use
    # lhead/ltail so Graphviz clips the edge at the cluster boundary.
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src not in by_id or tgt not in by_id:
            continue
        s_node = _find_leaf_descendant(src, by_id, children_of)
        t_node = _find_leaf_descendant(tgt, by_id, children_of)
        attrs = [f'label="{_esc(e.get("label") or "")}"', f'id="{_esc(e["id"])}"']
        if _is_container(by_id[src], children_of):
            attrs.append(f'ltail="cluster_{src}"')
        if _is_container(by_id[tgt], children_of):
            attrs.append(f'lhead="cluster_{tgt}"')
        lines.append(f'  "{s_node}" -> "{t_node}" [{",".join(attrs)}];')

    lines.append("}")
    return "\n".join(lines)


def _run_dot(dot_src: str) -> dict:
    _ensure_dot()
    proc = subprocess.run(
        ["dot", "-Tjson"],
        input=dot_src.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise GraphvizUnavailable(f"dot failed: {proc.stderr.decode('utf-8', 'ignore')}")
    return json.loads(proc.stdout.decode("utf-8"))


def _graph_bb(gv: dict) -> tuple[float, float]:
    """Overall graph bounding box (width_pt, height_pt) from the 'bb' attr."""
    bb = gv.get("bb", "0,0,100,100")
    _, _, w, h = (float(v) for v in bb.split(","))
    return w, h


def build_layout_graphviz(model: dict, slide: dict) -> dict:
    """Produce a layout-model dict using Graphviz for placement + routing."""
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    by_id, children_of, _ = _build_tree(nodes)

    dot_src = build_dot(model)
    gv = _run_dot(dot_src)

    graph_w_pt, graph_h_pt = _graph_bb(gv)

    def to_in(x_pt: float, y_pt: float) -> tuple[float, float]:
        # points -> inches, flip Y (Graphviz bottom-left -> our top-left).
        return x_pt / PT_PER_IN, (graph_h_pt - y_pt) / PT_PER_IN

    objects: list[dict] = []
    obj_index: dict[str, int] = {}

    # 1. Service nodes (objects list from gv["objects"] that are real nodes).
    gv_objects = gv.get("objects", [])
    # Map Graphviz internal names to entries; clusters carry "bb", nodes "pos".
    for o in gv_objects:
        name = o.get("name", "")
        if name.startswith("cluster_"):
            nid = name[len("cluster_"):]
            node = by_id.get(nid)
            if not node:
                continue
            bb = o.get("bb")
            if not bb:
                continue
            x1, y1, x2, y2 = (float(v) for v in bb.split(","))
            # Convert both corners; flipped Y means y2 becomes the top.
            gx1, gy_top = to_in(x1, y2)
            gx2, gy_bot = to_in(x2, y1)
            obj = {
                "objectId": nid,
                "kind": "CONTAINER",
                "x": round(gx1, 3), "y": round(gy_top, 3),
                "w": round(gx2 - gx1, 3), "h": round(gy_bot - gy_top, 3),
                "z": 0,  # set by depth below
                "parentObjectId": node.get("parentId"),
                "iconKey": None,
                "label": node.get("label", nid),
                "nodeType": node.get("type", "CONTAINER"),
            }
            obj_index[nid] = len(objects)
            objects.append(obj)
        else:
            nid = name
            node = by_id.get(nid)
            if not node or _is_container(node, children_of):
                continue
            pos = o.get("pos")
            if not pos:
                continue
            cx_pt, cy_pt = (float(v) for v in pos.split(","))
            w_in = float(o.get("width", NODE_W_IN))
            h_in = float(o.get("height", NODE_H_IN))
            cx_in, cy_in = to_in(cx_pt, cy_pt)
            obj = {
                "objectId": nid,
                "kind": "ICON_LABEL",
                "x": round(cx_in - w_in / 2, 3), "y": round(cy_in - h_in / 2, 3),
                "w": round(w_in, 3), "h": round(h_in, 3),
                "z": 100,
                "parentObjectId": node.get("parentId"),
                "iconKey": icon_key(node.get("type", "COMPONENT"),
                                    (node.get("properties") or {}).get("awsService")),
                "label": node.get("label", nid),
                "nodeType": node.get("type", "COMPONENT"),
            }
            obj_index[nid] = len(objects)
            objects.append(obj)

    # z-order by container depth (outer behind).
    def depth(nid: str) -> int:
        d, cur = 0, by_id.get(nid)
        while cur and cur.get("parentId") in by_id:
            d += 1
            cur = by_id[cur["parentId"]]
        return d

    for obj in objects:
        if obj["kind"] == "CONTAINER":
            obj["z"] = depth(obj["objectId"])
    max_depth = max((depth(o["objectId"]) for o in objects), default=0)

    # 2. Edges with routed spline points. Skip the invisible ordering edges we
    #    injected to stack loose leaves (those carry no real architecture id).
    real_edge_ids = {e["id"] for e in edges if e.get("id")}
    connectors = []
    for e in gv.get("edges", []):
        eid = e.get("id")
        if eid not in real_edge_ids:
            continue  # invisible ordering/layout edge
        pos = e.get("pos", "")
        pts = _parse_edge_pos(pos, to_in)
        if len(pts) < 2:
            continue
        connectors.append({
            "edgeId": eid,
            "sourceObjectId": _edge_endpoint_id(e, gv_objects, "tail", by_id, children_of),
            "targetObjectId": _edge_endpoint_id(e, gv_objects, "head", by_id, children_of),
            "route": "SPLINE",
            "z": max_depth + 1,
            "label": _edge_label(e),
            "points": pts,
        })

    # 3. Scale + center into the slide safe area.
    _fit_to_slide(objects, connectors, slide)

    return {
        "schemaVersion": "1.0",
        "projectId": model.get("projectId", ""),
        "jobId": model.get("jobId", ""),
        "title": model.get("title", ""),
        "slide": slide,
        "objects": objects,
        "connectors": connectors,
    }


def _edge_label(e: dict) -> str | None:
    return e.get("label") or None


def _edge_endpoint_id(e, gv_objects, which, by_id, children_of) -> str:
    """Resolve the logical node id for an edge endpoint (gvid -> name)."""
    gvid = e.get("tail" if which == "tail" else "head")
    try:
        name = gv_objects[int(gvid)].get("name", "")
    except (ValueError, IndexError, TypeError):
        name = ""
    if name.startswith("_anchor_"):
        return name[len("_anchor_"):]
    return name


def _parse_edge_pos(pos: str, to_in) -> list[dict]:
    """Parse Graphviz edge 'pos' spline into a list of {x,y} inch points."""
    if not pos:
        return []
    tokens = pos.replace("\n", " ").split()
    pts: list[dict] = []
    end_pt = None
    for tok in tokens:
        if tok.startswith("e,"):
            end_pt = tok[2:]
            continue
        if tok.startswith("s,"):
            tok = tok[2:]
        if "," in tok:
            try:
                xs, ys = tok.split(",")
                x_in, y_in = to_in(float(xs), float(ys))
                pts.append({"x": round(x_in, 3), "y": round(y_in, 3)})
            except ValueError:
                continue
    if end_pt:
        try:
            xs, ys = end_pt.split(",")
            x_in, y_in = to_in(float(xs), float(ys))
            pts.append({"x": round(x_in, 3), "y": round(y_in, 3)})
        except ValueError:
            pass
    return pts


def _fit_to_slide(objects: list[dict], connectors: list[dict], slide: dict) -> None:
    """Scale + center objects and connector points into the slide safe area."""
    if not objects:
        return
    margin = slide["safeMarginIn"]
    title_strip = 0.55
    legend_strip = 0.45
    min_x = min(o["x"] for o in objects)
    min_y = min(o["y"] for o in objects)
    max_x = max(o["x"] + o["w"] for o in objects)
    max_y = max(o["y"] + o["h"] for o in objects)
    diagram_w = max(max_x - min_x, 0.1)
    diagram_h = max(max_y - min_y, 0.1)

    avail_w = slide["widthIn"] - 2 * margin
    avail_h = slide["heightIn"] - 2 * margin - title_strip - legend_strip
    scale = min(avail_w / diagram_w, avail_h / diagram_h)
    off_x = margin + (avail_w - diagram_w * scale) / 2
    off_y = margin + title_strip + (avail_h - diagram_h * scale) / 2

    def tx(x: float) -> float:
        return round(off_x + (x - min_x) * scale, 3)

    def ty(y: float) -> float:
        return round(off_y + (y - min_y) * scale, 3)

    for o in objects:
        o["w"] = round(o["w"] * scale, 3)
        o["h"] = round(o["h"] * scale, 3)
        o["x"] = tx(o["x"])
        o["y"] = ty(o["y"])
    for c in connectors:
        c["points"] = [{"x": tx(p["x"]), "y": ty(p["y"])} for p in c["points"]]
