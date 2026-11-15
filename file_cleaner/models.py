from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum


class Category(Enum):
    LOCATION = "Location"
    DEVICE = "Device"
    DATES = "Dates"
    AUTHOR = "Author"
    SOFTWARE = "Software"
    OTHER = "Other"
    KEPT = "Kept"  # display data preserved on purpose (rotation, color profile)
    TECHNICAL = "Technical"  # describes the format and reveals nothing about the file's origin


@dataclass(frozen=True)
class Tag:
    category: Category
    name: str
    value: str
    source: str | None = None  # embedded part the tag came from, or None for the file itself

    @property
    def must_be_removed(self) -> bool:
        return self.category not in (Category.KEPT, Category.TECHNICAL)


@dataclass
class ScanResult:
    tags: list[Tag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # things cleaning leaves in place
    # Content removed only on request, such as "comments" or "tracked changes", in display order.
    extras: list[str] = field(default_factory=list)

    @property
    def has_location(self) -> bool:
        return any(t.category is Category.LOCATION for t in self.tags)

    @property
    def removable(self) -> list[Tag]:
        return [t for t in self.tags if t.must_be_removed]


@dataclass
class CleanResult:
    output_path: str
    after: ScanResult
    notes: list[str]


class CleaningError(Exception):
    """A problem to show the user. The message is written for them."""


class Cancelled(Exception):
    pass


def check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled
