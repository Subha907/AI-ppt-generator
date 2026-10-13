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
from .iconstore import get_icon_stream, get_template_stream

# python-pptx uses EMUs; 914400 EMU per inch. Inches() handles the conversion.
_EMU_PER_IN = 914400


def _rgb(hex_str: str) -> RGBColor:
    return RGBColor(*theme.hex_to_rgb(hex_str))


import re as _re

_CIDR_RE = _re.compile(r"\s*\(?\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\)?")


def _title_with_cidr(label: str, cidr: str | None) -> str:
    """Build a container title with the CIDR rendered inline exactly once.

    Agent 1 often embeds the CIDR in the label itself (as a second line, e.g.
    'Public Subnet AZ1\\n10.0.1.0/24') AND supplies a separate `cidr` field.
    Strip any CIDR already present in the label, collapse the newline, then
    append a single parenthesized CIDR so we never duplicate it.
    """
    base = (label or "").replace("\n", " ").strip()
    base = _CIDR_RE.sub("", base).strip()
    if cidr:
        return f"{base} ({cidr})"
    return base


# --- Right-hand description-panel column -----------------------------------
# All text/description boxes (Key Assumptions, Request Flow, Legend) live in a
# single right-hand column, stacked top-to-bottom and sized dynamically to
# their content. The diagram body (Agent 2) reserves this strip so nothing
# overlaps it.
PANEL_W = 2.65          # panel width (inches)
PANEL_RIGHT_MARGIN = 0.15
PANEL_TOP = 0.55        # first panel top, below the slide title
PANEL_GAP = 0.16        # vertical gap between stacked panels
_PANEL_CHARS_PER_IN = 10.5   # ~chars that fit per inch at 7pt body text


def panel_x(slide_w_in: float) -> float:
    return slide_w_in - PANEL_W - PANEL_RIGHT_MARGIN


def _wrap_count(text: str, width_in: float, font_pt: float = 7.0) -> int:
    """Estimate how many wrapped lines `text` needs in a box `width_in` wide.

    Uses an average char-per-inch heuristic scaled by font size so panel
    heights grow with their content (dynamic sizing) without a rendering pass.
    """
    usable = max(width_in - 0.18, 0.5)
    cpl = max(8, int(usable * _PANEL_CHARS_PER_IN * (7.0 / font_pt)))
    words = (text or "").split()
    if not words:
        return 1
    lines, cur = 0, ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) <= cpl:
            cur = trial
        else:
            lines += 1
            cur = w
    if cur:
        lines += 1
    return max(1, lines)


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
    t = " ".join((text or "").strip().split())  # collapse any newlines
    # Drop a leading vendor prefix for compactness (still recognizable), but do
    # NOT ellipsize - the label box is word-wrapped and sized for two lines, so
    # full captions render legibly instead of being cut with '...'.
    for prefix in ("Amazon ", "AWS "):
        if t.startswith(prefix) and len(t) > max_chars:
            t = t[len(prefix):]
            break
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
LABEL_BOX_W = 1.45    # wide enough for a full two-line caption under the icon
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
    # Use the full label-box width (centered on the icon) so captions wrap to
    # two lines instead of truncating. Allow enough height for two lines + spec.
    # Clamp the caption box to the per-icon slot width (set by the layout when
    # a lane holds multiple icons) so adjacent captions never overlap. Falls
    # back to the generous default when no slot constraint is provided.
    label_w = min(LABEL_BOX_W, obj.get("labelMaxW", LABEL_BOX_W))
    label_x = icon_cx - label_w / 2
    label_y = icon_y + icon_h + LABEL_GAP
    label_h = max(obj["y"] + obj["h"] - label_y, 0.42)
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

    # Component spec sub-text (muted grey) on a second line under the label,
    # e.g. 'm6i.xlarge | Auto Scaling 2-4'. Professional deployment metadata.
    spec = obj.get("spec")
    if spec:
        sp = tf.add_paragraph()
        sp.alignment = PP_ALIGN.CENTER
        srun = sp.add_run()
        srun.text = _fit_label(spec, max_chars=26)
        srun.font.size = Pt(6)
        srun.font.italic = True
        srun.font.name = theme.FONT_NAME
        srun.font.color.rgb = _rgb(theme.CONNECTOR_COLOR)


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
    """Pass 5: stamp the connector label on top of the line.

    Security-class edges (encryption / secrets / IAM) are short, dense, intra-
    subnet links; their labels pile up and collide, so we draw those edges
    without a text label to keep the busy lanes readable.
    """
    pts = conn.get("points") or []
    if len(pts) < 2:
        return
    # Inline connector labels (egress / HTTP / Logs & Metrics / etc.) pile up on
    # top of icons and subnet titles in a dense multi-AZ diagram. The step-by-
    # step narrative lives in the right-hand Request Flow rail instead, so the
    # ONLY inline label we keep is the dashed DB replication note.
    if conn.get("edgeClass") != "replication":
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
        Inches(0.3), Inches(0.08), Inches(slide_w_in - 0.6), Inches(0.42)
    )
    box.name = "slide-title"
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT     # left-aligned bold deep-blue header (spec)
    run = p.add_run()
    run.text = title or "Architecture"
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(theme.TITLE_ACCENT)


def _add_legend(slide, slide_w_in: float, top: float) -> float:
    """Legend panel (right column): node-type color key, one entry per row.

    Rendered as a titled box at the given `top`, sized dynamically to the entry
    count. Returns the panel's bottom Y so the next panel can stack below it.
    """
    entries = [
        ("Compute", theme.LEAF_STYLES["COMPUTE"].fill),
        ("Database", theme.LEAF_STYLES["DATABASE"].fill),
        ("Storage", theme.LEAF_STYLES["STORAGE"].fill),
        ("Network", theme.LEAF_STYLES["NETWORK"].fill),
        ("Security", theme.LEAF_STYLES["SECURITY"].fill),
    ]
    x = panel_x(slide_w_in)
    row_h = 0.22
    pad_top = 0.38     # room for the colored header bar
    pad_bottom = 0.08
    ch = pad_top + len(entries) * row_h + pad_bottom

    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(top), Inches(PANEL_W), Inches(ch)
    )
    box.name = "legend"
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb("FFFFFF")
    box.line.color.rgb = _rgb(theme.CONNECTOR_COLOR)
    box.line.width = Pt(0.75)
    box.shadow.inherit = False
    _blank_tf(box)
    _panel_header(slide, x, top, "Legend")

    sw = 0.16
    ry = top + pad_top
    for label, fill in entries:
        swatch = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x + 0.12), Inches(ry), Inches(sw), Inches(sw)
        )
        swatch.name = f"legend-swatch:{label}"
        swatch.fill.solid()
        swatch.fill.fore_color.rgb = _rgb(fill)
        swatch.line.color.rgb = _rgb(theme.TITLE_COLOR)
        swatch.line.width = Pt(0.5)
        swatch.shadow.inherit = False

        tb = slide.shapes.add_textbox(
            Inches(x + 0.12 + sw + 0.06), Inches(ry - 0.02), Inches(PANEL_W - 0.5), Inches(0.2)
        )
        tp = tb.text_frame.paragraphs[0]
        rr = tp.add_run()
        rr.text = label
        rr.font.size = Pt(8)
        rr.font.name = theme.FONT_NAME
        rr.font.color.rgb = _rgb(theme.TITLE_COLOR)
        ry += row_h
    return top + ch


def _blank_tf(shape) -> None:
    """Clear a shape's text frame so the box renders as an empty panel."""
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)


def _flow_steps(layout: dict) -> list[tuple[str, str, str | None]]:
    """Derive ordered (title, description, iconKey) request-flow steps from the
    primary 'request'-class connectors. Title = the step's target component;
    description = the source + edge label phrased as a short sentence; iconKey =
    the target node's AWS service icon, drawn inside the step's blue badge."""
    objects = layout.get("objects", [])
    label_of = {o["objectId"]: _oneline_c(o.get("label") or o["objectId"])
                for o in objects}
    icon_of = {o["objectId"]: o.get("iconKey") for o in objects}
    connectors = layout.get("connectors", [])
    flow = [c for c in connectors if c.get("edgeClass", "request") == "request"
            and len(c.get("points") or []) >= 2]
    steps: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for c in flow:
        tid = c.get("targetObjectId")
        # De-duplicate by target: the same component reached from several
        # sources (e.g. an ALB fed by 3 actors) should appear ONCE in the rail.
        key = (tid or "") + "|" + _oneline_c(label_of.get(tid, ""))
        if key in seen:
            continue
        seen.add(key)
        src = label_of.get(c.get("sourceObjectId"), "")
        tgt = label_of.get(tid, "")
        title = _fit_label(tgt, max_chars=28) or "Step"
        lbl = c.get("label")
        desc = f"From {src}" + (f" \u2014 {lbl}" if lbl else "")
        steps.append((title, desc, icon_of.get(tid)))
        if len(steps) >= 8:
            break
    return steps


def _oneline_c(text: str) -> str:
    return " ".join((text or "").split())


def _add_flow_callouts(slide, layout: dict, slide_w_in: float, top: float,
                       bottom: float | None = None) -> float:
    """Request Flow rail (right column): a colored header bar, then one row per
    step with the step's AWS service icon inside a solid blue circular badge, a
    bold step title, and a wrapped description line.

    When `bottom` is given, the rail STRETCHES to fill from `top` down to
    `bottom` (full-height right sidebar), distributing the rows evenly so the
    bottom-right dead space is eliminated. Returns the panel's bottom Y."""
    steps = _flow_steps(layout)
    if not steps:
        return top

    x = panel_x(slide_w_in)
    header_h = 0.30
    badge_d = 0.30
    title_h = 0.17
    desc_line_h = 0.145
    text_x_off = 0.5          # text starts right of the icon badge
    body_w = PANEL_W - text_x_off - 0.1

    # Natural (minimum) row heights from wrapped descriptions.
    row_heights: list[float] = []
    for _title, desc, _ik in steps:
        nlines = _wrap_count(desc, body_w, 7.0)
        row_heights.append(max(badge_d, title_h + nlines * desc_line_h))
    natural = header_h + 0.1 + sum(row_heights) + 0.1

    # Full-height stretch: if a target bottom is supplied and there is spare
    # room, distribute the extra space as inter-row gaps so the rail fills the
    # right column top-to-bottom.
    if bottom is not None and (bottom - top) > natural:
        ch = bottom - top
        extra = ch - natural
        row_gap = extra / max(1, len(steps))
    else:
        row_gap = 0.14
        ch = header_h + 0.1 + sum(row_heights) + row_gap * len(steps) + 0.1

    # Panel background + header.
    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(top), Inches(PANEL_W), Inches(ch)
    )
    box.name = "flow-callout"
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb("FFFFFF")
    box.line.color.rgb = _rgb(theme.CONNECTOR_COLOR)
    box.line.width = Pt(0.75)
    box.shadow.inherit = False
    _blank_tf(box)
    _panel_header(slide, x, top, "Request Flow")

    ry = top + header_h + 0.1
    for i, ((title, desc, icon_key), rh) in enumerate(zip(steps, row_heights), start=1):
        # Solid blue circular badge holding the step's AWS service icon (falls
        # back to the step number when no icon is available).
        badge = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(x + 0.12), Inches(ry), Inches(badge_d), Inches(badge_d)
        )
        badge.name = f"flow-badge:{i}"
        badge.fill.solid()
        badge.fill.fore_color.rgb = _rgb(theme.TITLE_ACCENT)
        badge.line.fill.background()
        badge.shadow.inherit = False
        _blank_tf(badge)

        placed_icon = False
        stream = get_icon_stream(icon_key) if icon_key else None
        if stream is not None:
            pad = 0.06
            try:
                slide.shapes.add_picture(
                    stream, Inches(x + 0.12 + pad), Inches(ry + pad),
                    Inches(badge_d - 2 * pad), Inches(badge_d - 2 * pad),
                )
                placed_icon = True
            except Exception:
                placed_icon = False
        # Small step-number chip at the badge's top-left corner (always), so the
        # ordering is explicit even with an icon in the badge.
        nchip = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(x + 0.04), Inches(ry - 0.04),
            Inches(0.16), Inches(0.16),
        )
        nchip.name = f"flow-num:{i}"
        nchip.fill.solid()
        nchip.fill.fore_color.rgb = _rgb("FFFFFF")
        nchip.line.color.rgb = _rgb(theme.TITLE_ACCENT)
        nchip.line.width = Pt(0.75)
        nchip.shadow.inherit = False
        ntf = nchip.text_frame
        ntf.word_wrap = False
        ntf.auto_size = MSO_AUTO_SIZE.NONE
        ntf.vertical_anchor = MSO_ANCHOR.MIDDLE
        for m in ("left", "right", "top", "bottom"):
            setattr(ntf, f"margin_{m}", Emu(0))
        npar = ntf.paragraphs[0]
        npar.alignment = PP_ALIGN.CENTER
        nr = npar.add_run()
        nr.text = str(i)
        nr.font.size = Pt(6)
        nr.font.bold = True
        nr.font.name = theme.FONT_NAME
        nr.font.color.rgb = _rgb(theme.TITLE_ACCENT)
        if not placed_icon:
            # No service icon: center the number in the badge instead.
            btf = badge.text_frame
            btf.vertical_anchor = MSO_ANCHOR.MIDDLE
            bp = btf.paragraphs[0]
            bp.alignment = PP_ALIGN.CENTER
            br = bp.add_run()
            br.text = str(i)
            br.font.size = Pt(11)
            br.font.bold = True
            br.font.name = theme.FONT_NAME
            br.font.color.rgb = _rgb("FFFFFF")

        # Title + description text block to the right of the badge.
        tb = slide.shapes.add_textbox(
            Inches(x + text_x_off), Inches(ry - 0.02),
            Inches(body_w), Inches(rh + 0.1),
        )
        tf = tb.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        for m in ("left", "right", "top", "bottom"):
            setattr(tf, f"margin_{m}", Emu(0))
        tp = tf.paragraphs[0]
        trun = tp.add_run()
        trun.text = title
        trun.font.size = Pt(8)
        trun.font.bold = True
        trun.font.name = theme.FONT_NAME
        trun.font.color.rgb = _rgb(theme.TITLE_ACCENT)
        dp = tf.add_paragraph()
        drun = dp.add_run()
        drun.text = desc
        drun.font.size = Pt(7)
        drun.font.name = theme.FONT_NAME
        drun.font.color.rgb = _rgb(theme.TITLE_COLOR)

        ry += rh + row_gap
    return top + ch


def _panel_header(slide, x: float, top: float, title: str) -> None:
    """Draw a colored header bar with white title text across a right panel."""
    hbar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(top), Inches(PANEL_W), Inches(0.28)
    )
    hbar.name = f"panel-header:{title}"
    hbar.fill.solid()
    hbar.fill.fore_color.rgb = _rgb(theme.PANEL_HEADER_FILL)
    hbar.line.fill.background()
    hbar.shadow.inherit = False
    tf = hbar.text_frame
    tf.word_wrap = False
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Emu(70000)
    tf.margin_right = Emu(20000)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = title
    run.font.size = Pt(10)
    run.font.bold = True
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(theme.PANEL_HEADER_TEXT)


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
    """Pass 2: the AWS-style label strip at the container's top-left.

    The CIDR (when present) is rendered INLINE in the title, matching the AWS
    reference format: 'Public Subnet AZ1 (10.0.1.0/24)'.
    """
    raw = obj.get("label") or obj["objectId"]
    label = _title_with_cidr(raw, obj.get("cidr"))
    st = theme.container_style(obj.get("nodeType", "CONTAINER"), label)
    strip_h = 0.22
    # Narrow SUBNET titles use a 7pt font (see below); size the strip to the
    # smaller glyph width so the full 'Subnet ... (CIDR)' fits, capped at the
    # box width.
    _per_char = 0.058 if (obj.get("nodeType") == "SUBNET" and len(label) > 20) else 0.072
    strip_w = min(max(len(label) * _per_char + 0.14, 0.7), obj["w"])
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
    # Smaller title font for narrow SUBNET boxes so 'Private App Subnet AZ1
    # (10.0.11.0/24)' fits without clipping; keep 9pt for wide containers.
    run.font.size = Pt(7 if (obj.get("nodeType") == "SUBNET" and len(label) > 20) else 9)
    run.font.bold = False
    run.font.name = theme.FONT_NAME
    run.font.color.rgb = _rgb(st.title_text)


def _base_presentation():
    """Open the shared brand template from S3 as the base deck, falling back to
    a blank 16:9 presentation if the template is unavailable.

    Returns (prs, slide): the slide is a clean drawing surface. If the template
    ships with a placeholder slide, we reuse the FIRST slide (so its master/
    theme + any background apply); otherwise we add a blank-layout slide.
    """
    stream = get_template_stream()
    if stream is not None:
        try:
            prs = Presentation(stream)
            if prs.slides:
                slide = prs.slides[0]
                return prs, slide, True
            # Template has masters/theme but no slide: add one on its layout.
            layout_idx = 6 if len(prs.slide_layouts) > 6 else len(prs.slide_layouts) - 1
            slide = prs.slides.add_slide(prs.slide_layouts[layout_idx])
            return prs, slide, True
        except Exception:
            pass
    prs = Presentation()
    return prs, None, False


def compile_pptx(layout: dict) -> bytes:
    """Build the editable PPTX in 5 strict passes (Ruleset section 3).

    The deck is built on top of the shared brand template (templates/
    template_slide.pptx in the icon bucket) when available, so generated
    content inherits the template's master/theme.
    """
    slide_def = layout["slide"]
    w_in = slide_def["widthIn"]
    h_in = slide_def["heightIn"]

    prs, slide, from_template = _base_presentation()
    prs.slide_width = Inches(w_in)
    prs.slide_height = Inches(h_in)
    if slide is None:
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

    # Pass 4 + 5 - connector lines + labels (directional arrows). Styled by
    # edge class: request=solid charcoal, replication=dashed orange (Multi-AZ),
    # security=dotted grey. Arrowheads point in the direction of data flow.
    for conn in connectors:
        _add_connector(slide, conn)
    for conn in connectors:
        _add_connector_label(slide, conn)

    _add_title(slide, layout.get("title", ""), w_in)
    # Right-hand column (two-column split): the Request Flow rail is now the
    # SOLE right panel and STRETCHES full-height (Key Assumptions + Legend were
    # removed for a cleaner, more professional layout).
    _add_flow_callouts(slide, layout, w_in, PANEL_TOP, bottom=h_in - 0.2)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _add_assumptions_box(slide, assumptions: list, slide_w_in: float, top: float) -> float:
    """Key Assumptions panel (right column): the model's design assumptions as
    muted bullet lines. Height grows with the wrapped content (dynamic sizing).
    Returns the panel's bottom Y; returns `top` unchanged if there are none."""
    if not assumptions:
        return top
    x = panel_x(slide_w_in)
    line_h = 0.155
    pad_top = 0.30
    pad_bottom = 0.08
    bullet_gap = 0.03   # small gap between bullets
    total_lines = sum(_wrap_count("\u2022 " + a, PANEL_W, 7.0) for a in assumptions)
    ch = pad_top + total_lines * line_h + len(assumptions) * bullet_gap + pad_bottom

    ch += 0.06  # extra room under the header bar
    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(top), Inches(PANEL_W), Inches(ch)
    )
    box.name = "key-assumptions"
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb("FFFFFF")
    box.line.color.rgb = _rgb(theme.CONNECTOR_COLOR)
    box.line.width = Pt(0.75)
    box.shadow.inherit = False
    _blank_tf(box)
    _panel_header(slide, x, top, "Key Assumptions")

    body = slide.shapes.add_textbox(
        Inches(x + 0.08), Inches(top + 0.32), Inches(PANEL_W - 0.18), Inches(ch - 0.38)
    )
    tf = body.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    for m in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{m}", Emu(0))
    first = True
    for a in assumptions:
        bp = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        br = bp.add_run()
        br.text = "\u2022 " + a
        br.font.size = Pt(7)
        br.font.name = theme.FONT_NAME
        br.font.color.rgb = _rgb(theme.TITLE_COLOR)
    return top + ch
