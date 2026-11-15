import pytest

from file_cleaner.classifier import classify
from file_cleaner.models import Category as C
from file_cleaner.tools import parse_tags


@pytest.mark.parametrize("family0, family1, name, expected", [
    ("EXIF", "GPS", "GPSLatitude", C.LOCATION),
    ("QuickTime", "Keys", "GPSCoordinates", C.LOCATION),
    ("QuickTime", "Keys", "LocationName", C.LOCATION),
    ("XMP", "XMP-photoshop", "City", C.LOCATION),
    ("IPTC", "IPTC", "Country-PrimaryLocationName", C.LOCATION),
    ("EXIF", "IFD0", "Make", C.DEVICE),
    ("EXIF", "ExifIFD", "LensModel", C.DEVICE),
    ("EXIF", "ExifIFD", "SerialNumber", C.DEVICE),
    ("MakerNotes", "Apple", "RunTimeValue", C.DEVICE),
    ("EXIF", "IFD0", "Artist", C.AUTHOR),
    ("PDF", "PDF", "Author", C.AUTHOR),
    ("ID3", "ID3v2_3", "Title", C.AUTHOR),
    ("EXIF", "ExifIFD", "DateTimeOriginal", C.DATES),
    ("EXIF", "IFD0", "Software", C.SOFTWARE),
    ("EXIF", "ExifIFD", "ExposureTime", C.OTHER),
    ("EXIF", "IFD0", "Orientation", C.KEPT),
    ("ICC_Profile", "ICC-header", "ProfileDateTime", C.KEPT),
    ("EXIF", "IFD0", "ImageWidth", C.TECHNICAL),
    ("File", "System", "FileName", C.TECHNICAL),
    ("PNG", "PNG", "BitDepth", C.TECHNICAL),
    ("PNG", "PNG", "Prompt", C.OTHER),
    ("PNG", "PNG-pHYs", "PixelsPerUnitX", C.TECHNICAL),
    ("Vorbis", "Vorbis", "Organization", C.OTHER),
    ("Vorbis", "Vorbis", "Vendor", C.TECHNICAL),
    ("Matroska", "Matroska", "Engineer", C.OTHER),
    ("Matroska", "Matroska", "DocType", C.TECHNICAL),
    ("Matroska", "Info", "TimecodeScale", C.TECHNICAL),
])
def test_classifies(family0, family1, name, expected):
    assert classify(family0, family1, name) is expected


def test_parses_exiftool_json():
    # Real -G0:1 output: when both groups have the same name, ExifTool writes it once ("IPTC:Keywords").
    tags = parse_tags("""[{"SourceFile":"a.jpg","File:System:FileName":"a.jpg","EXIF:GPS:GPSLatitude":"37 deg",
        "EXIF:IFD0:Make":"Apple","QuickTime:Track1:CreateDate":"0000:00:00 00:00:00",
        "IPTC:Keywords":["one","two"],"EXIF:IFD0:Make":"Second","PDF:Author":"Jane",
        "Vorbis:Artist":"Jane","File:FileType":"JPEG"}]""")

    assert any(t.category is C.LOCATION and t.value == "37 deg" for t in tags)
    assert {t.value for t in tags if t.name == "Make"} == {"Apple", "Second"}
    assert any(t.name == "Keywords" and t.value == "one, two" and t.must_be_removed for t in tags)
    assert any(t.name == "Author" and t.category is C.AUTHOR for t in tags)
    assert any(t.name == "Artist" and t.must_be_removed for t in tags)
    assert not any(t.name == "CreateDate" for t in tags)
    assert not any(t.name == "FileType" and t.must_be_removed for t in tags)
