import io
import re
from xml.sax.saxutils import escape

import qrcode
import qrcode.image.svg
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_M

FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _qr(data: str) -> qrcode.QRCode:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    return qr


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


def qr_png(data: str, caption: str = "") -> bytes:
    img = _qr(data).make_image(fill_color="black", back_color="white").convert("RGB")
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


def qr_svg(data: str, caption: str = "") -> str:
    svg = _qr(data).make_image(image_factory=qrcode.image.svg.SvgPathImage).to_string(encoding="unicode")
    if not caption:
        return svg
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not m:
        return svg
    w, h = float(m.group(1)), float(m.group(2))
    band = w * 0.11
    font_size = min(band * 0.55, w * 0.9 / (len(caption) * 0.56))  # ~0.56em per character in a sans font
    new_h = round(h + band, 2)
    svg = svg.replace(f'height="{m.group(2)}mm"', f'height="{new_h}mm"', 1)
    svg = svg.replace(m.group(0), f'viewBox="0 0 {m.group(1)} {new_h}"', 1)
    text = (f'<rect y="{h}" width="{w}" height="{round(band, 2)}" fill="#ffffff"/>'
            f'<text x="{w / 2:.2f}" y="{h + band * 0.2:.2f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-weight="700" font-size="{font_size:.2f}" fill="#000000">{escape(caption)}</text>')
    return svg.replace("</svg>", text + "</svg>")
