"""End-to-end checks on real files of every format the app advertises."""

import io
import os
import shutil

import pikepdf
import pytest
from conftest import exif, ffmpeg
from PIL import Image, ImageChops

from file_cleaner import engine
from file_cleaner.models import CleaningError

EXIF_TAGS = [
    "-GPSLatitude=37.7749", "-GPSLatitudeRef=N", "-GPSLongitude=122.4194", "-GPSLongitudeRef=W",
    "-Make=SecretCam", "-Model=X100", "-Artist=Jane Secret", "-Software=PhotoApp",
    "-DateTimeOriginal=2020:01:02 03:04:05",
]
EXTRA_JPEG_TAGS = ["-XMP:City=Secretville", "-IPTC:Keywords=private"]
XMP_TAGS = ["-XMP:GPSLatitude=37.7749", "-XMP:GPSLongitude=-122.4194", "-XMP:Creator=Jane Secret"]


def make_image(path, tags):
    Image.effect_noise((64, 48), 80).convert("RGB").save(path)
    exif("-q", "-overwrite_original", *tags, path)
    return str(path)


def pixels_equal(a, b):
    with Image.open(a) as x, Image.open(b) as y:
        return ImageChops.difference(x.convert("RGB"), y.convert("RGB")).getbbox() is None


def data(path):
    return open(path, "rb").read()


@pytest.mark.parametrize("name, tags", [
    ("a.jpg", EXIF_TAGS + EXTRA_JPEG_TAGS),
    ("a.png", EXIF_TAGS),
    ("a.webp", EXIF_TAGS),
    ("a.tif", EXIF_TAGS),
    ("a.heic", EXIF_TAGS),
    ("a.gif", XMP_TAGS),
])
def test_images_clean_losslessly(tmp_path, out, name, tags):
    source = make_image(tmp_path / name, tags)

    before = engine.scan(source)
    result = engine.clean(source, out)

    assert before.has_location
    assert result.after.removable == []
    assert result.notes == []
    assert pixels_equal(source, result.output_path)
    assert b"Secret" not in data(result.output_path)


def test_keeps_rotation_and_color_profile(tmp_path, out):
    source = str(tmp_path / "a.jpg")
    from PIL import ImageCms
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (64, 48), "red").save(source, icc_profile=srgb)
    exif("-q", "-overwrite_original", "-n", "-Orientation=6", *EXIF_TAGS, source)

    result = engine.clean(source, out)

    assert exif("-s3", "-n", "-Orientation", result.output_path).strip() == "6"
    assert exif("-s3", "-ProfileDescription", result.output_path).strip() != ""


def test_mp4_gps_removed(tmp_path, out):
    source = str(tmp_path / "a.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc=d=1:s=64x48", "-f", "lavfi", "-i", "sine=d=1", "-shortest",
           "-c:v", "mpeg4", "-c:a", "aac", "-metadata", "title=Secret", source)
    exif("-q", "-overwrite_original", "-Keys:GPSCoordinates=37.77 N, 122.41 W", "-QuickTime:Make=SecretCam", source)

    before = engine.scan(source)
    result = engine.clean(source, out)

    assert before.has_location
    assert result.after.removable == []
    assert b"Secret" not in data(result.output_path)
    assert exif("-s3", "-MIMEType", result.output_path).strip() == "video/mp4"


@pytest.mark.parametrize("name, video, audio", [
    ("a.mov", "mpeg4", "aac"), ("a.m4v", "mpeg4", "aac"), ("a.mkv", "mpeg4", "aac"),
    ("a.avi", "mpeg4", "mp3"), ("a.webm", "libvpx-vp9", "libopus"),
])
def test_video_formats(tmp_path, out, name, video, audio):
    source = str(tmp_path / name)
    ffmpeg("-f", "lavfi", "-i", "testsrc=d=1:s=64x48", "-f", "lavfi", "-i", "sine=d=1", "-shortest",
           "-c:v", video, "-c:a", audio, "-metadata", "title=Secret title", "-metadata", "artist=Jane Secret", source)

    result = engine.clean(source, out)

    assert result.after.removable == []
    assert b"Secret" not in data(result.output_path)


def test_video_keeps_rotation(tmp_path, out):
    plain, source = str(tmp_path / "plain.mp4"), str(tmp_path / "rotated.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc=d=1:s=64x48", "-c:v", "mpeg4", plain)
    ffmpeg("-display_rotation", "90", "-i", plain, "-c", "copy", source)
    expected = exif("-s3", "-Rotation", source).strip()
    assert expected not in ("", "0")

    result = engine.clean(source, out)

    assert exif("-s3", "-Rotation", result.output_path).strip() == expected


@pytest.mark.parametrize("name, codec", [
    ("a.mp3", "libmp3lame"), ("a.flac", "flac"), ("a.wav", "pcm_s16le"),
    ("a.m4a", "aac"), ("a.opus", "libopus"), ("a.ogg", "libvorbis"),
])
def test_audio_formats(tmp_path, out, name, codec):
    source = str(tmp_path / name)
    ffmpeg("-f", "lavfi", "-i", "sine=d=1", "-c:a", codec, "-metadata", "artist=Jane Secret", source)

    result = engine.clean(source, out)

    assert result.after.removable == []
    assert b"Jane Secret" not in data(result.output_path)


def make_pdf(path, jpeg):
    with Image.open(jpeg) as image:
        width, height = image.size
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    stream = pikepdf.Stream(pdf, data(jpeg))
    stream["/Type"] = pikepdf.Name.XObject
    stream["/Subtype"] = pikepdf.Name.Image
    stream["/Width"] = width
    stream["/Height"] = height
    stream["/ColorSpace"] = pikepdf.Name.DeviceRGB
    stream["/BitsPerComponent"] = 8
    stream["/Filter"] = pikepdf.Name.DCTDecode
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(stream)))
    pdf.pages[0].Contents = pdf.make_stream(b"q 100 0 0 100 0 0 cm /Im0 Do Q")
    pdf.docinfo["/Author"] = "Jane Secret"
    pdf.save(path)


def test_pdf_drops_properties_old_revisions_and_embedded_image_gps(tmp_path, out):
    jpeg = make_image(tmp_path / "photo.jpg", EXIF_TAGS)
    source = str(tmp_path / "a.pdf")
    make_pdf(source, jpeg)
    # ExifTool edits PDFs incrementally, so the old author stays in the bytes: the trap to catch.
    exif("-q", "-overwrite_original", "-Author=John Secret", "-Title=Secret title", source)
    assert b"Jane Secret" in data(source)

    before = engine.scan(source)
    result = engine.clean(source, out)

    assert before.has_location
    assert result.after.removable == []
    assert b"Secret" not in data(result.output_path)
    with pikepdf.open(result.output_path) as pdf:
        assert len(pdf.pages) == 1
        assert "/Info" not in pdf.trailer
        assert "/Metadata" not in pdf.Root
        image = pdf.pages[0].Resources.XObject.Im0
        assert pixels_equal(jpeg, io.BytesIO(image.read_raw_bytes()))


def test_password_protected_pdf_fails_cleanly(tmp_path, out):
    source = str(tmp_path / "locked.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(source, encryption=pikepdf.Encryption(owner="o", user="u"))

    with pytest.raises(CleaningError, match="password"):
        engine.clean(source, out)
    assert not os.path.exists(out)


def test_unicode_file_names(tmp_path, out):
    source = str(tmp_path / "φωτογραφία 日本.jpg")
    os.rename(make_image(tmp_path / "plain.jpg", EXIF_TAGS), source)

    result = engine.clean(source, out)

    assert os.path.basename(result.output_path) == "φωτογραφία 日本.jpg"
    assert result.after.removable == []


def test_falls_back_to_reencoding_when_extension_lies(tmp_path, out):
    source = str(tmp_path / "fake.jpg")
    os.rename(make_image(tmp_path / "real.png", EXIF_TAGS), source)

    result = engine.clean(source, out)

    assert result.after.removable == []
    assert any("Re-saved" in n for n in result.notes)


def test_damaged_image_fails_without_output(tmp_path, out):
    source = tmp_path / "damaged.jpg"
    source.write_bytes(bytes([0xFF, 0xD8, 0xFF, 0x00, 1, 2]))

    with pytest.raises(CleaningError):
        engine.clean(str(source), out)
    assert not os.path.exists(out)


def test_copy_that_fails_verification_is_never_saved(tmp_path, out, monkeypatch):
    from file_cleaner import images
    source = make_image(tmp_path / "a.jpg", EXIF_TAGS)
    # A cleaner that leaves everything in place: verification must refuse the copy.
    monkeypatch.setattr(images, "clean", lambda s, t, cancel=None: (shutil.copyfile(s, t), [])[1])

    with pytest.raises(CleaningError, match="Verification failed"):
        engine.clean(source, out)
    assert not os.path.exists(out) or os.listdir(out) == []


def make_annotated_pdf(path):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    note = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text, Rect=[10, 10, 30, 30],
        T="Carl Commenter", Contents="Private remark"))
    link = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[50, 50, 90, 60],
        A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI="https://example.com")))
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([note, link]))
    pdf.attachments["secret.txt"] = pikepdf.AttachedFileSpec(pdf, b"attached secret data")
    pdf.save(path)
    return str(path)


def test_pdf_comments_and_attachments_kept_by_default(tmp_path, out):
    source = make_annotated_pdf(tmp_path / "a.pdf")

    result = engine.clean(source, out)

    assert engine.scan(source).extras == ["comments", "attached files"]
    assert result.after.extras == ["comments", "attached files"]
    assert b"Carl Commenter" in data(result.output_path)  # so the byte checks below mean something


def test_pdf_comments_and_attachments_removed_on_request(tmp_path, out):
    source = make_annotated_pdf(tmp_path / "a.pdf")

    result = engine.clean(source, out, extras=True)

    assert result.after.extras == []
    assert "Removed comments and attached files." in result.notes
    with pikepdf.open(result.output_path) as pdf:
        annotations = pdf.pages[0].Annots
        assert [str(a.Subtype) for a in annotations] == ["/Link"]  # links are content, so they stay
        assert len(pdf.attachments) == 0
    for secret in (b"Carl Commenter", b"Private remark", b"attached secret data", b"secret.txt"):
        assert secret not in data(result.output_path)
