"""Picks the right cleaner for a file and makes sure only verified copies are ever saved."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .models import CleaningError, CleanResult, ScanResult

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".gif", ".heic", ".heif"}
DOCUMENT_TYPES = {".pdf", ".docx", ".xlsx", ".pptx"}
MEDIA_TYPES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"}

_reserved: set[str] = set()
_reserved_lock = threading.Lock()


def is_supported(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_TYPES | DOCUMENT_TYPES | MEDIA_TYPES


def _handler(path: str):
    # Imported here because office.py calls back into this module for embedded files.
    from . import images, media, office, pdf

    ext = Path(path).suffix.lower()
    if ext in IMAGE_TYPES:
        return images
    if ext in MEDIA_TYPES:
        return media
    if ext == ".pdf":
        return pdf
    if ext in DOCUMENT_TYPES:
        return office
    raise CleaningError("This file type is not supported.")


def scan(path: str, cancel: threading.Event | None = None) -> ScanResult:
    handler = _handler(path)
    return handler.scan(path, cancel=cancel)


def clean_to(source: str, target: str, cancel: threading.Event | None = None, extras: bool = False) -> list[str]:
    """With extras, comments, tracked changes, speaker notes and attachments go too (documents only)."""
    handler = _handler(source)
    if extras and handler.__name__.endswith((".office", ".pdf")):
        return handler.clean(source, target, cancel, extras=True)
    return handler.clean(source, target, cancel)


def clean(source: str, output_folder: str, relative_folder: str = "",
          cancel: threading.Event | None = None, extras: bool = False) -> CleanResult:
    """Cleans into a temporary file, verifies the result, and only then saves it. The original is
    only ever read, and a copy that fails verification is never saved."""
    ext = Path(source).suffix
    with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
        cleaned = os.path.join(temp, "cleaned" + ext)
        notes = clean_to(source, cleaned, cancel, extras)
        after = scan(cleaned, cancel)

        left = [t.name for t in after.removable[:5]] + (after.extras if extras else [])
        if left:
            names = ", ".join(dict.fromkeys(left))
            raise CleaningError(f"Verification failed, so no copy was saved. Still present: {names}.")

        return CleanResult(_publish(cleaned, source, output_folder, relative_folder), after, notes)


def _publish(cleaned: str, source: str, output_folder: str, relative_folder: str) -> str:
    folder = os.path.join(output_folder, relative_folder)
    os.makedirs(folder, exist_ok=True)

    target = _reserve_path(folder, os.path.basename(source))
    partial = None
    try:
        # Created exclusively under a unique name, so no file already in the folder is overwritten.
        handle, partial = tempfile.mkstemp(prefix=os.path.basename(target) + ".", suffix=".partial", dir=folder)
        os.close(handle)
        shutil.copyfile(cleaned, partial)  # a brand new file, so it gets fresh dates
        os.rename(partial, target)
        return target
    except OSError as error:
        if partial and os.path.exists(partial):
            os.unlink(partial)
        raise CleaningError(f"The cleaned copy could not be saved: {error.strerror}") from error
    finally:
        with _reserved_lock:
            _reserved.discard(os.path.normcase(target))


def _reserve_path(folder: str, name: str) -> str:
    """Never overwrites: a second photo.jpg becomes "photo (1).jpg"."""
    stem, ext = os.path.splitext(name)
    with _reserved_lock:
        candidate = os.path.join(folder, name)
        number = 0
        while os.path.exists(candidate) or os.path.normcase(candidate) in _reserved:
            number += 1
            candidate = os.path.join(folder, f"{stem} ({number}){ext}")
        _reserved.add(os.path.normcase(candidate))
    return candidate


@dataclass(frozen=True)
class Found:
    path: str
    relative_folder: str


def collect(paths) -> list[Found]:
    """Expands dropped files and folders. Folders keep their name and inner structure."""
    found = []
    for path in paths:
        if os.path.isfile(path):
            found.append(Found(path, ""))
        elif os.path.isdir(path):
            parent = os.path.dirname(os.path.normpath(path))
            for folder, _, files in os.walk(path, followlinks=False):
                for name in files:
                    found.append(Found(os.path.join(folder, name), os.path.relpath(folder, parent)))
    return found
