from __future__ import annotations

import os
import shutil
import tempfile
import threading

import pillow_heif
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from . import tools
from .models import Category, CleaningError, ScanResult, Tag

pillow_heif.register_heif_opener()

_REENCODE_QUALITY = 95
_LOSSY_FORMATS = {"JPEG", "WEBP", "HEIF"}  # where the quality setting applies; the rest re-save losslessly


def scan(path: str, source: str | None = None, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult(tools.read_tags(path, source=source, cancel=cancel))
    marker = tools.find_location_marker(path)
    if marker and not result.has_location:
        result.tags.append(Tag(Category.LOCATION, "Location data", f"Hidden marker: {marker}", source))
    return result


def clean(source: str, target: str, cancel: threading.Event | None = None) -> list[str]:
    if tools.strip_image(source, target, cancel):
        if not scan(target, cancel=cancel).removable:
            return []
        os.unlink(target)

    # Lossless removal failed or left something behind, so rebuild the file from its pixels.
    with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
        rebuilt = os.path.join(temp, "rebuilt" + os.path.splitext(target)[1])
        image_format = _reencode(source, rebuilt)
        # The encoder may add tags of its own, such as a software name in TIFF files.
        if not tools.strip_image(rebuilt, target, cancel):
            shutil.copyfile(rebuilt, target)
    if image_format in _LOSSY_FORMATS:
        return [f"Re-saved at high quality ({_REENCODE_QUALITY}%) because metadata could not be removed losslessly."]
    return ["Re-saved from its pixels, without quality loss, because metadata could not be removed directly."]


def _reencode(source: str, target: str) -> str:
    """Returns the format the image was saved in."""
    try:
        with Image.open(source) as image:
            image_format = image.format
            options = {"quality": _REENCODE_QUALITY}
            if profile := image.info.get("icc_profile"):
                options["icc_profile"] = profile
            for key in ("duration", "loop"):
                if key in image.info:
                    options[key] = image.info[key]

            # Rotation is applied to the pixels here, because the tag that held it is being dropped.
            frames = [ImageOps.exif_transpose(frame.copy()) for frame in ImageSequence.Iterator(image)]
            if len(frames) > 1:
                options.update(save_all=True, append_images=frames[1:])
            frames[0].save(target, format=image_format, **options)
            return image_format
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise CleaningError("This image could not be read or re-saved.") from error
