"""Cleans DOCX, XLSX and PPTX, which are ZIP packages of XML parts and media files."""

from __future__ import annotations

import os
import posixpath
import re
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from xml.dom import minidom

from . import engine
from .models import Category, CleaningError, ScanResult, Tag, check

_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_SETTINGS = "word/settings.xml"
_SETTINGS_RELS = "word/_rels/settings.xml.rels"
_WORKBOOK = "xl/workbook.xml"

_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_EMPTY_CORE = _DECLARATION + (
    b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>')
_EMPTY_APP = _DECLARATION + (
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"/>')
_EMPTY_CUSTOM = _DECLARATION + (
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"/>')

_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

_PROPERTY_CATEGORIES = {
    "creator": Category.AUTHOR, "lastmodifiedby": Category.AUTHOR, "company": Category.AUTHOR,
    "manager": Category.AUTHOR, "created": Category.DATES, "modified": Category.DATES,
    "lastprinted": Category.DATES, "application": Category.SOFTWARE, "appversion": Category.SOFTWARE,
}

# Content removed only when asked for, as named to the user.
COMMENTS, TRACKED_CHANGES, SPEAKER_NOTES = "comments", "tracked changes", "speaker notes"
_EXTRA_ORDER = (COMMENTS, TRACKED_CHANGES, SPEAKER_NOTES)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Parts reached through these relationship types hold comments, the people who wrote them, or notes.
_EXTRA_RELATIONSHIPS = {
    "/comments": COMMENTS, "/commentsExtended": COMMENTS, "/commentsIds": COMMENTS,
    "/commentsExtensible": COMMENTS, "/people": COMMENTS, "/commentAuthors": COMMENTS, "/authors": COMMENTS,
    "/threadedComment": COMMENTS, "/person": COMMENTS,
    "/notesSlide": SPEAKER_NOTES, "/revisionHeaders": TRACKED_CHANGES, "/usernames": TRACKED_CHANGES,
}
# The same parts by location, which also catches ones left behind without a relationship.
_EXTRA_FOLDERS = {
    "ppt/comments/": COMMENTS, "xl/threadedcomments/": COMMENTS, "xl/persons/": COMMENTS,
    "ppt/notesslides/": SPEAKER_NOTES, "xl/revisions/": TRACKED_CHANGES,
}
_EXTRA_PARTS = {"word/people.xml": COMMENTS, "ppt/commentauthors.xml": COMMENTS, "ppt/authors.xml": COMMENTS}

# Word revision marks: each one stores the reviewer's name and the time of the change.
_REVISION_MARKS = {
    "ins", "del", "moveFrom", "moveTo", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
    "moveToRangeEnd", "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblPrExChange", "trPrChange",
    "tcPrChange", "tblGridChange", "numberingChange", "cellIns", "cellDel", "cellMerge",
}
_COMMENT_MARKS = {"commentRangeStart", "commentRangeEnd", "commentReference"}


@dataclass
class _Layout:
    """Where a package keeps its property parts. The locations aren't fixed: some producers store core
    properties under a generated name, so they must be looked up through the root relationships."""
    core: str | None
    app: str | None
    custom: str | None
    thumbnails: set[str]

    def is_property_part(self, name: str) -> bool:
        return name in (self.core, self.app, self.custom) or name in self.thumbnails


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_xml(archive: zipfile.ZipFile, part: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(part))
    except KeyError:
        return None


def _layout(archive: zipfile.ZipFile) -> _Layout:
    layout = _Layout(None, None, None, set())
    rels = _read_xml(archive, _ROOT_RELS)
    if rels is None:
        return layout

    for rel in rels:
        rel_type = rel.get("Type", "")
        target = rel.get("Target", "").lstrip("/")
        if rel_type.endswith("/core-properties"):
            layout.core = target
        elif rel_type.endswith("/extended-properties"):
            layout.app = target
        elif rel_type.endswith("/custom-properties"):
            layout.custom = target
        elif rel_type.endswith("/thumbnail"):
            layout.thumbnails.add(target)
    return layout


def _open(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise CleaningError("This file is damaged or password protected.") from error


def _is_embedded_file(name: str, layout: _Layout) -> bool:
    return not layout.is_property_part(name) and engine.is_supported(name)


# Comments, tracked changes and speaker notes

def _rels_source(rels_name: str) -> str:
    """The part a relationships file belongs to: word/_rels/document.xml.rels -> word/document.xml."""
    folder, name = posixpath.split(rels_name)
    return posixpath.join(posixpath.dirname(folder), name[:-len(".rels")])


def _rels_file(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


def _resolve(source: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


def _relationship_files(archive: zipfile.ZipFile):
    for name in archive.namelist():
        if name.lower().endswith(".rels"):
            root = _read_xml(archive, name)
            if root is not None:
                yield name, _rels_source(name), root


def _only_comment_boxes(data: bytes) -> bool:
    """Excel draws old-style comment boxes in a VML drawing, which can also hold form controls."""
    # A pattern, not a parser: VML written by Excel is often not well-formed XML, and prefixes vary.
    shapes = re.findall(rb"<(?:[\w.-]+:)?ClientData\b[^>]*>", data)
    return bool(shapes) and all(re.search(rb"""ObjectType\s*=\s*["']Note["']""", s) for s in shapes)


def _extra_parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Maps each part that only holds comments, their authors, speaker notes or Excel revision history to
    what it holds. Removing them takes their relationships and content type entries with them."""
    names = {n.lower(): n for n in archive.namelist()}
    found = {}
    for lower, name in names.items():
        base = posixpath.basename(lower)
        for folder, label in _EXTRA_FOLDERS.items():
            if lower.startswith(folder):
                found[name] = label
        if lower in _EXTRA_PARTS:
            found[name] = _EXTRA_PARTS[lower]
        elif lower.startswith(("word/", "xl/")) and base.startswith("comment") and base.endswith(".xml"):
            found[name] = COMMENTS
        elif lower.startswith("xl/") and lower.endswith(".vml") and _only_comment_boxes(archive.read(name)):
            found[name] = COMMENTS

    for _, source, root in _relationship_files(archive):
        for rel in root:
            if rel.get("TargetMode") == "External":
                continue
            rel_type = rel.get("Type", "")
            label = next((v for k, v in _EXTRA_RELATIONSHIPS.items() if rel_type.endswith(k)), None)
            target = names.get(_resolve(source, rel.get("Target", "")).lower())
            if label and target:
                found[target] = label

    for part, label in list(found.items()):
        rels = names.get(_rels_file(part).lower())
        if rels:
            found[rels] = label
    return found


def _is_word_part(name: str) -> bool:
    return name.startswith("word/") and name.endswith(".xml")


def _word_marks(archive: zipfile.ZipFile, skip) -> set[str]:
    found = set()
    for name in archive.namelist():
        if not _is_word_part(name) or name in skip:
            continue
        root = _read_xml(archive, name)
        if root is None:
            continue
        for element in root.iter():
            if not element.tag.startswith("{" + _W + "}"):
                continue
            local = _local(element.tag)
            if local in _REVISION_MARKS:
                found.add(TRACKED_CHANGES)
            elif local in _COMMENT_MARKS:
                found.add(COMMENTS)
    return found


def _find_extras(archive: zipfile.ZipFile) -> tuple[dict[str, str], list[str]]:
    parts = _extra_parts(archive)
    labels = set(parts.values()) | _word_marks(archive, parts)
    return parts, [label for label in _EXTRA_ORDER if label in labels]


# Scanning

def scan(path: str, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult()
    with _open(path) as archive:
        layout = _layout(archive)
        _read_properties(archive, layout.core, result)
        _read_properties(archive, layout.app, result)
        _read_custom_properties(archive, layout.custom, result)

        if layout.thumbnails:
            result.tags.append(Tag(Category.OTHER, "Document thumbnail", "Preview image of the first page"))

        rels = _read_xml(archive, _SETTINGS_RELS)
        if rels is not None and any(r.get("Type", "").endswith("/attachedTemplate") for r in rels):
            result.tags.append(Tag(Category.OTHER, "Template path", "Attached template location"))

        settings = _read_xml(archive, _SETTINGS)
        if settings is not None and any(_local(e.tag) == "rsids" for e in settings.iter()):
            result.tags.append(Tag(Category.OTHER, "Revision IDs", "Editing session identifiers"))

        workbook = _read_xml(archive, _WORKBOOK)
        if workbook is not None:
            for element in workbook.iter():
                if _local(element.tag) == "absPath":
                    result.tags.append(Tag(Category.AUTHOR, "Saved file path", element.get("url", "")))

        _scan_embedded(archive, layout, result, cancel)
        _, extras = _find_extras(archive)
        result.extras = [e for e in _EXTRA_ORDER if e in extras or e in result.extras]
        _add_warnings(archive, workbook, result)
    return result


def _read_properties(archive: zipfile.ZipFile, part: str | None, result: ScanResult) -> None:
    root = _read_xml(archive, part) if part else None
    if root is None:
        return
    for element in root:
        name = _local(element.tag)
        value = ", ".join(t.strip() for t in element.itertext() if t.strip())
        if value:
            category = _PROPERTY_CATEGORIES.get(name.lower(), Category.OTHER)
            result.tags.append(Tag(category, name, value))


def _read_custom_properties(archive: zipfile.ZipFile, part: str | None, result: ScanResult) -> None:
    root = _read_xml(archive, part) if part else None
    if root is None:
        return
    for prop in root:
        value = "".join(prop.itertext()).strip()
        result.tags.append(Tag(Category.OTHER, prop.get("name", "Custom property"), value))


def _scan_embedded(archive, layout: _Layout, result: ScanResult, cancel) -> None:
    for name in archive.namelist():
        if not _is_embedded_file(name, layout):
            continue
        check(cancel)
        with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
            file = os.path.join(temp, "part" + os.path.splitext(name)[1])
            with open(file, "wb") as f:
                f.write(archive.read(name))
            inner = engine.scan(file, cancel)
        result.tags.extend(
            Tag(t.category, t.name, t.value, name) for t in inner.tags if t.must_be_removed)
        result.extras.extend(e for e in inner.extras if e not in result.extras)


def _add_warnings(archive: zipfile.ZipFile, workbook: ET.Element | None, result: ScanResult) -> None:
    names = [n.lower() for n in archive.namelist()]

    if any("customxml/" in n for n in names):
        result.warnings.append("Contains custom XML data, which stays in the file.")

    if any(_has_hidden_text(archive, name) for name in archive.namelist() if _is_word_part(name)):
        result.warnings.append("Contains hidden text, which stays in the file.")

    if workbook is not None and any(
            _local(e.tag) == "sheet" and e.get("state") in ("hidden", "veryHidden") for e in workbook.iter()):
        result.warnings.append("Contains hidden sheets, which stay in the file.")

    if any("embeddings/" in n and not n.endswith("/") and not engine.is_supported(n) for n in names):
        result.warnings.append("Contains embedded objects that could not be inspected.")


def _has_hidden_text(archive: zipfile.ZipFile, name: str) -> bool:
    """Body, headers, footers, notes and styles can all hide text. w:val="0" or "false" turns it off."""
    root = _read_xml(archive, name)
    return root is not None and any(
        e.tag == f"{{{_W}}}vanish" and e.get(f"{{{_W}}}val", "true").lower() not in ("0", "false", "off")
        for e in root.iter())


# Cleaning

def clean(source: str, target: str, cancel: threading.Event | None = None, extras: bool = False) -> list[str]:
    cleaned = 0
    with _open(source) as archive, zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as output:
        layout = _layout(archive)
        removal = _plan_removal(archive) if extras else None
        for name in archive.namelist():
            check(cancel)
            if name in layout.thumbnails or (removal and name in removal.parts):
                continue

            if name == layout.core:
                content = _EMPTY_CORE
            elif name == layout.app:
                content = _EMPTY_APP
            elif name == layout.custom:
                content = _EMPTY_CUSTOM
            elif _is_embedded_file(name, layout):
                content = _clean_embedded(archive, name, cancel, extras)
                cleaned += 1
            else:
                edits = [_EDITS[name]] if name in _EDITS else []
                if removal:
                    edits += removal.edits_for(name)
                content = _edit_xml(archive.read(name), *edits) if edits else archive.read(name)

            info = zipfile.ZipInfo(name, _FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, content)

    notes = [f"Cleaned {cleaned} embedded file(s)."] if cleaned else []
    if removal and removal.labels:
        notes.append(f"Removed {_join(removal.labels)}.")
    return notes


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _clean_embedded(archive: zipfile.ZipFile, name: str, cancel, extras: bool) -> bytes:
    ext = os.path.splitext(name)[1]
    with tempfile.TemporaryDirectory(prefix="filecleaner-", ignore_cleanup_errors=True) as temp:
        source = os.path.join(temp, "in" + ext)
        target = os.path.join(temp, "out" + ext)
        with open(source, "wb") as f:
            f.write(archive.read(name))
        engine.clean_to(source, target, cancel, extras)
        with open(target, "rb") as f:
            return f.read()


@dataclass
class _Removal:
    parts: set[str]  # removed entirely
    labels: list[str]
    lower_parts: set[str] = field(default_factory=set)
    removed_ids: dict[str, set[str]] = field(default_factory=dict)  # part -> relationship IDs that went with it

    def edits_for(self, name: str) -> list:
        edits = []
        if name == _CONTENT_TYPES:
            edits.append(self._remove_overrides)
        if name.lower().endswith(".rels"):
            ids = self.removed_ids.get(_rels_source(name), set())
            if ids:
                edits.append(lambda d: _remove_relationship_ids(d, ids))
        elif self.removed_ids.get(name):
            ids = self.removed_ids[name]
            edits.append(lambda d: _remove_references(d, ids))
        if _is_word_part(name) and (COMMENTS in self.labels or TRACKED_CHANGES in self.labels):
            edits.append(_accept_revisions)
            edits.append(_remove_comment_marks)
        return edits

    def _remove_overrides(self, document: minidom.Document) -> None:
        for override in list(document.getElementsByTagName("Override")):
            if override.getAttribute("PartName").lstrip("/").lower() in self.lower_parts:
                _remove(override)


def _plan_removal(archive: zipfile.ZipFile) -> _Removal:
    parts, labels = _find_extras(archive)
    removal = _Removal(set(parts), labels, {p.lower() for p in parts})
    for _, source, root in _relationship_files(archive):
        for rel in root:
            if rel.get("TargetMode") != "External" and \
                    _resolve(source, rel.get("Target", "")).lower() in removal.lower_parts:
                removal.removed_ids.setdefault(source, set()).add(rel.get("Id"))
    return removal


def _remove_relationship_ids(document: minidom.Document, ids: set[str]) -> None:
    for rel in list(document.getElementsByTagName("Relationship")):
        if rel.getAttribute("Id") in ids:
            _remove(rel)


def _remove_references(document: minidom.Document, ids: set[str]) -> None:
    """Drops elements that pointed at a removed part, such as an Excel sheet's comment drawing or a
    PowerPoint slide's comment list, so the file doesn't refer to something that's gone."""
    for element in list(document.getElementsByTagName("*")):
        if element.parentNode is None or element.getAttributeNS(_R, "id") not in ids:
            continue
        parent = element.parentNode
        _remove(parent if parent.localName == "ext" else element)


def _unwrap(element: minidom.Element) -> None:
    parent = element.parentNode
    while element.firstChild is not None:
        parent.insertBefore(element.firstChild, element)
    parent.removeChild(element)


def _accept_revisions(document: minidom.Document) -> None:
    """Accepts every tracked change, as Word's "Accept All Changes" does, leaving the text as it reads now."""
    for name in ("del", "moveFrom"):
        for element in _elements_in(document, _W, name):
            if element.parentNode is None:
                continue
            parent = element.parentNode
            if parent.localName == "trPr" and parent.parentNode is not None:
                _remove(parent.parentNode)  # a deleted table row
            else:
                _remove(element)  # deleted text, or the mark on a deleted paragraph break
    for name in ("ins", "moveTo"):
        for element in _elements_in(document, _W, name):
            if element.parentNode is not None:
                _unwrap(element)
    for name in _REVISION_MARKS - {"del", "moveFrom", "ins", "moveTo"} | {"trackRevisions"}:
        for element in _elements_in(document, _W, name):
            if element.parentNode is not None:
                _remove(element)


def _remove_comment_marks(document: minidom.Document) -> None:
    for name in _COMMENT_MARKS:
        for element in _elements_in(document, _W, name):
            parent = element.parentNode
            if parent is None:
                continue
            _remove(element)
            # A comment reference sits in a run of its own, which would otherwise be left empty.
            if parent.localName == "r" and parent.parentNode is not None and all(
                    c.nodeType != c.ELEMENT_NODE or c.localName == "rPr" for c in parent.childNodes):
                _remove(parent)


def _elements_in(document: minidom.Document, namespace: str, local_name: str) -> list[minidom.Element]:
    return list(document.getElementsByTagNameNS(namespace, local_name))


def _edit_xml(data: bytes, *edits) -> bytes:
    # minidom, not ElementTree: Office relies on the original namespace prefixes staying as they are.
    document = minidom.parseString(data)
    for edit in edits:
        edit(document)
    return document.toxml(encoding="UTF-8", standalone=True)


def _elements(document: minidom.Document, local_name: str) -> list[minidom.Element]:
    return list(document.getElementsByTagNameNS("*", local_name))


def _remove(element: minidom.Element) -> None:
    element.parentNode.removeChild(element)


def _remove_relationships(document: minidom.Document, suffix: str) -> None:
    for rel in list(document.getElementsByTagName("Relationship")):
        if rel.getAttribute("Type").endswith(suffix):
            _remove(rel)


def _remove_word_traces(document: minidom.Document) -> None:
    for name in ("attachedTemplate", "rsids"):
        for element in _elements(document, name):
            _remove(element)


def _remove_saved_path(document: minidom.Document) -> None:
    # Excel records the folder a workbook was last saved in, which usually includes the user name.
    for path in _elements(document, "absPath"):
        node = path
        while node.parentNode is not None and node.parentNode.nodeType == node.ELEMENT_NODE:
            if node.parentNode.localName == "AlternateContent":
                node = node.parentNode
                break
            node = node.parentNode
        _remove(node if node.localName == "AlternateContent" else path)


_EDITS = {
    _ROOT_RELS: lambda d: _remove_relationships(d, "/thumbnail"),
    _SETTINGS_RELS: lambda d: _remove_relationships(d, "/attachedTemplate"),
    _SETTINGS: _remove_word_traces,
    _WORKBOOK: _remove_saved_path,
}

