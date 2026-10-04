import io

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_M


def _qr(data: str) -> qrcode.QRCode:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    return qr


def qr_png(data: str) -> bytes:
    img = _qr(data).make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_svg(data: str) -> str:
    img = _qr(data).make_image(image_factory=qrcode.image.svg.SvgPathImage)
    return img.to_string(encoding="unicode")
