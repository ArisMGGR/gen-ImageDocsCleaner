# Builds the standalone version that runs without Python: dist\File Cleaner\File Cleaner.exe, plus
# dist\FileCleaner-<version>-win64.zip to share. Called by Build.bat; run Install.bat once first.
# A folder build, not a single exe: a single exe would unpack ~200 MB to a temp folder on every start,
# and keeping the Qt libraries as separate files is what the LGPL asks of a bundled copy.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'The venv folder is missing. Run Install.bat first.' }

function Invoke-Checked([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

& $python -m pip install --upgrade --quiet -r (Join-Path $root 'requirements-build.txt'); Invoke-Checked 'Installing PyInstaller'
& $python (Join-Path $root 'run.py') --selftest; Invoke-Checked 'Checking for ExifTool and FFmpeg (run Install.bat)'

$version = (& $python -c "import sys; sys.path.insert(0, r'$root'); import file_cleaner; print(file_cleaner.__version__)").Trim()
$work = Join-Path $root 'build'
$dist = Join-Path $root 'dist'
$app = Join-Path $dist 'File Cleaner'
New-Item -ItemType Directory -Force $work | Out-Null

# Windows shows these in the exe's Properties > Details.
$numbers = (($version.Split('.') + @('0', '0', '0', '0'))[0..3]) -join ', '
$versionFile = Join-Path $work 'version.txt'
$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($numbers), prodvers=($numbers)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'File Cleaner'),
      StringStruct('ProductName', 'File Cleaner'),
      StringStruct('FileVersion', '$version'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('OriginalFilename', 'File Cleaner.exe'),
      StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
[IO.File]::WriteAllText($versionFile, $versionInfo, (New-Object Text.UTF8Encoding $false))  # no BOM

Write-Host "Building File Cleaner $version ..."
& $python -m PyInstaller (Join-Path $root 'run.py') `
    --noconfirm --clean --windowed --log-level WARN `
    --name 'File Cleaner' `
    --icon (Join-Path $root 'assets\FileCleaner.ico') `
    --version-file $versionFile `
    --distpath $dist --workpath (Join-Path $work 'pyinstaller') --specpath $work `
    --add-data "$(Join-Path $root 'assets\FileCleaner.ico');assets" `
    --add-data "$(Join-Path $root 'third_party\tools');third_party\tools" `
    --exclude-module tkinter --exclude-module pytest
Invoke-Checked 'PyInstaller'

foreach ($file in 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md') {
    Copy-Item (Join-Path $root $file) $app
}

# The built app must find its bundled tools on its own.
$exe = Join-Path $app 'File Cleaner.exe'
try {
    $check = Start-Process $exe -ArgumentList '--selftest' -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw 'The built app could not find ExifTool or FFmpeg.' }
} catch [InvalidOperationException] {
    # Smart App Control and similar policies block new unsigned programs, including this one.
    Write-Warning "Windows would not run the new exe, so its self-test was skipped: $($_.Exception.Message)"
}

$zip = Join-Path $dist "FileCleaner-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Write-Host 'Creating the zip ...'
Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal

Write-Host ''
Write-Host "Built: $exe"
Write-Host "Zip:   $zip"
