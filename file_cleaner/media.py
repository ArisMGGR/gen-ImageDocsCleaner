from __future__ import annotations

import os
import threading

from . import tools
from .models import Category, CleaningError, ScanResult, Tag


def scan(path: str, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult(tools.read_tags(path, embedded=True, cancel=cancel))
    marker = tools.find_location_marker(path)
    if marker and not result.has_location:
        result.tags.append(Tag(Category.LOCATION, "Location data", f"Hidden marker: {marker}"))
    return result


def clean(source: str, target: str, cancel: threading.Event | None = None) -> list[str]:
    # Streams are copied, never re-encoded. Only real video, audio and subtitle streams are mapped:
    # data streams (where GPS tracks live) and cover art are left out.
    args = [
        str(tools.FFMPEG), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", source,
        "-map", "0:V?", "-map", "0:a?", "-map", "0:s?", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    ]
    if target.lower().endswith(".mp3"):
        args += ["-id3v2_version", "0", "-write_id3v1", "0"]
    args.append(target)

    result = tools.run_tool(args, cancel)
    if result.returncode != 0 or not os.path.exists(target):
        detail = result.stderr.splitlines()[0] if result.stderr.strip() else ""
        raise CleaningError(f"This media file could not be processed. {detail}".strip())
    return []
