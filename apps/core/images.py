"""
Secure image upload handling.

Every upload is opened with Pillow, checked against an allow-list of formats
and size limits, resized and re-encoded to WebP with a random filename. The
original bytes (and any EXIF/GPS metadata or embedded payloads) are never
stored.
"""
import io
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 40_000_000  # guard against decompression bombs

# (max width, max height) per usage
SIZES = {
    "item": (1000, 1000),
    "logo": (512, 512),
    "cover": (1800, 900),
}


def validate_image_upload(f) -> None:
    if f.size > settings.MAX_IMAGE_UPLOAD_BYTES:
        mb = settings.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(_("Image is too large (max %(mb)s MB).") % {"mb": mb})
    try:
        f.seek(0)
        with Image.open(f) as img:
            if img.format not in ALLOWED_FORMATS:
                raise ValidationError(_("Only JPEG, PNG and WebP images are allowed."))
            if img.width * img.height > MAX_PIXELS:
                raise ValidationError(_("Image dimensions are too large."))
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(_("Upload a valid image file.")) from exc
    finally:
        f.seek(0)


def process_image(f, kind: str = "item") -> ContentFile:
    """Validate, resize and re-encode an uploaded image to WebP."""
    validate_image_upload(f)
    with Image.open(f) as img:
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail(SIZES.get(kind, SIZES["item"]), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=80, method=4)
    return ContentFile(buf.getvalue(), name=f"{uuid.uuid4().hex}.webp")
