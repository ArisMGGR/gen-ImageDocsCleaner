import os
import shutil
import time
import zipfile
from datetime import datetime

import docx
import openpyxl
import openpyxl.comments
import pptx
import pytest
from conftest import exif
from PIL import Image

from file_cleaner import engine
from file_cleaner.models import Category, CleaningError


def make_docx(path, photo=None):
    document = docx.Document()
    document.add_paragraph("Visible body text")
    if photo:
        document.add_picture(photo)
    props = document.core_properties
    props.author = "Jane Secret"
    props.last_modified_by = "John Secret"
    props.title = "Confidential title"
    props.created = datetime(2020, 5, 1)
    document.save(path)
    return str(path)


def edit_zip(path, changes):
    temp = str(path) + ".tmp"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            if name in changes:
                value = changes.pop(name)
                if value is not None:
                    target.writestr(name, value)
            else:
                target.writestr(name, source.read(name))
        for name, value in changes.items():
            target.writestr(name, value)
    shutil.move(temp, path)


def all_text(path):
    with zipfile.ZipFile(path) as archive:
        return "\n".join(archive.read(n).decode("latin-1") for n in archive.namelist())


def geotagged_jpeg(path):
    Image.new("RGB", (64, 48), "red").save(path)
    exif("-q", "-overwrite_original", "-GPSLatitude=37.7", "-GPSLatitudeRef=N", "-Make=SecretCam", path)
    return str(path)


def test_scan_finds_properties(tmp_path):
    scan = engine.scan(make_docx(tmp_path / "a.docx"))

    assert any(t.category is Category.AUTHOR and t.value == "Jane Secret" for t in scan.tags)
    assert any(t.name == "created" for t in scan.tags)


def test_clean_removes_properties_and_keeps_body(tmp_path, out):
    source = make_docx(tmp_path / "a.docx")
    original = open(source, "rb").read()

    result = engine.clean(source, out)

    assert open(source, "rb").read() == original
    assert result.after.removable == []
    assert "Secret" not in all_text(result.output_path)
    assert docx.Document(result.output_path).paragraphs[0].text == "Visible body text"


def test_finds_core_properties_stored_under_a_generated_name(tmp_path, out):
    source = make_docx(tmp_path / "a.docx")
    with zipfile.ZipFile(source) as archive:
        core = archive.read("docProps/core.xml")
        rels = archive.read("_rels/.rels").decode()
    moved = "package/services/metadata/core-properties/abc123.psmdcp"
    edit_zip(source, {"docProps/core.xml": None, moved: core, "_rels/.rels": rels.replace("docProps/core.xml", moved)})

    assert any(t.value == "Jane Secret" for t in engine.scan(source).tags)
    assert "Secret" not in all_text(engine.clean(source, out).output_path)


def test_removes_thumbnail_and_its_relationship(tmp_path, out):
    source = make_docx(tmp_path / "a.docx")
    with zipfile.ZipFile(source) as archive:
        rels = archive.read("_rels/.rels").decode()
    thumb = ('<Relationship Id="rThumb" Target="docProps/thumbnail.jpeg" Type="http://schemas.openxmlformats.org/'
             'package/2006/relationships/metadata/thumbnail"/></Relationships>')
    edit_zip(source, {"docProps/thumbnail.jpeg": b"jpeg", "_rels/.rels": rels.replace("</Relationships>", thumb)})

    result = engine.clean(source, out)

    with zipfile.ZipFile(result.output_path) as archive:
        assert "docProps/thumbnail.jpeg" not in archive.namelist()
        assert b"thumbnail" not in archive.read("_rels/.rels")


def test_removes_word_revision_ids_and_template_path(tmp_path, out):
    source = make_docx(tmp_path / "a.docx")
    with zipfile.ZipFile(source) as archive:
        settings = archive.read("word/settings.xml").decode()
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    settings = settings.replace("</w:settings>", f'<w:rsids xmlns:w="{w}"><w:rsidRoot w:val="00AB12CD"/></w:rsids></w:settings>')
    edit_zip(source, {"word/settings.xml": settings})

    assert any(t.name == "Revision IDs" for t in engine.scan(source).tags)
    result = engine.clean(source, out)

    assert "00AB12CD" not in all_text(result.output_path)
    docx.Document(result.output_path)


def test_removes_excel_saved_path(tmp_path, out):
    source = str(tmp_path / "a.xlsx")
    book = openpyxl.Workbook()
    book.properties.creator = "Jane Secret"
    book.active["A1"] = "cell value"
    book.save(source)
    with zipfile.ZipFile(source) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
    saved_path = ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                  '<mc:Choice Requires="x15"><x15ac:absPath xmlns:x15ac="http://schemas.microsoft.com/office/'
                  'spreadsheetml/2010/11/ac" url="C:\\Users\\jane\\Documents\\"/></mc:Choice></mc:AlternateContent>')
    edit_zip(source, {"xl/workbook.xml": workbook.replace("<sheets>", saved_path + "<sheets>")})

    assert any("jane" in t.value for t in engine.scan(source).tags)
    result = engine.clean(source, out)

    assert "jane" not in all_text(result.output_path)
    assert "Secret" not in all_text(result.output_path)
    assert openpyxl.load_workbook(result.output_path).active["A1"].value == "cell value"


def test_pptx_with_geotagged_photo(tmp_path, out):
    photo = geotagged_jpeg(tmp_path / "photo.jpg")
    source = str(tmp_path / "a.pptx")
    deck = pptx.Presentation()
    deck.core_properties.author = "Jane Secret"
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(photo, 0, 0)
    deck.save(source)

    before = engine.scan(source)
    result = engine.clean(source, out)

    assert any(t.category is Category.LOCATION and t.source for t in before.tags)
    assert result.after.removable == []
    assert any("embedded" in n for n in result.notes)
    assert len(pptx.Presentation(result.output_path).slides) == 1


def test_docx_with_geotagged_photo(tmp_path, out):
    photo = geotagged_jpeg(tmp_path / "photo.jpg")
    source = make_docx(tmp_path / "a.docx", photo)

    before = engine.scan(source)
    result = engine.clean(source, out)

    assert before.has_location
    assert result.after.removable == []
    assert len(docx.Document(result.output_path).inline_shapes) == 1


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TRACKED_BODY = (
    f'<w:p xmlns:w="{W}"><w:r><w:t xml:space="preserve">Kept </w:t></w:r>'
    '<w:ins w:id="91" w:author="Ingrid Reviewer" w:date="2024-01-01T00:00:00Z"><w:r><w:t>added</w:t></w:r></w:ins>'
    '<w:del w:id="92" w:author="Ingrid Reviewer" w:date="2024-01-01T00:00:00Z"><w:r><w:delText>gone</w:delText></w:r></w:del>'
    '<w:r><w:rPr><w:b/><w:rPrChange w:id="93" w:author="Ingrid Reviewer"><w:rPr/></w:rPrChange></w:rPr>'
    '<w:t xml:space="preserve"> bold</w:t></w:r></w:p>')


def make_reviewed_docx(path):
    """A document with a real comment (python-docx writes the parts and relationships Word does) and
    tracked insertions, deletions and formatting changes."""
    document = docx.Document()
    paragraph = document.add_paragraph("Commented text")
    document.add_comment(paragraph.runs, text="Private remark", author="Carl Commenter", initials="CC")
    document.save(path)
    with zipfile.ZipFile(path) as archive:
        body = archive.read("word/document.xml").decode()
        settings = archive.read("word/settings.xml").decode()
    edit_zip(path, {
        "word/document.xml": body.replace("<w:sectPr", TRACKED_BODY.replace(f' xmlns:w="{W}"', "") + "<w:sectPr", 1),
        "word/settings.xml": settings.replace("</w:settings>", "<w:trackRevisions/></w:settings>"),
    })
    return str(path)


def test_comments_and_tracked_changes_are_reported_and_kept_by_default(tmp_path, out):
    source = make_reviewed_docx(tmp_path / "a.docx")

    scan = engine.scan(source)
    result = engine.clean(source, out)

    assert scan.extras == ["comments", "tracked changes"]
    assert result.after.extras == ["comments", "tracked changes"]
    assert "Carl Commenter" in all_text(result.output_path)


def test_removes_comments_and_accepts_tracked_changes(tmp_path, out):
    source = make_reviewed_docx(tmp_path / "a.docx")

    result = engine.clean(source, out, extras=True)

    text = all_text(result.output_path)
    assert result.after.extras == []
    assert "Removed comments and tracked changes." in result.notes
    for secret in ("Carl Commenter", "Private remark", "Ingrid Reviewer", "gone", "trackRevisions", "comments.xml"):
        assert secret not in text
    with zipfile.ZipFile(result.output_path) as archive:
        assert not any("comment" in n.lower() for n in archive.namelist())
    paragraphs = [p.text for p in docx.Document(result.output_path).paragraphs]
    assert paragraphs == ["Commented text", "Kept added bold"]


def test_accepting_a_deleted_table_row_removes_the_row(tmp_path, out):
    source = make_docx(tmp_path / "a.docx")
    document = docx.Document(source)
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "stays"
    table.cell(1, 0).text = "deleted row"
    document.save(source)
    with zipfile.ZipFile(source) as archive:
        body = archive.read("word/document.xml").decode()
    marked = body.replace("<w:tr>", "<w:tr>", 1)
    rows = marked.split("<w:tr>")
    rows[2] = '<w:trPr><w:del w:id="5" w:author="Ingrid Reviewer"/></w:trPr>' + rows[2]
    edit_zip(source, {"word/document.xml": "<w:tr>".join(rows)})

    assert engine.scan(source).extras == ["tracked changes"]
    result = engine.clean(source, out, extras=True)

    table = docx.Document(result.output_path).tables[0]
    assert [r.cells[0].text for r in table.rows] == ["stays"]


def test_removes_excel_comments_and_their_drawing(tmp_path, out):
    source = str(tmp_path / "a.xlsx")
    book = openpyxl.Workbook()
    book.active["A1"] = "cell value"
    book.active["A1"].comment = openpyxl.comments.Comment("Private remark", "Carl Commenter")
    book.save(source)

    assert engine.scan(source).extras == ["comments"]
    result = engine.clean(source, out, extras=True)

    text = all_text(result.output_path)
    assert "Carl Commenter" not in text and "Private remark" not in text
    assert "legacyDrawing" not in text and "commentsDrawing" not in text
    sheet = openpyxl.load_workbook(result.output_path).active
    assert sheet["A1"].value == "cell value" and sheet["A1"].comment is None


def test_removes_speaker_notes_and_powerpoint_comments(tmp_path, out):
    source = str(tmp_path / "a.pptx")
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.notes_slide.notes_text_frame.text = "Private speaker note"
    deck.save(source)
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(source) as archive:
        slide_rels = archive.read("ppt/slides/_rels/slide1.xml.rels").decode()
        presentation_rels = archive.read("ppt/_rels/presentation.xml.rels").decode()
        types = archive.read("[Content_Types].xml").decode()
    edit_zip(source, {
        "ppt/comments/comment1.xml": f'<p:cmLst xmlns:p="{p}"><p:cm authorId="0"><p:text>Private remark</p:text></p:cm></p:cmLst>',
        "ppt/commentAuthors.xml": f'<p:cmAuthorLst xmlns:p="{p}"><p:cmAuthor id="0" name="Carl Commenter"/></p:cmAuthorLst>',
        "ppt/slides/_rels/slide1.xml.rels": slide_rels.replace(
            "</Relationships>", f'<Relationship Id="rC1" Type="{rel_type}/comments" Target="../comments/comment1.xml"/></Relationships>'),
        "ppt/_rels/presentation.xml.rels": presentation_rels.replace(
            "</Relationships>", f'<Relationship Id="rA1" Type="{rel_type}/commentAuthors" Target="commentAuthors.xml"/></Relationships>'),
        "[Content_Types].xml": types.replace("</Types>", (
            '<Override PartName="/ppt/comments/comment1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.comments+xml"/>'
            '<Override PartName="/ppt/commentAuthors.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"/></Types>')),
    })

    assert engine.scan(source).extras == ["comments", "speaker notes"]
    result = engine.clean(source, out, extras=True)

    text = all_text(result.output_path)
    for secret in ("Private speaker note", "Private remark", "Carl Commenter", "notesSlide1", "comment1.xml"):
        assert secret not in text
    reopened = pptx.Presentation(result.output_path)
    assert len(reopened.slides) == 1 and not reopened.slides[0].has_notes_slide


def test_never_overwrites_existing_output(tmp_path, out):
    source = make_docx(tmp_path / "report.docx")

    first = engine.clean(source, out)
    second = engine.clean(source, out)

    assert os.path.basename(first.output_path) == "report.docx"
    assert os.path.basename(second.output_path) == "report (1).docx"


def test_fresh_dates_and_subfolder_preserved(tmp_path, out):
    source = make_docx(tmp_path / "report.docx")
    os.utime(source, (1420070400, 1420070400))

    result = engine.clean(source, out, os.path.join("Trip", "Day1"))

    assert result.output_path == os.path.join(out, "Trip", "Day1", "report.docx")
    assert os.path.getmtime(result.output_path) > time.time() - 60


def test_damaged_file_fails_without_output(tmp_path, out):
    source = tmp_path / "broken.docx"
    source.write_text("not a zip file")

    with pytest.raises(CleaningError):
        engine.clean(str(source), out)

    assert not os.path.exists(out)


def test_collect_keeps_dropped_folder_structure(tmp_path):
    (tmp_path / "Trip" / "Day1").mkdir(parents=True)
    (tmp_path / "Trip" / "Day1" / "a.jpg").write_text("")
    (tmp_path / "single.jpg").write_text("")

    found = engine.collect([str(tmp_path / "Trip"), str(tmp_path / "single.jpg")])

    assert any(f.relative_folder == os.path.join("Trip", "Day1") for f in found)
    assert any(f.path.endswith("single.jpg") and f.relative_folder == "" for f in found)
