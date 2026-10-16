from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def validate_portfolio_image(upload):
    """Safe upload validation: size limit, extension allow-list, and the file must decode as a real image."""
    name = (upload.name or "").lower()
    if not any(name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise ValidationError("Only JPG, PNG or WEBP images are allowed.")
    if upload.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(f"Image is too large (max {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    try:
        upload.seek(0)
        img = Image.open(upload)
        img.verify()
        fmt = img.format
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValidationError("This file is not a valid image.")
    finally:
        upload.seek(0)
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError("Only JPG, PNG or WEBP images are allowed.")
    if img.width * img.height > 40_000_000:
        raise ValidationError("Image dimensions are too large.")
