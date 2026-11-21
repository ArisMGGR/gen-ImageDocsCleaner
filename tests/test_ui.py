"""Drives the real window through the whole flow, without showing it on screen."""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import exif
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from file_cleaner import ui


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(ui.STYLE)
    return application


def dispose(win):
    """Closes the window and deletes it now. A window that has run a scan and is left for interpreter
    shutdown crashes the test process on exit (seen with PySide6 6.12 on Python 3.14), after every test passed."""
    win.close()
    win.pool.shutdown(wait=True)
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app, tmp_path):
    win = ui.MainWindow(tmp_path / "settings.json")
    yield win
    dispose(win)


def wait_for(app, condition, timeout=30):
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, "timed out"
        app.processEvents()
        time.sleep(0.01)


def geotagged(path):
    Image.new("RGB", (64, 48), "red").save(path)
    exif("-q", "-overwrite_original", "-GPSLatitude=37.7", "-GPSLatitudeRef=N", "-Make=<b>SecretCam</b>", path)
    return str(path)


def settled(window):
    return all(i.state not in (ui.State.SCANNING, ui.State.CLEANING) for i in window.items.values())


def test_full_flow(app, window, tmp_path):
    photo = geotagged(tmp_path / "photo.jpg")
    notes = tmp_path / "notes.txt"
    notes.write_text("x")

    assert window.stack.currentIndex() == 0
    window.add_paths([photo, str(notes), photo])
    assert window.stack.currentIndex() == 1
    assert len(window.items) == 2
    wait_for(app, lambda: settled(window))

    item = window.items[os.path.normpath(photo)]
    assert item.status.startswith("LOCATION")
    assert window.items[os.path.normpath(str(notes))].state is ui.State.SKIPPED
    assert window.clean_button.text() == "Clean 1 file"

    window.tree.setCurrentItem(item.row)
    assert "GPSLatitude" in window.details.toPlainText()
    assert "<b>SecretCam</b>" in window.details.toPlainText()  # shown literally, never as markup

    out = tmp_path / "out"
    out.mkdir()
    window.set_output_folder(str(out))
    window.clean_button.click()
    wait_for(app, lambda: not window.busy)

    assert item.state is ui.State.CLEANED
    assert (out / "photo.jpg").exists()
    assert "CLEANED COPY" in window.details.toPlainText()
    assert window.clean_button.isEnabled() is False
    assert "1 cleaned" in window.summary.text()


def test_dropping_a_folder_adds_its_files(app, window, tmp_path):
    folder = tmp_path / "Trip"
    folder.mkdir()
    geotagged(folder / "a.jpg")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(folder))])

    window.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    wait_for(app, lambda: settled(window))

    item = next(iter(window.items.values()))
    assert item.relative_folder == "Trip"
    assert item.has_location


def test_failed_file_is_reported(app, window, tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_text("not a zip")

    window.add_paths([str(broken)])
    wait_for(app, lambda: settled(window))

    item = window.items[os.path.normpath(str(broken))]
    assert item.state is ui.State.FAILED
    assert "damaged" in item.error


def test_output_folder_is_remembered(app, tmp_path):
    settings = tmp_path / "settings.json"
    first = ui.MainWindow(settings)
    first.set_output_folder(str(tmp_path))
    dispose(first)

    second = ui.MainWindow(settings)
    assert str(tmp_path) in second.output_label.text()
    dispose(second)


def test_remove_extras_option_is_remembered_and_applied(app, tmp_path):
    import docx

    source = tmp_path / "review.docx"
    document = docx.Document()
    paragraph = document.add_paragraph("Body")
    document.add_comment(paragraph.runs, text="Private remark", author="Carl Commenter")
    document.save(source)
    settings = tmp_path / "settings.json"
    out = tmp_path / "out"
    out.mkdir()

    first = ui.MainWindow(settings)
    first.add_paths([str(source)])
    wait_for(app, lambda: settled(first))
    assert "will stay in the file" in first.details.toPlainText()
    first.remove_extras.setChecked(True)
    assert "Comments will be removed." in first.details.toPlainText()
    dispose(first)

    second = ui.MainWindow(settings)
    assert second.remove_extras.isChecked()
    second.set_output_folder(str(out))
    second.add_paths([str(source)])
    wait_for(app, lambda: settled(second))
    second.clean_button.click()
    wait_for(app, lambda: not second.busy)

    item = next(iter(second.items.values()))
    assert item.state is ui.State.CLEANED, item.error
    assert item.extras == []
    assert "Removed comments." in second.details.toPlainText()
    dispose(second)


def test_selftest_reports_tools_and_libraries_that_cannot_run(monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_text("not a program")  # exists on disk, but Windows cannot start it
    monkeypatch.setattr(ui.tools, "FFMPEG", fake)
    monkeypatch.setitem(sys.modules, "file_cleaner.pdf", None)  # as if pikepdf were blocked

    problems = ui.selftest()

    assert any(p.startswith("ffmpeg.exe could not start") for p in problems), problems
    assert any(p.startswith("The pdf cleaner could not load") for p in problems), problems
    assert not any("exiftool" in p or "images" in p for p in problems), problems
