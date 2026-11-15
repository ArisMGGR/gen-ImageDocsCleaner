"""Sorts ExifTool tags (Family0:Family1:Name) into the categories the report shows."""

from __future__ import annotations

import re

from .models import Category

# Groups that only describe the file, or ExifTool's own view of it.
_IGNORED_GROUPS = {"exiftool", "file", "system", "composite", "track", "sidecar"}

# Groups holding descriptive metadata: any tag we don't recognize there still counts as removable.
_DESCRIPTIVE_GROUPS = {
    "exif", "iptc", "xmp", "makernotes", "photoshop", "flashpix", "id3", "pdf", "mpf", "jumbf", "c2pa",
    "ducky", "pictureinfo", "meta", "canonvrd", "printim",
    *(f"app{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15)),
}

# Required by the format, or needed to decode pixels.
_STRUCTURAL_TAGS = {t.lower() for t in """
    ExifByteOrder ImageWidth ImageHeight ExifImageWidth ExifImageHeight BitsPerSample Compression
    PhotometricInterpretation SamplesPerPixel PlanarConfiguration RowsPerStrip StripOffsets StripByteCounts
    TileWidth TileLength TileOffsets TileByteCounts XResolution YResolution ResolutionUnit FillOrder
    SampleFormat ExtraSamples Predictor ColorMap SubfileType OldSubfileType YCbCrPositioning YCbCrSubSampling
    YCbCrCoefficients ReferenceBlackWhite ColorSpace ComponentsConfiguration Thresholding
    PDFVersion Linearized PageCount Encryption TaggedPDF Language PageMode PageLayout
    MPEGAudioVersion AudioLayer AudioBitrate SampleRate ChannelMode MSStereo IntensityStereo
    HandlerDescription PageNumber WhitePoint PrimaryChromaticities TransferFunction
    CopyrightFlag OriginalMedia Emphasis VBRFrames VBRBytes NumberOfChannels Duration
""".split()}

_KEPT_TAGS = {"orientation", "rotation", "matrixstructure"}

# Groups where free-form text shares a group with format fields: PNG text chunks sit beside the image
# header, and Vorbis comments can use any name. Everything except the listed format fields is text.
_MIXED_GROUPS = {
    "png": {t.lower() for t in """
        ImageWidth ImageHeight BitDepth ColorType Compression Filter Interlace Palette Transparency Gamma
        WhitePointX WhitePointY RedX RedY GreenX GreenY BlueX BlueY SRGBRendering BackgroundColor
        SignificantBits AnimationFrames AnimationPlays ProfileName ColorPrimaries TransferCharacteristics
        MatrixCoefficients VideoFullRangeFlag
    """.split()},
    # The stream header, and the encoder library's name that every comment block must carry.
    "vorbis": {t.lower() for t in """
        Vendor VorbisVersion AudioChannels SampleRate NominalBitrate MaxBitrate MinBitrate
    """.split()},
    # MKV and WebM: the EBML header shares the group with tags, which can have any name.
    "matroska": {t.lower() for t in """
        EBMLVersion EBMLReadVersion EBMLMaxIDLength EBMLMaxSizeLength DocType DocTypeVersion DocTypeReadVersion
    """.split()},
}

_LOCATION = re.compile(
    r"GPS|Location|Geo(?!metry)|Latitude|Longitude|Altitude|ISO6709|Coordinates|Sublocation"
    r"|^(City|Country|CountryCode|State|Province|Province-State)$|^Country-", re.I)
_DEVICE = re.compile(
    r"^(Make|Model|CameraModelName|UniqueCameraModel|LocalizedCameraModel|HostComputer|AndroidModel"
    r"|AndroidManufacturer)$|Lens|Serial|CameraOwner|OwnerName|Firmware|MakerNote|DeviceManufacturer"
    r"|DeviceModel|ContentIdentifier|CameraID|ImageUniqueID", re.I)
_AUTHOR = re.compile(
    r"Author|Artist|Creator|Copyright|Rights|Owner|Publisher|Producer|Contributor|Company|Manager|Credit"
    r"|Byline|Writer|Title|Subject|Description|Comment|Keywords|Caption|Album|Composer|Performer|Lyrics"
    r"|Rating|Category", re.I)
_SOFTWARE = re.compile(r"Software|CreatorTool|Encoder|EncodedBy|Application|History|DocumentID|InstanceID", re.I)
_DATE = re.compile(r"Date|TimeStamp|TimeCreated|DigitalCreationTime|^Year$|OffsetTime|SubSecTime", re.I)


def classify(family0: str, family1: str, name: str) -> Category:
    group = family0.lower()
    lowered = name.lower()

    if group in _IGNORED_GROUPS:
        return Category.TECHNICAL
    if group.startswith("icc") or family1.lower().startswith("icc") or lowered in _KEPT_TAGS:
        return Category.KEPT
    if _LOCATION.search(name):
        return Category.LOCATION
    if lowered in _STRUCTURAL_TAGS:
        return Category.TECHNICAL
    if group == "makernotes" or _DEVICE.search(name):
        return Category.DEVICE
    if _AUTHOR.search(name):
        return Category.AUTHOR
    if _SOFTWARE.search(name):
        return Category.SOFTWARE
    if _DATE.search(name):
        return Category.DATES
    if group in _MIXED_GROUPS and family1.lower() == group:
        return Category.TECHNICAL if lowered in _MIXED_GROUPS[group] else Category.OTHER
    return Category.OTHER if group in _DESCRIPTIVE_GROUPS or group.startswith("xmp") else Category.TECHNICAL


def is_empty_value(value: str) -> bool:
    """Video containers often hold zeroed-out required dates, which reveal nothing."""
    text = value.strip()
    return text in ("", "0") or text.startswith("0000:00:00")
