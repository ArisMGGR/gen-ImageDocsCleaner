# Downloads the ExifTool and FFmpeg programs File Cleaner uses into third_party/ (not committed).
# Called by Install.bat.
# Every download is pinned to an exact version and verified against a checksum before use.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root 'third_party\tools'
New-Item -ItemType Directory -Force $tools | Out-Null

$work = Join-Path ([IO.Path]::GetTempPath()) ("filecleaner-fetch-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null

function Get-Verified($url, $sha256, $name) {
    $file = Join-Path $work $name
    Write-Host "Downloading $name"
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing -UserAgent 'FileCleaner-build'
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $sha256) { throw "Checksum mismatch for $name`nexpected $sha256`nactual   $actual" }
    return $file
}

try {
    if (-not (Test-Path (Join-Path $tools 'exiftool\exiftool.exe'))) {
        # Hash published at https://exiftool.org/checksums.txt
        $version = '13.60'
        $zip = Get-Verified "https://sourceforge.net/projects/exiftool/files/exiftool-${version}_64.zip/download" `
            '548b1f4e0b713cd2a26824b34080c35db4e2d2edd166468eb38b1d55ada90c2e' 'exiftool.zip'
        $out = Join-Path $work 'exiftool'
        Expand-Archive $zip $out
        $dir = Get-ChildItem $out -Directory | Select-Object -First 1
        Rename-Item (Join-Path $dir.FullName 'exiftool(-k).exe') 'exiftool.exe'
        # A leftover folder (say, the exe was quarantined) would make Move-Item nest the new copy inside it.
        $dest = Join-Path $tools 'exiftool'
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Move-Item $dir.FullName $dest
    }

    if (-not (Test-Path (Join-Path $tools 'ffmpeg\ffmpeg.exe'))) {
        # LGPL static build, hash from the GitHub release asset.
        # Daily builds are deleted after 14 days, but the last build of each month is kept for two years,
        # so always pin a month-end build (this one until about October 2028). Update tag, name and hash together.
        $tag = 'autobuild-2026-10-31-12-59'
        $name = 'ffmpeg-n8.1.3-27-g8dca1586f4-win64-lgpl-8.1'
        $zip = Get-Verified "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/$tag/$name.zip" `
            'd4c2a42673917b4a97104a3e9f082729a9730796b98664e37c0b7eba6c6c613d' 'ffmpeg.zip'
        $out = Join-Path $work 'ffmpeg'
        Expand-Archive $zip $out
        $dir = Get-ChildItem $out -Directory | Select-Object -First 1
        $dest = Join-Path $tools 'ffmpeg'
        New-Item -ItemType Directory -Force $dest | Out-Null
        Copy-Item (Join-Path $dir.FullName 'bin\ffmpeg.exe') $dest
        Copy-Item (Join-Path $dir.FullName 'LICENSE.txt') $dest
    }
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host 'Tools ready.'
