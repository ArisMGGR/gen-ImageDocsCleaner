# Regenerates FileCleaner.ico: a white page on black with a red slash through a location pin.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function New-Frame([int]$size) {
    $bmp = New-Object Drawing.Bitmap $size, $size
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.ScaleTransform($size / 256.0, $size / 256.0)

    $g.FillRectangle((New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(255, 12, 12, 14))), 0, 0, 256, 256)

    # Location pin
    $pin = New-Object Drawing.Drawing2D.GraphicsPath
    $pin.AddArc(58, 28, 140, 140, 150, 240)
    $pin.AddLine(189, 133, 128, 232)
    $pin.AddLine(128, 232, 67, 133)
    $pin.CloseFigure()
    $g.FillPath([Drawing.Brushes]::White, $pin)
    $g.FillEllipse((New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(255, 12, 12, 14))), 98, 68, 60, 60)

    # Red slash
    $slash = New-Object Drawing.Pen ([Drawing.Color]::FromArgb(255, 229, 20, 43)), 26
    $slash.StartCap = 'Round'; $slash.EndCap = 'Round'
    $g.DrawLine($slash, 44, 44, 212, 212)
    $g.Dispose()

    $ms = New-Object IO.MemoryStream
    $bmp.Save($ms, [Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return , $ms.ToArray()
}

$sizes = 16, 24, 32, 48, 64, 128, 256
$frames = $sizes | ForEach-Object { , (New-Frame $_) }

$out = New-Object IO.MemoryStream
$w = New-Object IO.BinaryWriter $out
$w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]; $len = $frames[$i].Length
    $dim = if ($s -eq 256) { 0 } else { $s }
    $w.Write([byte]$dim); $w.Write([byte]$dim); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([uint16]1); $w.Write([uint16]32); $w.Write([uint32]$len); $w.Write([uint32]$offset)
    $offset += $len
}
foreach ($f in $frames) { $w.Write($f) }
[IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'FileCleaner.ico'), $out.ToArray())
