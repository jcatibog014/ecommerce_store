"""Product photos: check an upload really is a picture, then keep two small WebP copies.

A phone photo can be 5 MB; a 1000-pixel WebP is about 80 KB, and the small
copy for product lists about 15 KB. That keeps pages fast on mobile data.
"""
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import Photo

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
BIG_SIDE = 1000
SMALL_SIDE = 400
# A tiny file can claim to be a 100,000 x 100,000 picture and fill the memory
# when opened. Pillow refuses anything bigger than this.
Image.MAX_IMAGE_PIXELS = 40_000_000


class PhotoProblem(ValueError):
    pass


def webp(image, side):
    copy = image.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, "WEBP", quality=82, method=4)
    return out.getvalue(), copy.size


def make_photo(store_id, data):
    """A Photo row (not yet added) from uploaded bytes, or PhotoProblem with a sentence for the page."""
    if not data:
        raise PhotoProblem("Choose a photo first.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoProblem("That photo is too big. Photos can be up to 6 MB.")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            if opened.format not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
                raise PhotoProblem("Use a JPG, PNG or WebP photo.")
            image = ImageOps.exif_transpose(opened)  # phones save "turn me" as a note; do it now
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoProblem("That file isn't a photo we can read. Use a JPG, PNG or WebP photo.") from None
    big, (width, height) = webp(image, BIG_SIDE)
    small, _ = webp(image, SMALL_SIDE)
    return Photo(store_id=store_id, data=big, thumb=small, width=width, height=height)
