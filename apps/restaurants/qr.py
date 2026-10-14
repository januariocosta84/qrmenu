import io
import re
import textwrap
from xml.sax.saxutils import escape

import qrcode
import qrcode.image.svg
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import base64

from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


BADGE_SHARE = 0.22  # badge width as a share of the code: covers ~5% of it; H correction recovers up to 30%


def _qr(data: str, badge: bool = False) -> qrcode.QRCode:
    # With a badge in the middle, use the highest error correction: it recovers the covered part and
    # also copes with dirt and scratches on table stickers.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H if badge else ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    return qr


def qr_badge(restaurant, size: int = 300) -> Image.Image | None:
    """
    The square badge for the middle of a restaurant's QR codes: its logo with the name
    underneath (or just the name, in bold, without a logo). None if there's nothing to show.
    """
    name = (restaurant.name or "").strip()
    logo = None
    if restaurant.logo:
        try:
            with restaurant.logo.open("rb") as f:
                logo = Image.open(io.BytesIO(f.read())).convert("RGBA")
        except (OSError, ValueError):
            logo = None
    if not name and logo is None:
        return None
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 7, fill="white", outline="black",
                           width=max(2, size // 75))
    pad = size // 10
    inner = size - 2 * pad

    def text_block(lines_max, max_font, top, height):
        """Name on up to `lines_max` lines, whole words only, as large as fits in the area."""
        for font_size in range(max_font, 9, -1):
            font = _font(font_size)
            chars = max(4, int(inner / (font_size * 0.58)))
            lines = textwrap.wrap(name, chars, break_long_words=False)
            line_h = int(font_size * 1.12)
            fits = (len(lines) <= lines_max and line_h * len(lines) <= height
                    and all(draw.textlength(ln, font=font) <= inner for ln in lines))
            if fits or font_size == 10:
                if not fits:  # very long name: keep what fits
                    lines = textwrap.wrap(name, chars)[:lines_max]
                y = top + (height - line_h * len(lines)) // 2
                for ln in lines:
                    w = draw.textlength(ln, font=font)
                    draw.text(((size - w) / 2, y), ln, fill="black", font=font)
                    y += line_h
                return

    if logo is not None:
        logo_h = int(inner * (0.62 if name else 1))
        logo.thumbnail((inner, logo_h), Image.LANCZOS)
        badge.alpha_composite(logo, ((size - logo.width) // 2, pad + (logo_h - logo.height) // 2))
        if name:
            text_block(2, size // 9, pad + logo_h + size // 40, inner - logo_h - size // 40)
    else:
        text_block(4, size // 5, pad, inner)
    return badge


def _badge_box(width: float, height: float) -> tuple[float, float, float]:
    """(x, y, side) of the badge in the middle of a code of this size."""
    side = width * BADGE_SHARE
    return (width - side) / 2, (height - side) / 2, side


def site_link(request=None) -> str:
    """The platform address printed under every QR code, e.g. https://qrmenu.timorstore.com."""
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL
    return request.build_absolute_uri("/").rstrip("/") if request is not None else ""


def _font(size: int):
    for path in FONT_FILES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def qr_png(data: str, caption: str = "", badge: Image.Image | None = None) -> bytes:
    img = _qr(data, badge is not None).make_image(fill_color="black", back_color="white").convert("RGB")
    if badge is not None:
        x, y, side = _badge_box(*img.size)
        img.paste(b := badge.resize((int(side), int(side)), Image.LANCZOS), (int(x), int(y)), b)
    if caption:
        w, h = img.size
        band = max(40, w // 9)
        canvas = Image.new("RGB", (w, h + band), "white")
        canvas.paste(img, (0, 0))
        draw = ImageDraw.Draw(canvas)
        size = band // 2
        font = _font(size)
        while size > 8 and draw.textlength(caption, font=font) > w * 0.9:  # shrink to fit the width
            size -= 1
            font = _font(size)
        tw = draw.textlength(caption, font=font)
        # Sit the text in the quiet zone just below the code.
        draw.text(((w - tw) / 2, h - band * 0.25), caption, fill="black", font=font)
        img = canvas
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_svg(data: str, caption: str = "", badge: Image.Image | None = None) -> str:
    svg = _qr(data, badge is not None).make_image(image_factory=qrcode.image.svg.SvgPathImage).to_string(encoding="unicode")
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not m:
        return svg
    w, h = float(m.group(1)), float(m.group(2))
    if badge is not None:
        buf = io.BytesIO()
        badge.save(buf, format="PNG")
        x, y, side = _badge_box(w, h)
        svg = svg.replace("</svg>", f'<image x="{x:.2f}" y="{y:.2f}" width="{side:.2f}" height="{side:.2f}" '
                                    f'href="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"/></svg>')
    if not caption:
        return svg
    band = w * 0.11
    font_size = min(band * 0.55, w * 0.9 / (len(caption) * 0.56))  # ~0.56em per character in a sans font
    new_h = round(h + band, 2)
    svg = svg.replace(f'height="{m.group(2)}mm"', f'height="{new_h}mm"', 1)
    svg = svg.replace(m.group(0), f'viewBox="0 0 {m.group(1)} {new_h}"', 1)
    text = (f'<rect y="{h}" width="{w}" height="{round(band, 2)}" fill="#ffffff"/>'
            f'<text x="{w / 2:.2f}" y="{h + band * 0.2:.2f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-weight="700" font-size="{font_size:.2f}" fill="#000000">{escape(caption)}</text>')
    return svg.replace("</svg>", text + "</svg>")
