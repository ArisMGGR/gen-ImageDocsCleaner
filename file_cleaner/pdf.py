"""Saving through pikepdf writes a new file containing only reachable objects, which also drops the
earlier revisions that an incremental PDF update would otherwise leave recoverable."""

from __future__ import annotations

import os
import tempfile
import threading

import pikepdf

from . import images, tools
from .models import Category, CleaningError, ScanResult, Tag, check

_PRIVATE_KEYS = ("/Metadata", "/PieceInfo", "/LastModified")

COMMENTS, ATTACHMENTS = "comments", "attached files"

# Annotations a reader adds on top of the page: notes, highlights, drawings, stamps and attached files.
# Each one can carry its author's name. Links and form fields are part of the document and stay.
_MARKUP = {f"/{n}" for n in (
    "Text", "FreeText", "Popup", "Highlight", "Underline", "StrikeOut", "Squiggly", "Caret", "Ink", "Stamp",
    "Line", "Square", "Circle", "Polygon", "PolyLine", "FileAttachment", "Sound", "Redact")}


def _open(path: str) -> pikepdf.Pdf:
    try:
        return pikepdf.open(path)
    except pikepdf.PasswordError as error:
        raise CleaningError("This PDF is password protected.") from error
    except pikepdf.PdfError as error:
        raise CleaningError("This PDF is damaged and could not be read.") from error


def _dictionaries(pdf: pikepdf.Pdf):
    for obj in pdf.objects:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            yield obj


def _image_extension(obj) -> str | None:
    """Only JPEG and JPEG 2000 streams are real image files with room for metadata."""
    if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != pikepdf.Name.Image:
        return None
    filters = obj.get("/Filter")
    if isinstance(filters, pikepdf.Array):
        filters = filters[0] if len(filters) == 1 else None
    if filters == pikepdf.Name.DCTDecode:
        return ".jpg"
    if filters == pikepdf.Name.JPXDecode:
        return ".jp2"
    return None


def scan(path: str, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult(tools.read_tags(path, cancel=cancel))

    with _open(path) as pdf:
        objects = list(_dictionaries(pdf))

        private = sum(1 for d in objects if any(k in d for k in _PRIVATE_KEYS))
        if private and not result.removable:
            result.tags.append(Tag(Category.OTHER, "Internal metadata", f"{private} object(s)"))

        index = 0
        for obj in objects:
            ext = _image_extension(obj)
            if ext is None:
                continue
            check(cancel)
            index += 1
            with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
                file = os.path.join(temp, "image" + ext)
                with open(file, "wb") as f:
                    f.write(obj.read_raw_bytes())
                inner = images.scan(file, f"Embedded image {index}", cancel)
            result.tags.extend(t for t in inner.tags if t.must_be_removed)

        result.extras = _find_extras(pdf)
        _add_warnings(objects, result)
    return result


def _is_markup(annotation) -> bool:
    return isinstance(annotation, pikepdf.Dictionary) and str(annotation.get("/Subtype", "")) in _MARKUP


def _find_extras(pdf: pikepdf.Pdf) -> list[str]:
    extras = []
    if any(_is_markup(a) for page in pdf.pages for a in page.obj.get("/Annots", [])):
        extras.append(COMMENTS)
    if any(isinstance(d, pikepdf.Dictionary) and "/EF" in d for d in _dictionaries(pdf)) or \
            any(d.get("/Type") == pikepdf.Name.EmbeddedFile for d in _dictionaries(pdf)):
        extras.append(ATTACHMENTS)
    return extras


def _remove_extras(pdf: pikepdf.Pdf) -> list[str]:
    removed = _find_extras(pdf)
    for page in pdf.pages:
        annotations = page.obj.get("/Annots")
        if annotations is not None:
            kept = [a for a in annotations if not _is_markup(a)]
            if kept:
                page.obj["/Annots"] = pikepdf.Array(kept)
            else:
                del page.obj["/Annots"]
        if "/AF" in page.obj:
            del page.obj["/AF"]
    names = pdf.Root.get("/Names")
    if names is not None and "/EmbeddedFiles" in names:
        del names["/EmbeddedFiles"]
    for key in ("/AF", "/Collection"):
        if key in pdf.Root:
            del pdf.Root[key]
    # Objects that nothing points to any more are left out when the file is saved.
    return removed


def _add_warnings(objects: list, result: ScanResult) -> None:
    def named(key: str, value) -> bool:
        return any(d.get(key) == value for d in objects)

    if named("/Type", pikepdf.Name.Sig) or named("/FT", pikepdf.Name.Sig):
        result.warnings.append("Digitally signed. Cleaning will invalidate the signature.")


def clean(source: str, target: str, cancel: threading.Event | None = None, extras: bool = False) -> list[str]:
    cleaned = 0
    with _open(source) as pdf:
        removed = _remove_extras(pdf) if extras else []
        for obj in _dictionaries(pdf):
            check(cancel)
            ext = _image_extension(obj)
            if ext is not None:
                obj.write(_strip_image(obj.read_raw_bytes(), ext, cancel), filter=obj.get("/Filter"))
                cleaned += 1
            for key in _PRIVATE_KEYS:
                if key in obj:
                    del obj[key]

        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]
        pdf.save(target, fix_metadata_version=False)

    notes = [f"Cleaned {cleaned} embedded image(s)."] if cleaned else []
    if removed:
        notes.append(f"Removed {' and '.join(removed)}.")
    return notes


def _strip_image(data: bytes, ext: str, cancel) -> bytes:
    with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
        source = os.path.join(temp, "in" + ext)
        target = os.path.join(temp, "out" + ext)
        with open(source, "wb") as f:
            f.write(data)
        if not tools.strip_image(source, target, cancel):
            raise CleaningError("An embedded image could not be cleaned.")
        with open(target, "rb") as f:
            return f.read()
