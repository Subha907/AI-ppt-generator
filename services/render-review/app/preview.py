"""Pillow preview renderer.

Draws a PNG that mirrors the compiled slide (same containers, icons, connectors,
labels and theme colors) directly from the layout model. This gives Agent 4 an
image to review without needing PowerPoint or LibreOffice installed.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from . import theme
from .config import get_settings
from .iconstore import get_icon_stream


def _font(size: int):
    """Best-effort TrueType font; falls back to Pillow's default bitmap font."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded(draw, box, radius, fill, outline, width):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _seg(draw, x0, y0, x1, y1, color, width=2, dashed=False):
    """Draw a straight segment, optionally dashed (for replication edges)."""
    if not dashed:
        draw.line([(x0, y0), (x1, y1)], fill=color, width=width)
        return
    import math
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist == 0:
        return
    ux, uy = dx / dist, dy / dist
    dash, gap = 7, 5
    pos = 0.0
    while pos < dist:
        a = pos
        b = min(pos + dash, dist)
        draw.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)],
                  fill=color, width=width)
        pos += dash + gap


def _arrowhead(draw, p_prev: dict, p_last: dict, color, px):
    """Draw a small filled triangle at p_last pointing away from p_prev."""
    import math
    x0, y0 = px(p_prev["x"]), px(p_prev["y"])
    x1, y1 = px(p_last["x"]), px(p_last["y"])
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1:
        return
    ux, uy = dx / dist, dy / dist
    size = 7
    # Base points perpendicular to the direction.
    bx, by = x1 - ux * size, y1 - uy * size
    perp = (-uy, ux)
    half = 3.5
    p1 = (bx + perp[0] * half, by + perp[1] * half)
    p2 = (bx - perp[0] * half, by - perp[1] * half)
    draw.polygon([(x1, y1), p1, p2], fill=color)


import re as _re

_CIDR_RE = _re.compile(r"\s*\(?\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\)?")


def _oneline(text: str) -> str:
    """Collapse any newlines/tabs to single spaces. PIL's textlength cannot
    measure multiline text, so every label drawn in the preview must be
    single-line (LLM labels occasionally contain embedded newlines)."""
    return " ".join((text or "").split())


def _title_with_cidr(label: str, cidr: str | None) -> str:
    """Single-line container title with the CIDR rendered inline exactly once.

    Strips any CIDR already embedded in the label (Agent 1 often puts it on a
    second line) before appending the parenthesized `cidr`, avoiding dupes.
    """
    base = _CIDR_RE.sub("", _oneline(label)).strip()
    if cidr:
        return f"{base} ({cidr})"
    return base


def _wrap_lines(draw, text: str, font, max_w: float, max_lines: int = 2) -> list[str]:
    """Word-wrap `text` into up to `max_lines` lines that each fit `max_w` px.

    Drops a leading vendor prefix for compactness. Greedy wrap; the final line
    is ellipsized only if words still overflow (last resort), so most captions
    render in full across two lines.
    """
    text = _oneline(text)
    if draw.textlength(text, font=font) > max_w:
        for prefix in ("Amazon ", "AWS "):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines:
        lines.append(cur)
    else:
        # Overflow beyond max_lines: append leftover words to the last line and
        # ellipsize it to fit.
        leftover = cur
        last = lines[-1] + " " + leftover if leftover else lines[-1]
        while last and draw.textlength(last + "\u2026", font=font) > max_w and " " in last:
            last = last.rsplit(" ", 1)[0]
        if draw.textlength(last, font=font) > max_w:
            last += "\u2026"
        lines[-1] = last
    return [l for l in lines if l][:max_lines]


# Right-hand description-panel column (mirrors compiler.py).
PANEL_W = 2.65
PANEL_RIGHT_MARGIN = 0.15
PANEL_TOP = 0.55
PANEL_GAP = 0.16


def _panel_x(slide_w_in: float) -> float:
    return slide_w_in - PANEL_W - PANEL_RIGHT_MARGIN


def _fit_text(draw, text: str, font, max_w: int) -> str:
    text = _oneline(text)
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "..."
    while text and draw.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ""


def render_preview(layout: dict) -> bytes:
    settings = get_settings()
    ppi = settings.preview_px_per_in
    slide = layout["slide"]
    W = int(round(slide["widthIn"] * ppi))
    H = int(round(slide["heightIn"] * ppi))

    img = Image.new("RGB", (W, H), theme.hex_to_rgb(theme.BACKGROUND))
    draw = ImageDraw.Draw(img)

    def px(v_in: float) -> int:
        return int(round(v_in * ppi))

    # Connectors first (behind boxes would hide them); draw after containers but
    # before/after leaves is fine - we draw containers, then connectors, then
    # leaves + labels on top for clarity.
    objects = sorted(layout.get("objects", []), key=lambda o: o.get("z", 0))

    # 1. Containers + AWS-style label strip.
    for o in objects:
        if o.get("kind") != "CONTAINER":
            continue
        label = _title_with_cidr(o.get("label") or o["objectId"], o.get("cidr"))
        st = theme.container_style(o.get("nodeType", "CONTAINER"), label)
        box = [px(o["x"]), px(o["y"]), px(o["x"] + o["w"]), px(o["y"] + o["h"])]
        _rounded(draw, box, radius=6, fill=theme.hex_to_rgb(st.fill),
                 outline=theme.hex_to_rgb(st.border), width=2)
        # Label strip (filled tab, top-left). Narrow SUBNET titles use a
        # smaller font so 'Private App Subnet AZ1 (CIDR)' fits without clipping.
        f = _font(9 if (o.get("nodeType") == "SUBNET" and len(label) > 20) else 11)
        strip_w = min(max(int(draw.textlength(label, font=f)) + 14, 60), int(o["w"] * ppi))
        strip_h = 20
        draw.rectangle([box[0], box[1], box[0] + strip_w, box[1] + strip_h],
                       fill=theme.hex_to_rgb(st.title_fill))
        draw.text((box[0] + 5, box[1] + 3), label,
                  fill=theme.hex_to_rgb(st.title_text), font=f)

    # 2. Connectors (directional). Styled by edge class to mirror the PPTX:
    #    request=solid charcoal, replication=dashed orange, security=dotted grey.
    #    A small triangular arrowhead is drawn at the final point.
    for c in layout.get("connectors", []):
        pts = c.get("points") or []
        if len(pts) < 2:
            continue
        cst = theme.connector_style(c.get("edgeClass", "request"))
        col = theme.hex_to_rgb(cst.color)
        dashed = cst.dash != "solid"
        for a, b in zip(pts, pts[1:]):
            _seg(draw, px(a["x"]), px(a["y"]), px(b["x"]), px(b["y"]),
                 col, width=2, dashed=dashed)
        # Arrowhead at the terminal point, oriented along the last segment.
        _arrowhead(draw, pts[-2], pts[-1], col, px)

    # (connector labels are drawn in section 2b, after icons, so they sit on top)

    # 3. Leaves: real AWS icon (from S3) with the label centered below it.
    #    Falls back to a colored square if the icon is unavailable - matching
    #    the PPTX compiler so the preview shows what the slide shows.
    for o in objects:
        if o.get("kind") == "CONTAINER":
            continue
        st = theme.leaf_style(o.get("nodeType", "COMPONENT"))
        label_h_in = min(0.34, o["h"] * 0.4)
        icon_h_in = max(o["h"] - label_h_in, 0.3)
        icon_w_in = min(icon_h_in, o["w"])
        icon_x_in = o["x"] + (o["w"] - icon_w_in) / 2
        ibox = [px(icon_x_in), px(o["y"]),
                px(icon_x_in + icon_w_in), px(o["y"] + icon_h_in)]

        stream = get_icon_stream(o.get("iconKey"))
        if stream is not None:
            try:
                icon_img = Image.open(stream).convert("RGBA")
                target = (max(ibox[2] - ibox[0], 1), max(ibox[3] - ibox[1], 1))
                icon_img = icon_img.resize(target, Image.LANCZOS)
                img.paste(icon_img, (ibox[0], ibox[1]), icon_img)
            except Exception:
                _rounded(draw, ibox, radius=5, fill=theme.hex_to_rgb(st.fill),
                         outline=theme.hex_to_rgb(st.border), width=2)
        else:
            _rounded(draw, ibox, radius=5, fill=theme.hex_to_rgb(st.fill),
                     outline=theme.hex_to_rgb(st.border), width=2)

        f = _font(9)
        cx = px(o["x"] + o["w"] / 2)
        ly = px(o["y"] + icon_h_in) + 2
        # Honour the per-icon slot width (set by the layout for multi-icon
        # lanes) so captions don't overlap their neighbours; otherwise allow a
        # caption a bit wider than the icon.
        cap_w = min(max(o["w"], 1.4), o.get("labelMaxW", 1.4)) * ppi
        for line in _wrap_lines(draw, o.get("label") or o["objectId"], f, cap_w):
            tw = draw.textlength(line, font=f)
            draw.text((cx - tw / 2, ly), line,
                      fill=theme.hex_to_rgb(theme.TITLE_COLOR), font=f)
            ly += int(0.14 * ppi)
        # Component spec sub-text (muted grey), e.g. 'm6i.xlarge | Auto Scaling 2-4'.
        spec = o.get("spec")
        if spec:
            sf = _font(7)
            for line in _wrap_lines(draw, spec, sf, cap_w):
                tw = draw.textlength(line, font=sf)
                draw.text((cx - tw / 2, ly), line,
                          fill=theme.hex_to_rgb(theme.CONNECTOR_COLOR), font=sf)
                ly += int(0.11 * ppi)

    # 3b. Connector labels (on top of lines/icons), e.g. 'HTTPS (Internet)',
    #     'Multi-AZ Replication'. Short, centred at the route midpoint.
    clf = _font(8)
    for c in layout.get("connectors", []):
        label = c.get("label")
        pts = c.get("points") or []
        if not label or len(pts) < 2:
            continue
        # Keep only the dashed DB replication label inline; all other flow
        # labels live in the right-hand Request Flow rail (they collide with
        # icons/subnet titles when drawn on the dense diagram).
        if c.get("edgeClass") != "replication":
            continue
        cst = theme.connector_style(c.get("edgeClass", "request"))
        mid = pts[len(pts) // 2]
        txt = _fit_text(draw, _oneline(label), clf, 1.6 * ppi)
        tw = draw.textlength(txt, font=clf)
        lx, ly = px(mid["x"]) - tw / 2, px(mid["y"]) - 14
        # White halo behind text so it stays legible over lines/boxes.
        draw.rectangle([lx - 2, ly - 1, lx + tw + 2, ly + 11], fill=(255, 255, 255))
        draw.text((lx, ly), txt, fill=theme.hex_to_rgb(cst.color), font=clf)

    # 4. Title (left-aligned, bold accent) - mirrors the compiler.
    tf = _font(20)
    title = _oneline(layout.get("title", "Architecture"))
    draw.text((px(0.3), px(0.12)), title,
              fill=theme.hex_to_rgb(theme.TITLE_ACCENT), font=tf)

    # Right-hand description column: Key Assumptions -> Request Flow -> Legend,
    # stacked top-to-bottom and sized dynamically. Mirrors the PPTX compiler so
    # preview == slide.
    panel_x = _panel_x(slide["widthIn"])
    body_w_px = (PANEL_W - 0.22) * ppi
    panel_top = PANEL_TOP

    def _panel_box(top_in: float, height_in: float, title: str, title_font):
        # Box + colored header bar with white title (mirrors the compiler).
        draw.rectangle(
            [px(panel_x), px(top_in), px(panel_x + PANEL_W), px(top_in + height_in)],
            fill=(255, 255, 255),
            outline=theme.hex_to_rgb(theme.CONNECTOR_COLOR), width=1,
        )
        draw.rectangle(
            [px(panel_x), px(top_in), px(panel_x + PANEL_W), px(top_in + 0.28)],
            fill=theme.hex_to_rgb(theme.PANEL_HEADER_FILL),
        )
        draw.text((px(panel_x + 0.09), px(top_in + 0.05)), title,
                  fill=theme.hex_to_rgb(theme.PANEL_HEADER_TEXT), font=title_font)

    hfont = _font(10)
    bfont = _font(9)

    # Key Assumptions + Legend panels were removed for a cleaner layout. The
    # Request Flow rail is now the SOLE right panel and fills the full height.

    # Request Flow rail (icon badge + bold title + description), STRETCHED to
    # fill from the top down to the slide bottom (full-height right sidebar).
    flow = [c for c in layout.get("connectors", [])
            if c.get("edgeClass", "request") == "request"
            and len(c.get("points") or []) >= 2]
    if flow:
        label_of = {o["objectId"]: _oneline(o.get("label") or o["objectId"])
                    for o in objects}
        icon_of = {o["objectId"]: o.get("iconKey") for o in objects}
        steps = []
        seen = set()
        for c in flow:
            tid = c.get("targetObjectId")
            key = (tid or "") + "|" + _oneline(label_of.get(tid, ""))
            if key in seen:
                continue
            seen.add(key)
            src = label_of.get(c.get("sourceObjectId"), "")
            tgt = label_of.get(tid, "")
            lbl = c.get("label")
            desc = f"From {src}" + (f" \u2014 {lbl}" if lbl else "")
            steps.append((tgt or "Step", desc, icon_of.get(tid)))
            if len(steps) >= 8:
                break
        badge_d = 0.3
        text_x = panel_x + 0.5
        twrap_px = (PANEL_W - 0.5 - 0.1) * ppi
        title_h, desc_line_h = 0.17, 0.145
        tfont = _font(9)
        dfont = _font(8)
        wrapped = [_wrap_lines(draw, d, dfont, twrap_px, max_lines=2) for _t, d, _i in steps]
        rhs = [max(badge_d, title_h + len(w) * desc_line_h) for w in wrapped]
        rail_bottom = slide["heightIn"] - 0.2
        natural = 0.28 + 0.1 + sum(rhs) + 0.1
        if (rail_bottom - panel_top) > natural:
            fch = rail_bottom - panel_top
            row_gap = (fch - natural) / max(1, len(steps))
        else:
            row_gap = 0.14
            fch = 0.28 + 0.1 + sum(rhs) + row_gap * len(steps) + 0.1
        _panel_box(panel_top, fch, "Request Flow", hfont)
        ry = panel_top + 0.28 + 0.1
        for i, ((title, _desc, ikey), lines, rh) in enumerate(zip(steps, wrapped, rhs), start=1):
            bx0, by0 = px(panel_x + 0.12), px(ry)
            bx1, by1 = px(panel_x + 0.12 + badge_d), px(ry + badge_d)
            draw.ellipse([bx0, by0, bx1, by1], fill=theme.hex_to_rgb(theme.TITLE_ACCENT))
            placed = False
            stream = get_icon_stream(ikey) if ikey else None
            if stream is not None:
                try:
                    pad = int(0.06 * ppi)
                    glyph = Image.open(stream).convert("RGBA").resize(
                        (max(bx1 - bx0 - 2 * pad, 1), max(by1 - by0 - 2 * pad, 1)),
                        Image.LANCZOS)
                    img.paste(glyph, (bx0 + pad, by0 + pad), glyph)
                    placed = True
                except Exception:
                    placed = False
            # Step-number chip at the badge corner.
            cx0, cy0 = px(panel_x + 0.04), px(ry - 0.04)
            cx1, cy1 = px(panel_x + 0.04 + 0.16), px(ry - 0.04 + 0.16)
            draw.ellipse([cx0, cy0, cx1, cy1], fill=(255, 255, 255),
                         outline=theme.hex_to_rgb(theme.TITLE_ACCENT), width=1)
            cnw = draw.textlength(str(i), font=_font(7))
            draw.text(((cx0 + cx1) / 2 - cnw / 2, cy0 + 1), str(i),
                      fill=theme.hex_to_rgb(theme.TITLE_ACCENT), font=_font(7))
            if not placed:
                nw = draw.textlength(str(i), font=tfont)
                draw.text(((bx0 + bx1) / 2 - nw / 2, by0 + 4), str(i),
                          fill=(255, 255, 255), font=tfont)
            # Title + description.
            draw.text((px(text_x), px(ry - 0.02)),
                      _fit_text(draw, title, tfont, twrap_px),
                      fill=theme.hex_to_rgb(theme.TITLE_ACCENT), font=tfont)
            dy = ry - 0.02 + title_h
            for ln in lines:
                draw.text((px(text_x), px(dy)), ln,
                          fill=theme.hex_to_rgb(theme.TITLE_COLOR), font=dfont)
                dy += desc_line_h
            ry += rh + row_gap

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
