# File Cleaner

A free, open-source Windows app that removes location data and other metadata from images, documents and
media. It works entirely offline, and your originals are never changed.

## Install

**Without Python:** download the `FileCleaner-<version>-win64.zip` from the
[Releases page](https://github.com/ArisMGGR/gen-ImageDocsCleaner/releases/latest), unzip it anywhere and run
**`File Cleaner.exe`** in the unzipped folder. Keep the `_internal` folder next to it.

**From source:**

1. Install [Python 3.11 to 3.14](https://www.python.org/downloads/) and tick *Add python.exe to PATH*.
2. Double-click **`Install.bat`**. It creates the `venv` folder, installs the requirements, downloads ExifTool and
   FFmpeg (about 170 MB the first time, with checksums verified), and creates a **`File Cleaner`** shortcut.
3. Double-click the **`File Cleaner`** shortcut. You can copy it to your Desktop or pin it to the Start menu.

Run `Install.bat` again at any time to update the Python packages, or if you move the folder. ExifTool and FFmpeg
are downloaded only when missing: delete the `third_party\tools` folder first to download them again.

## Supported files

- **Images:** JPG, PNG, WEBP, TIFF, GIF, HEIC
- **Documents:** PDF, DOCX, XLSX, PPTX
- **Video and audio:** MP4, M4V, MOV, MKV, WEBM, AVI, MP3, M4A, WAV, FLAC, OGG, OPUS

## How it works

1. Drop files or folders into the window.
2. Review what each file contains. Location data is flagged in red.
3. Click **Clean**. Cleaned copies are saved to the folder you chose.
4. Each copy is re-read to confirm the metadata is gone. If anything remains, no copy is saved and the file is
   marked as failed.

## What is removed

- **Images and video:** GPS, camera and device details, dates, author and software tags, thumbnails. Rotation and
  color profile are kept so files look the same.
- **Documents:** author, company, dates, revision details and similar properties, saved file paths, document
  thumbnails, and metadata in embedded images and media.
- **Optionally, comments, tracked changes, speaker notes and PDF attachments.** Tick *Also remove comments, tracked
  changes, speaker notes and PDF attachments*. Tracked changes are accepted, as with Word's *Accept All Changes*, so
  the text reads the way it does now. In PDFs, notes, highlights, drawings and stamps are removed, but links and form
  fields stay. The cleaned copy is checked to confirm these are gone too.
- **PDFs** are fully rewritten, so earlier revisions can't be recovered from the file.
- **Video and audio** are copied without re-encoding. GPS data tracks and cover art are dropped.
- Cleaned copies get fresh file dates and keep their file names.

## What is not removed

- Anything visible in the content, such as a street sign in a photo or an address in a document.
- Comments, tracked changes, speaker notes and PDF attachments, unless you tick the option above. The app tells you
  when a document contains them.
- Hidden text and hidden sheets. The app warns when a document contains these.
- Older `.doc`, `.xls` and `.ppt` files are skipped.

## Development

```bat
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest
```

## Building the standalone version

Run `Install.bat` once, then double-click **`Build.bat`**. It creates `dist\File Cleaner\File Cleaner.exe` and
`dist\FileCleaner-<version>-win64.zip`, with Python, ExifTool and FFmpeg included.

The exe is not code-signed. On PCs with Smart App Control or a similar policy, Windows blocks unsigned programs, and
SmartScreen warns on first launch elsewhere. Signing the exe with a code-signing certificate avoids both.

## License

MIT. See [LICENSE](LICENSE) and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
