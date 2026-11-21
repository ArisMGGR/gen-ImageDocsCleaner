from __future__ import annotations

import ctypes
import html
import importlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import APP_ID, engine, tools
from .models import Cancelled, Category, CleaningError, Tag

ICON = Path(__file__).resolve().parent.parent / "assets" / "FileCleaner.ico"
SETTINGS_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FileCleaner" / "settings.json"
MAX_PARALLEL = 3

BG, PANEL, HOVER, LINE, TEXT, MUTED = "#0B0B0D", "#141417", "#1D1D21", "#2A2A2F", "#F2F2F3", "#9A9AA3"
RED, RED_HOVER, RED_PRESSED, RED_TINT = "#E5142B", "#FF2E45", "#B80F22", "#3A0B12"

STYLE = f"""
* {{ font-family: "Segoe UI"; font-size: 13px; color: {TEXT}; outline: none; }}
QMainWindow, #root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
#brand {{ font-size: 13px; font-weight: 700; letter-spacing: 1px; }}
#mark {{ background: {RED}; }}
#muted {{ color: {MUTED}; font-size: 12px; }}
#fieldLabel {{ color: {MUTED}; font-size: 10px; font-weight: 600; }}
#dropTitle {{ font-size: 24px; font-weight: 600; }}
#dropPanel {{ background: {PANEL}; border: 1px dashed {LINE}; border-radius: 6px; }}
#footer {{ border-top: 1px solid {LINE}; }}
QPushButton {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px; padding: 7px 14px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:focus {{ border-color: {RED}; }}
QPushButton:disabled {{ color: #55555B; }}
#primary {{ background: {RED}; border-color: {RED}; font-weight: 600; padding: 9px 24px; }}
#primary:hover {{ background: {RED_HOVER}; border-color: {RED_HOVER}; }}
#primary:pressed {{ background: {RED_PRESSED}; }}
#primary:disabled {{ background: #4A1219; border-color: #4A1219; color: #8E7276; }}
QTreeWidget {{ background: {BG}; border: 1px solid {LINE}; border-radius: 6px; }}
QTreeWidget::item {{ padding: 8px 6px; border-bottom: 1px solid {LINE}; }}
QTreeWidget::item:hover {{ background: {HOVER}; }}
QTreeWidget::item:selected {{ background: {RED_TINT}; color: {TEXT}; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 6px; font-size: 11px; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; padding: 12px; }}
QProgressBar {{ background: {LINE}; border: none; max-height: 3px; }}
QProgressBar::chunk {{ background: {RED}; }}
QSplitter::handle {{ background: {BG}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {LINE}; border-radius: 3px; background: {PANEL}; }}
QCheckBox::indicator:hover {{ border-color: {MUTED}; }}
QCheckBox::indicator:checked {{ background: {RED}; border-color: {RED}; }}
QCheckBox:disabled {{ color: #55555B; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


class State(Enum):
    SCANNING = "Reading…"
    READY = "Ready"
    CLEANING = "Cleaning…"
    CLEANED = "Cleaned"
    FAILED = "Failed"
    SKIPPED = "Skipped"


@dataclass(eq=False)
class Item:
    path: str
    relative_folder: str
    row: QTreeWidgetItem
    state: State = State.SCANNING
    found: list[Tag] = field(default_factory=list)
    kept: list[Tag] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)  # comments and the like, removed only on request
    has_location: bool = False
    error: str | None = None
    output_path: str | None = None

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def status(self) -> str:
        count = len(self.found)
        if self.state is State.CLEANED:
            return f"Cleaned · {count} removed"
        if self.state is State.READY:
            if self.has_location:
                return f"LOCATION · {count} fields"
            return f"{count} fields" if count else "No metadata"
        return self.state.value


class Signals(QObject):
    """Workers report through these, so the window only ever changes on the UI thread."""
    scanned = Signal(object, object, object)
    cleaned = Signal(object, object, object)


def load_settings(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(settings, dict):
        return {}
    if not isinstance(settings.get("output_folder", ""), str):
        del settings["output_folder"]  # the folder is simply asked for again
    return settings


def save_settings(path: Path, settings: dict) -> None:
    """Stores only preferences. File names and history are never written anywhere."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings), encoding="utf-8")
    except OSError:
        pass  # not worth interrupting anyone over: the folder is simply asked for again next time


def describe(error: Exception) -> str:
    return str(error) if isinstance(error, CleaningError) else f"Unexpected problem: {error}"


class MainWindow(QMainWindow):
    def __init__(self, settings_path: Path = SETTINGS_PATH):
        super().__init__()
        self.setWindowTitle("File Cleaner")
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1120, 720)
        self.setMinimumSize(820, 520)
        self.setAcceptDrops(True)

        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        self.items: dict[str, Item] = {}
        self.pool = ThreadPoolExecutor(MAX_PARALLEL)
        self.cancel = threading.Event()
        self.closing = threading.Event()  # stops scans too; Cancel only stops cleaning
        self.pending = 0
        self.busy = False

        self.signals = Signals()
        self.signals.scanned.connect(self._on_scanned)
        self.signals.cleaned.connect(self._on_cleaned)

        self._build()
        self._refresh()
        self.stack.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.stack.setFocus()  # so no button starts out looking selected

    def _build(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(18, 14, 18, 14)
        toolbar.setSpacing(8)
        mark = QFrame(objectName="mark")
        mark.setFixedSize(10, 10)
        toolbar.addWidget(mark)
        toolbar.addWidget(QLabel("FILE CLEANER", objectName="brand"))
        toolbar.addSpacing(16)
        self.add_files = QPushButton("Add files", clicked=self._choose_files)
        self.add_folder = QPushButton("Add folder", clicked=self._choose_folder)
        self.clear = QPushButton("Clear list", clicked=self._clear)
        for button in (self.add_files, self.add_folder, self.clear):
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.summary = QLabel(objectName="muted")
        toolbar.addWidget(self.summary)
        page.addLayout(toolbar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_drop_panel())
        self.stack.addWidget(self._build_results())
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(18, 0, 18, 14)
        wrapper.addWidget(self.stack)
        page.addLayout(wrapper, 1)

        self.progress = QProgressBar(textVisible=False)
        self.progress.hide()
        page.addWidget(self.progress)
        page.addWidget(self._build_footer())

    def _build_drop_panel(self) -> QWidget:
        panel = QFrame(objectName="dropPanel")
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        for text, name in (
            ("Drop files or folders here", "dropTitle"),
            ("Removes location and other metadata. Your originals are never changed.", ""),
            ("Images, PDF, Word, Excel, PowerPoint, video and audio", "muted"),
        ):
            label = QLabel(text, objectName=name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        return panel

    def _build_results(self) -> QWidget:
        self.tree = QTreeWidget(columnCount=3, rootIsDecorated=False, alternatingRowColors=False)
        self.tree.setHeaderLabels(["File", "Folder", "Status"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(2, 190)
        self.tree.currentItemChanged.connect(lambda *_: self._show_details())

        # Metadata is untrusted, so links never open and the HTML is always escaped.
        self.details = QTextBrowser(openLinks=False, openExternalLinks=False)

        splitter = QSplitter(Qt.Orientation.Horizontal, handleWidth=12, childrenCollapsible=False)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.details)
        splitter.setSizes([560, 500])
        return splitter

    def _build_footer(self) -> QWidget:
        footer = QFrame(objectName="footer")
        rows = QVBoxLayout(footer)
        rows.setContentsMargins(18, 12, 18, 12)
        rows.setSpacing(10)
        layout = QHBoxLayout()
        layout.setSpacing(8)
        rows.addLayout(layout)

        self.remove_extras = QCheckBox("Also remove comments, tracked changes, speaker notes and PDF attachments")
        self.remove_extras.setToolTip(
            "Documents only. Tracked changes are accepted, so the text reads as it does now.")
        self.remove_extras.setChecked(bool(self.settings.get("remove_extras")))
        self.remove_extras.toggled.connect(self._set_remove_extras)
        rows.addWidget(self.remove_extras)

        info = QVBoxLayout()
        info.setSpacing(0)
        info.addWidget(QLabel("SAVE CLEANED COPIES TO", objectName="fieldLabel"))
        self.output_label = QLabel()
        info.addWidget(self.output_label)
        layout.addLayout(info)
        layout.addSpacing(6)

        self.change_output = QPushButton("Change", clicked=self._choose_output)
        self.open_output = QPushButton("Open", clicked=self._open_output)
        layout.addWidget(self.change_output)
        layout.addWidget(self.open_output)
        layout.addStretch()

        self.cancel_button = QPushButton("Cancel", clicked=self.cancel.set)
        self.cancel_button.hide()
        self.clean_button = QPushButton(objectName="primary", clicked=self._start_clean)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.clean_button)
        return footer

    # Adding files

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and not self.busy:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        self.add_paths(paths)

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add a folder")
        if path:
            self.add_paths([path])

    def add_paths(self, paths: list[str]) -> None:
        for found in engine.collect(paths):
            path = os.path.normpath(found.path)
            if path in self.items:
                continue

            row = QTreeWidgetItem([os.path.basename(path), os.path.dirname(path), ""])
            item = Item(path, found.relative_folder, row)
            row.setData(0, Qt.ItemDataRole.UserRole, path)
            self.items[path] = item
            self.tree.addTopLevelItem(row)

            if engine.is_supported(path):
                self.pool.submit(self._scan, item)
            else:
                item.state = State.SKIPPED
                item.messages.append("This file type is not supported, so it was skipped.")
            self._update_row(item)

        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._refresh()

    def _scan(self, item: Item) -> None:
        try:
            self.signals.scanned.emit(item, engine.scan(item.path, self.closing), None)
        except Exception as error:  # a broken file must never take the app down
            self.signals.scanned.emit(item, None, error)

    def _on_scanned(self, item: Item, scan, error) -> None:
        if self.items.get(item.path) is not item:
            return  # removed by Clear list while it was being read

        if error is not None:
            item.error = describe(error)
            item.state = State.FAILED
        else:
            order = list(Category)
            tags = sorted(scan.tags, key=lambda t: order.index(t.category))
            item.found = [t for t in tags if t.must_be_removed]
            item.kept = [t for t in tags if t.category is Category.KEPT and t.name in ("Orientation", "Rotation")]
            item.messages.extend(scan.warnings)
            item.extras = scan.extras
            item.has_location = scan.has_location
            item.state = State.READY
        self._update_row(item)
        self._refresh()

    # Cleaning

    def _start_clean(self) -> None:
        folder = self.settings.get("output_folder")
        if not folder or not os.path.isdir(folder):
            self._choose_output()
            folder = self.settings.get("output_folder")
            if not folder or not os.path.isdir(folder):
                return

        queue = [i for i in self.items.values() if i.state is State.READY]
        self.cancel.clear()
        self.pending = len(queue)
        self.progress.setRange(0, len(queue))
        self.progress.setValue(0)
        self._set_busy(True)

        for item in queue:
            item.state = State.CLEANING
            self._update_row(item)
            self.pool.submit(self._clean, item, folder, self.remove_extras.isChecked())
        self._refresh()

    def _clean(self, item: Item, folder: str, extras: bool) -> None:
        try:
            if self.cancel.is_set():
                raise Cancelled
            result = engine.clean(item.path, folder, item.relative_folder, self.cancel, extras)
            self.signals.cleaned.emit(item, result, None)
        except Exception as error:
            self.signals.cleaned.emit(item, None, error)

    def _on_cleaned(self, item: Item, result, error) -> None:
        if isinstance(error, Cancelled):
            item.state = State.READY
        elif error is not None:
            item.error = describe(error)
            item.state = State.FAILED
        else:
            item.output_path = result.output_path
            item.messages.extend(result.notes)
            item.extras = result.after.extras
            item.state = State.CLEANED

        self.pending -= 1
        self.progress.setValue(self.progress.maximum() - self.pending)
        if self.pending == 0:
            self._set_busy(False)
        self._update_row(item)
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        for button in (self.add_files, self.add_folder, self.clear, self.change_output, self.remove_extras):
            button.setEnabled(not busy)

    # Other actions

    def _clear(self) -> None:
        self.items.clear()
        self.tree.clear()
        self._refresh()

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose where cleaned copies are saved")
        if folder:
            self.set_output_folder(folder)

    def set_output_folder(self, folder: str) -> None:
        self.settings["output_folder"] = os.path.normpath(folder)
        save_settings(self.settings_path, self.settings)
        self._refresh()

    def _set_remove_extras(self, checked: bool) -> None:
        self.settings["remove_extras"] = checked
        save_settings(self.settings_path, self.settings)
        self._show_details()

    def _open_output(self) -> None:
        folder = self.settings.get("output_folder")
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # Display

    def _update_row(self, item: Item) -> None:
        item.row.setText(2, item.status)
        color = {State.FAILED: RED_HOVER, State.CLEANED: TEXT}.get(item.state, MUTED)
        if item.state is State.READY and item.has_location:
            color = RED_HOVER
        item.row.setForeground(2, QBrush(QColor(color)))
        item.row.setForeground(1, QBrush(QColor(MUTED)))
        if self.tree.currentItem() is item.row:
            self._show_details()

    def _refresh(self) -> None:
        items = list(self.items.values())
        self.stack.setCurrentIndex(1 if items else 0)

        ready = sum(i.state is State.READY for i in items)
        self.clean_button.setText("Clean 1 file" if ready == 1 else f"Clean {ready} files")
        self.clean_button.setEnabled(ready > 0 and not self.busy)
        self.output_label.setText(self.settings.get("output_folder") or "Not chosen yet")

        parts = ["1 file" if len(items) == 1 else f"{len(items)} files"]
        for label, count in (
            ("reading {}", sum(i.state is State.SCANNING for i in items)),
            ("{} with location data", sum(i.has_location and i.state is not State.CLEANED for i in items)),
            ("{} cleaned", sum(i.state is State.CLEANED for i in items)),
            ("{} failed", sum(i.state is State.FAILED for i in items)),
        ):
            if count:
                parts.append(label.format(count))
        self.summary.setText(" · ".join(parts) if items else "")

    def _show_details(self) -> None:
        row = self.tree.currentItem()
        item = self.items.get(row.data(0, Qt.ItemDataRole.UserRole)) if row else None
        self.details.setHtml(self._details_html(item, self.remove_extras.isChecked()) if item else "")

    @staticmethod
    def _details_html(item: Item, remove_extras: bool = False) -> str:
        esc = html.escape

        def section(title: str) -> str:
            return f'<p style="color:{MUTED};font-size:11px;font-weight:600;margin:16px 0 4px 0">{title}</p>'

        def rows(tags: list[Tag]) -> str:
            cells = []
            for tag in tags:
                color = RED_HOVER if tag.category is Category.LOCATION else TEXT
                name_color = RED_HOVER if tag.category is Category.LOCATION else MUTED
                value = esc(tag.value[:300])
                if tag.source:
                    value += f'<br><span style="color:{MUTED};font-size:11px">in {esc(tag.source)}</span>'
                cells.append(f'<tr><td width="170" style="color:{name_color};padding:2px 10px 2px 0">{esc(tag.name)}</td>'
                             f'<td style="color:{color};padding:2px 0">{value}</td></tr>')
            return f'<table cellspacing="0">{"".join(cells)}</table>'

        parts = [f'<p style="font-size:16px;font-weight:600;margin:0">{esc(item.name)}</p>',
                 f'<p style="color:{MUTED};font-size:12px;margin:2px 0 0 0">{esc(item.status)}</p>']
        if item.error:
            parts.append(f'<p style="color:{RED_HOVER};margin-top:10px">{esc(item.error)}</p>')
        messages = list(item.messages)
        if item.extras:
            what = _join(item.extras).capitalize()  # escaped with the other messages below
            if item.state is State.CLEANED:
                messages.append(f"{what} were kept, as chosen.")
            elif remove_extras:
                messages.append(f"{what} will be removed.")
            else:
                messages.append(f"{what} will stay in the file, with the names of the people who wrote them. "
                                "Tick “Also remove comments…” below to remove them.")
        if messages:
            parts.append(section("PLEASE NOTE"))
            parts.extend(f'<p style="margin:0 0 4px 0">{esc(m)}</p>' for m in messages)
        if item.output_path:
            parts.append(section("CLEANED COPY"))
            parts.append(f'<p style="margin:0">{esc(item.output_path)}</p>')
            parts.append(f'<p style="color:{MUTED};font-size:12px;margin-top:4px">'
                         'Re-read after cleaning: none of the fields below remain.</p>')
        if item.kept:
            parts.append(section("KEPT SO THE FILE DISPLAYS CORRECTLY"))
            parts.append(rows(item.kept))
        if item.found:
            parts.append(section("FOUND IN THE ORIGINAL"))
            parts.append(rows(item.found))
        return "".join(parts)

    def closeEvent(self, event) -> None:
        self.cancel.set()
        self.closing.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _use_dark_title_bar(window: QMainWindow) -> None:
    try:
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass  # purely cosmetic


def selftest() -> list[str]:
    """Checks that every file type can actually be handled: runs both tools and loads each cleaner,
    since a library or program can be present on disk and still be blocked from running."""
    problems = tools.tool_problems()
    for name in ("images", "media", "pdf", "office"):
        try:
            importlib.import_module(f"{__package__}.{name}")
        except (ImportError, OSError) as error:
            problems.append(f"The {name} cleaner could not load: {error}")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if "--selftest" in argv:
        problems = selftest()
        print("\n".join(problems) if problems else "OK")
        return 1 if problems else 0

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    window = MainWindow()
    _use_dark_title_bar(window)
    window.show()

    missing = tools.missing_tools()
    if missing:
        QMessageBox.warning(window, "File Cleaner",
                            f"{', '.join(missing)} could not be found. {tools.REPAIR_HINT}")
    if argv:
        window.add_paths(argv)
    return app.exec()
