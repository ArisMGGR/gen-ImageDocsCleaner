"""Runs the bundled ExifTool and FFmpeg, and the byte-level location check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .classifier import classify, is_empty_value
from .models import Cancelled, Category, CleaningError, Tag, check

# The standalone build (PyInstaller) keeps the same layout inside its _internal folder.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "third_party" / "tools"
REPAIR_HINT = ("Please download and unpack File Cleaner again." if getattr(sys, "frozen", False)
               else "Please run Install.bat again.")
EXIFTOOL = TOOLS_DIR / "exiftool" / "exiftool.exe"
FFMPEG = TOOLS_DIR / "ffmpeg" / "ffmpeg.exe"

_LOCATION_MARKERS = [b"GPSLatitude", b"GPSLongitude", b"exif:GPS", b"com.apple.quicktime.location", b"ISO6709"]
_CHUNK = 1 << 20


def missing_tools() -> list[str]:
    return [p.name for p in (EXIFTOOL, FFMPEG) if not p.exists()]


def tool_problems() -> list[str]:
    """Runs each tool once. A file that exists can still fail to start, for example when Windows
    Smart App Control or antivirus blocks it."""
    problems = []
    for path, args in ((EXIFTOOL, ["-ver"]), (FFMPEG, ["-version"])):
        if not path.exists():
            problems.append(f"{path.name} is missing.")
            continue
        try:
            result = run_tool([str(path), *args])
        except OSError as error:
            problems.append(f"{path.name} could not start (blocked by Windows?): {error.strerror or error}")
            continue
        if result.returncode != 0:
            problems.append(f"{path.name} failed with exit code {result.returncode}.")
    return problems


def run_tool(args: list[str], cancel: threading.Event | None = None) -> subprocess.CompletedProcess:
    if not Path(args[0]).exists():
        raise CleaningError(f"{Path(args[0]).name} is missing. {REPAIR_HINT}")

    process = subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    while True:
        try:
            out, err = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                process.kill()
                process.communicate()
                raise Cancelled from None
    return subprocess.CompletedProcess(
        args, process.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _exiftool(args: list[str], cancel: threading.Event | None) -> subprocess.CompletedProcess:
    # ExifTool's Windows command line can't take every Unicode file name. An argument file can.
    handle, arg_file = tempfile.mkstemp(prefix="filecleaner-", suffix=".args")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(["-charset", "filename=utf8", *args]) + "\n")
        return run_tool([str(EXIFTOOL), "-@", arg_file], cancel)
    finally:
        os.unlink(arg_file)


def read_tags(path: str, embedded: bool = False, source: str | None = None,
              cancel: threading.Event | None = None) -> list[Tag]:
    args = ["-json", "-G0:1", "-a", "-api", "largefilesupport=1"]
    if embedded:
        args.append("-ee")  # also reads timed GPS tracks recorded by dashcams and action cameras
    result = _exiftool([*args, path], cancel)
    if not result.stdout.strip():
        first_line = result.stderr.splitlines()[0] if result.stderr.strip() else ""
        raise CleaningError(f"Could not read this file's metadata. {first_line}".strip())
    return parse_tags(result.stdout, source)


def parse_tags(text: str, source: str | None = None) -> list[Tag]:
    tags = []
    # Pairs, not dicts: with -a the same tag can appear twice, and both must be seen.
    for file_pairs in json.loads(text, object_pairs_hook=list):
        for key, raw in file_pairs:
            parts = key.split(":")
            # With -G0:1, ExifTool writes "PDF:Author" rather than "PDF:PDF:Author" when both groups match.
            if len(parts) == 2:
                parts = [parts[0], parts[0], parts[1]]
            if len(parts) != 3:
                continue
            value = _flatten(raw)
            category = classify(*parts)
            if category is not Category.KEPT and is_empty_value(value):
                continue
            tags.append(Tag(category, parts[2], value, source))
    return tags


def _flatten(value) -> str:
    if isinstance(value, list):
        if value and isinstance(value[0], tuple):  # a nested object, from the pairs hook
            return ", ".join(f"{k}: {_flatten(v)}" for k, v in value)
        return ", ".join(_flatten(v) for v in value)
    return str(value)


def strip_image(source: str, target: str, cancel: threading.Event | None = None) -> bool:
    """Writes a copy without metadata. Pixels are untouched; rotation and color profile stay."""
    args = ["-m", "-all=", "--icc_profile:all", "--adobe:all", "-trailer:all="]
    # In a TIFF the main tags sit beside the pixel layout, so "-all=" leaves them. They must be named.
    if Path(source).suffix.lower() in (".tif", ".tiff"):
        args += ["-CommonIFD0=", "-IFD0:DocumentName=", "-IFD0:PageName=", "-IFD0:HostComputer="]
    args += ["-tagsFromFile", "@", "-exif:Orientation", "-o", target, source]
    return _exiftool(args, cancel).returncode == 0 and os.path.exists(target)


def find_location_marker(path: str, cancel: threading.Event | None = None) -> str | None:
    """A second opinion after cleaning, covering places a metadata reader would not parse. The markers
    are long enough that they won't occur by chance inside compressed image or video data."""
    overlap = max(len(m) for m in _LOCATION_MARKERS)
    tail = b""
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            check(cancel)
            data = tail + chunk
            for marker in _LOCATION_MARKERS:
                if marker in data:
                    return marker.decode()
            tail = data[-overlap:]
    return None
