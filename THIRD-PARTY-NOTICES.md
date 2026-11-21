# Third-party notices

File Cleaner is MIT licensed. It uses the following separate components under their own licenses. None of them are
stored in this repository: `Install.bat` downloads them, and the standalone build includes them.

| Component | Use | License | Source |
| --- | --- | --- | --- |
| ExifTool 13.60 by Phil Harvey | Reads and removes metadata | Perl Artistic License or GPL | https://exiftool.org |
| FFmpeg 8.1 (LGPL build by BtbN) | Copies media without metadata | LGPL 2.1 or later | https://ffmpeg.org, build: https://github.com/FFmpeg-Builds/FFmpeg-Builds |
| PySide6 (Qt) | App window | LGPL 3.0 | https://doc.qt.io/qtforpython |
| pikepdf | Rewrites PDF files | MPL 2.0 | https://github.com/pikepdf/pikepdf |
| qpdf (in pikepdf) | PDF library pikepdf is built on | Apache License 2.0 | https://github.com/qpdf/qpdf |
| lxml (used by pikepdf) | XML library pikepdf needs | BSD 3-Clause. Its binary wheel includes libxml2, libxslt and libexslt (MIT), zlib (zlib License) and iconv (LGPL 2.1) | https://github.com/lxml/lxml |
| Pillow | Re-saves images as a fallback | MIT-CMU | https://github.com/python-pillow/Pillow |
| pillow-heif | HEIC support for Pillow | Source: BSD 3-Clause. Binary wheel: GPL 2.0, because it includes x265 | https://github.com/bigcat88/pillow_heif |
| libheif, libde265 (in pillow-heif) | Decode HEIC images | LGPL 3.0 | https://github.com/strukturag/libheif, https://github.com/strukturag/libde265 |
| x265 (in pillow-heif) | HEVC encoder library | GPL 2.0 | https://github.com/Multicorewareinc/x265 |
| MinGW-w64 runtime (in pillow-heif) | C/C++ runtime for the libraries above | GPL 3.0 with GCC exception, MIT, BSD | https://www.mingw-w64.org |
| Python | Runs the app (standalone build only) | PSF License | https://www.python.org |
| PyInstaller bootloader | Starts the standalone build | GPL 2.0 with an exception that allows bundling | https://pyinstaller.org |

ExifTool and FFmpeg run as separate programs from `third_party\tools` (`_internal\third_party\tools` in the
standalone build). The Qt, libheif, libde265 and x265 libraries stay separate, replaceable files in the standalone build. FFmpeg's full license text is in
`third_party\tools\ffmpeg\LICENSE.txt` after installation.

Because the pillow-heif binary includes x265, the standalone build as a whole is distributed under the terms of the
GPL 2.0 for those parts. The source code of x265, libheif and libde265 is available at the links above, and
pillow-heif's list of bundled libraries is in its `LICENSES_bundled.txt`.
