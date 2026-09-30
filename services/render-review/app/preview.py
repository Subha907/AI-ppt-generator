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


def _fit_text(draw, text: str, font, max_w: int) -> str:
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
        label = o.get("label") or o["objectId"]
        st = theme.container_style(o.get("nodeType", "CONTAINER"), label)
        box = [px(o["x"]), px(o["y"]), px(o["x"] + o["w"]), px(o["y"] + o["h"])]
        _rounded(draw, box, radius=6, fill=theme.hex_to_rgb(st.fill),
                 outline=theme.hex_to_rgb(st.border), width=2)
        # Label strip (filled tab, top-left).
        f = _font(11)
        strip_w = min(max(int(draw.textlength(label, font=f)) + 14, 60), int(o["w"] * ppi))
        strip_h = 20
        draw.rectangle([box[0], box[1], box[0] + strip_w, box[1] + strip_h],
                       fill=theme.hex_to_rgb(st.title_fill))
        draw.text((box[0] + 5, box[1] + 3), label,
                  fill=theme.hex_to_rgb(st.title_text), font=f)

    # 2. Connectors.
    for c in layout.get("connectors", []):
        pts = c.get("points") or []
        for a, b in zip(pts, pts[1:]):
            draw.line(
                [(px(a["x"]), px(a["y"])), (px(b["x"]), px(b["y"]))],
                fill=theme.hex_to_rgb(theme.CONNECTOR_COLOR),
                width=2,
            )

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
        label = _fit_text(draw, o.get("label") or o["objectId"], f, o["w"] * ppi)
        tw = draw.textlength(label, font=f)
        cx = px(o["x"] + o["w"] / 2)
        ly = px(o["y"] + icon_h_in) + 2
        draw.text((cx - tw / 2, ly), label,
                  fill=theme.hex_to_rgb(theme.TITLE_COLOR), font=f)

    # 4. Title.
    tf = _font(18)
    draw.text((px(0.3), px(0.06)), layout.get("title", "Architecture"),
              fill=theme.hex_to_rgb(theme.TITLE_COLOR), font=tf)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
