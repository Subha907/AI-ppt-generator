"""python-pptx compiler: build an EDITABLE draft.pptx from a layout model.

Everything is a native PowerPoint shape (rounded rectangles, text boxes,
connectors) - nothing is flattened to an image - so the output remains fully
editable in PowerPoint (PDF requirement).

Build order (PDF 11.1):
  1. Create presentation, set 16:9 dimensions.
  2. Add a blank slide.
  3. Draw containers in z-order (outer first), then nested.
  4. Add service icons + labels.
  5. Draw connectors from precomputed orthogonal routes.
  6. Add title and legend; apply theme colors/fonts.
"""
from __future__ import annotations

import io

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from . import theme
from .iconstore import get_icon_stream

# python-pptx uses EMUs; 914400 EMU per inch. Inches() handles the conversion.
_EMU_PER_IN = 914400


def _rgb(hex_str: str) -> RGBColor:
    return RGBColor(*theme.hex_to_rgb(hex_str))


def _is_container(obj: dict) -> bool:
    return obj.get("kind") == "CONTAINER"


def _set_dash(line, val: str = "dash") -> None:
    """Set a shape/connector outline dash pattern: 'dash', 'dot', or 'solid'."""
    ln = line._get_or_add_ln()
    for existing in ln.findall(qn("a:prstDash")):
        ln.remove(existing)
    if val and val != "solid":
        d = ln.makeelement(qn("a:prstDash"), {"val": val})
        ln.append(d)


def _set_dashed(line) -> None:
    """Backwards-compatible: set a dashed outline."""
    _set_dash(line, "dash")


def _fit_label(text: str, max_chars: int = 22) -> str:
    """Keep leaf labels short enough to render inside a small icon box.

    Drops a leading 'Amazon '/'AWS ' vendor prefix first (still recognizable),
    then truncates with an ellipsis if still too long.
    """
    t = text.strip()
    if len(t) > max_chars:
        for prefix in ("Amazon ", "AWS "):
            if t.startswith(prefix):
                t = t[len(prefix):]
                break
    if len(t) > max_chars:
        t = t[: max_chars - 1].rstrip() + "\u2026"
    return t


def _add_container(slide, obj: dict) -> None:
    label = obj.get("label") or obj["objectId"]
    st = theme.container_style(obj.get("nodeType", "CONTAINER"), label)

    # The group box: white/tinted interior, colored border (dashed for
    # Region/AZ per AWS convention), no shadow.
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(obj["x"]), Inches(obj["y"]), Inches(obj["w"]), Inches(obj["h"]),
    )
    shape.name = f"container:{obj['objectId']}"
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(st.fill)
    shape.line.color.rgb = _rgb(st.border)
    shape.line.width = Pt(getattr(st, "border_width", 1.5))
    if st.dashed:
        _set_dashed(shape.line)
    shape.shadow.inherit = False
    shape.text_frame.word_wrap = False
    shape.text_frame.auto_size = MSO_AUTO_SIZE.NONE

    # AWS-style label strip: a small filled tab at the top-left holding the
    # group name (like the group icons' colored label). Fixed height, single
    # line, so it can never wrap or overflow the group border.
    strip_h = 0.26
    strip_w = min(max(len(label) * 0.085 + 0.14, 0.7), obj["w"])
    strip = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(obj["x"]), Inches(obj["y"]), Inches(strip_w), Inches(strip_h),
    )
    strip.name = f"container-title:{obj['objectId']}"
    strip.fill.solid()
    strip.fill.fore_color.rgb = _rgb(st.title_fill)
    strip.line.fill.background()
    strip.shadow.inherit = False
    tf = strip.text_frame
    tf.word_wrap = False
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Emu(45000)
    tf.margin_right = Emu(20000)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = label
    run.font.size = Pt(9)
    run.font.bold = False
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(st.title_text)


ICON_SIZE_IN = 0.5    # Ruleset section 6: fixed 0.5in icon square
LABEL_BOX_W = 1.0     # label box 1.0in wide, offset -0.25 to center under icon
LABEL_GAP = 0.05      # 0.05in directly below the icon


def _add_leaf(slide, obj: dict) -> None:
    st = theme.leaf_style(obj.get("nodeType", "COMPONENT"))
    # Ruleset section 6: the icon is a fixed 0.5in square, horizontally centered
    # in the object's allotted box; the label sits in an independent 1.0in-wide
    # text frame 0.05in below the icon, offset -0.25in to stay centered.
    icon_w = icon_h = min(ICON_SIZE_IN, obj["w"], obj["h"] * 0.6)
    icon_x = obj["x"] + (obj["w"] - icon_w) / 2
    icon_y = obj["y"]
    icon_cx = icon_x + icon_w / 2

    stream = get_icon_stream(obj.get("iconKey"))
    if stream is not None:
        pic = slide.shapes.add_picture(
            stream, Inches(icon_x), Inches(icon_y), Inches(icon_w), Inches(icon_h)
        )
        pic.name = f"icon:{obj['objectId']}"
    else:
        icon = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(icon_x), Inches(icon_y), Inches(icon_w), Inches(icon_h),
        )
        icon.name = f"icon:{obj['objectId']}"
        icon.fill.solid()
        icon.fill.fore_color.rgb = _rgb(st.fill)
        icon.line.color.rgb = _rgb(st.border)
        icon.line.width = Pt(1.0)
        icon.shadow.inherit = False
        icon.text_frame.word_wrap = False
        icon.text_frame.auto_size = MSO_AUTO_SIZE.NONE

    # Label: independent text frame, 1.0in wide centered under the icon center,
    # 0.05in below the icon baseline. Word-wrap on, zero margins, 8pt Arial.
    label_w = min(LABEL_BOX_W, obj["w"] + 0.5)
    label_x = icon_cx - label_w / 2
    label_y = icon_y + icon_h + LABEL_GAP
    label_h = max(obj["y"] + obj["h"] - label_y, 0.24)
    lbl = slide.shapes.add_textbox(
        Inches(label_x), Inches(label_y), Inches(label_w), Inches(label_h),
    )
    lbl.name = f"icon-label:{obj['objectId']}"
    tf = lbl.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = _fit_label(obj.get("label") or obj["objectId"])
    run.font.size = Pt(8)  # Ruleset section 6: 8-9pt
    run.font.bold = False
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(theme.TITLE_COLOR)


def _set_arrowhead(line, at_end: bool = True) -> None:
    """Add an OPEN arrowhead to a connector line end (AWS convention)."""
    ln = line._get_or_add_ln()
    tag = "a:tailEnd" if at_end else "a:headEnd"
    for existing in ln.findall(qn(tag)):
        ln.remove(existing)
    end = ln.makeelement(qn(tag), {"type": "stealth", "w": "med", "len": "med"})
    ln.append(end)


def _add_connector(slide, conn: dict) -> None:
    """Pass 4: draw the connector line(s) only (no label). Style by edge class
    per Ruleset section 7."""
    pts = conn.get("points") or []
    if len(pts) < 2:
        return
    cst = theme.connector_style(conn.get("edgeClass", "request"))
    color = _rgb(cst.color)
    segments = list(zip(pts, pts[1:]))
    last = len(segments) - 1
    for i, (a, b) in enumerate(segments):
        c = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Inches(a["x"]), Inches(a["y"]), Inches(b["x"]), Inches(b["y"]),
        )
        c.name = f"conn:{conn['edgeId']}"
        c.line.color.rgb = color
        c.line.width = Pt(cst.width)
        if cst.dash != "solid":
            _set_dash(c.line, cst.dash)
        # Direction of data flow: open arrowhead only on the final segment.
        if i == last:
            _set_arrowhead(c.line, at_end=True)


def _add_connector_label(slide, conn: dict) -> None:
    """Pass 5: stamp the connector label on top of the line."""
    pts = conn.get("points") or []
    if len(pts) < 2:
        return
    cst = theme.connector_style(conn.get("edgeClass", "request"))
    label = conn.get("label")
    if label:
        mid = pts[len(pts) // 2]
        lw, lh = 1.1, 0.2
        tb = slide.shapes.add_textbox(
            Inches(mid["x"] - lw / 2), Inches(mid["y"] - lh - 0.02),
            Inches(lw), Inches(lh),
        )
        tb.name = f"conn-label:{conn['edgeId']}"
        tf = tb.text_frame
        tf.word_wrap = False
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.margin_left = Emu(0)
        tf.margin_right = Emu(0)
        tf.margin_top = Emu(0)
        tf.margin_bottom = Emu(0)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = _fit_label(label, max_chars=16)
        run.font.size = Pt(6)
        run.font.italic = True  # AWS: italics preferred over underline on flows
        run.font.name = theme.FONT_NAME
        run.font.color.rgb = _rgb(cst.color)


def _add_title(slide, title: str, slide_w_in: float) -> None:
    box = slide.shapes.add_textbox(
        Inches(0.3), Inches(0.06), Inches(slide_w_in - 0.6), Inches(0.4)
    )
    box.name = "slide-title"
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = title or "Architecture"
    run.font.size = Pt(16)
    run.font.bold = True
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(theme.TITLE_COLOR)


def _add_legend(slide, slide_w_in: float, slide_h_in: float) -> None:
    """A small legend mapping the main node-type colors."""
    entries = [
        ("Compute", theme.LEAF_STYLES["COMPUTE"].fill),
        ("Database", theme.LEAF_STYLES["DATABASE"].fill),
        ("Storage", theme.LEAF_STYLES["STORAGE"].fill),
        ("Network", theme.LEAF_STYLES["NETWORK"].fill),
        ("Security", theme.LEAF_STYLES["SECURITY"].fill),
    ]
    sw = 0.16
    gap = 0.02
    box_w = 1.15
    x = 0.3
    y = slide_h_in - 0.34
    container = slide.shapes.add_textbox(Inches(x), Inches(y - 0.04), Inches(0.9), Inches(0.28))
    container.name = "legend-title"
    tp = container.text_frame.paragraphs[0]
    r = tp.add_run()
    r.text = "Legend:"
    r.font.size = Pt(8)
    r.font.bold = True
    r.font.name = theme.FONT_NAME
    r.font.color.rgb = _rgb(theme.TITLE_COLOR)

    cursor = x + 0.85
    for label, fill in entries:
        swatch = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(cursor), Inches(y), Inches(sw), Inches(sw)
        )
        swatch.name = f"legend-swatch:{label}"
        swatch.fill.solid()
        swatch.fill.fore_color.rgb = _rgb(fill)
        swatch.line.color.rgb = _rgb(theme.TITLE_COLOR)
        swatch.line.width = Pt(0.5)
        swatch.shadow.inherit = False

        tb = slide.shapes.add_textbox(
            Inches(cursor + sw + gap), Inches(y - 0.03), Inches(box_w - sw), Inches(0.24)
        )
        tp = tb.text_frame.paragraphs[0]
        rr = tp.add_run()
        rr.text = label
        rr.font.size = Pt(8)
        rr.font.name = theme.FONT_NAME
        rr.font.color.rgb = _rgb(theme.TITLE_COLOR)
        cursor += box_w + 0.1


# Ruleset section 3: scaffolding draw order (deepest background first).
_SCAFFOLD_ORDER = {
    "ACCOUNT": 0, "REGION": 1, "VPC": 2, "CONTAINER": 3,
    "AVAILABILITYZONE": 4, "SUBNET": 5,
}


def _add_container_box(slide, obj: dict) -> None:
    """Pass 1: the container box only (no title strip)."""
    st = theme.container_style(obj.get("nodeType", "CONTAINER"), obj.get("label") or "")
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(obj["x"]), Inches(obj["y"]), Inches(obj["w"]), Inches(obj["h"]),
    )
    shape.name = f"container:{obj['objectId']}"
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(st.fill)
    shape.line.color.rgb = _rgb(st.border)
    shape.line.width = Pt(getattr(st, "border_width", 1.5))
    if st.dashed:
        _set_dash(shape.line, "dash")
    shape.shadow.inherit = False
    shape.text_frame.word_wrap = False
    shape.text_frame.auto_size = MSO_AUTO_SIZE.NONE


def _add_container_title(slide, obj: dict) -> None:
    """Pass 2: the AWS-style label strip at the container's top-left."""
    label = obj.get("label") or obj["objectId"]
    st = theme.container_style(obj.get("nodeType", "CONTAINER"), label)
    strip_h = 0.26
    strip_w = min(max(len(label) * 0.085 + 0.14, 0.7), obj["w"])
    strip = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(obj["x"]), Inches(obj["y"]), Inches(strip_w), Inches(strip_h),
    )
    strip.name = f"container-title:{obj['objectId']}"
    strip.fill.solid()
    strip.fill.fore_color.rgb = _rgb(st.title_fill)
    strip.line.fill.background()
    strip.shadow.inherit = False
    tf = strip.text_frame
    tf.word_wrap = False
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Emu(45000)
    tf.margin_right = Emu(20000)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = label
    run.font.size = Pt(9)
    run.font.bold = False
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(st.title_text)


def compile_pptx(layout: dict) -> bytes:
    """Build the editable PPTX in 5 strict passes (Ruleset section 3)."""
    slide_def = layout["slide"]
    w_in = slide_def["widthIn"]
    h_in = slide_def["heightIn"]

    prs = Presentation()
    prs.slide_width = Inches(w_in)
    prs.slide_height = Inches(h_in)
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

    objects = layout.get("objects", [])
    containers = [o for o in objects if _is_container(o)]
    leaves = [o for o in objects if not _is_container(o)]
    connectors = layout.get("connectors", [])

    # Pass 1 - deepest background: scaffolding boxes in strict type order,
    # then by z within a type (outer to inner).
    def scaffold_key(o: dict):
        return (
            _SCAFFOLD_ORDER.get(o.get("nodeType", "CONTAINER"), 3),
            o.get("z", 0),
        )
    for obj in sorted(containers, key=scaffold_key):
        _add_container_box(slide, obj)

    # Pass 2 - container typography (title strips) on top of the boxes.
    for obj in sorted(containers, key=scaffold_key):
        _add_container_title(slide, obj)

    # Pass 3 - active elements: icon + label nodes.
    for obj in leaves:
        _add_leaf(slide, obj)

    # Pass 4 - connector overlays (lines only), above all structure.
    for conn in connectors:
        _add_connector(slide, conn)

    # Pass 5 - topmost labels: connector labels + slide title + legend.
    for conn in connectors:
        _add_connector_label(slide, conn)
    _add_title(slide, layout.get("title", ""), w_in)
    _add_legend(slide, w_in, h_in)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
