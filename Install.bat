@echo off
REM Double-click to set up File Cleaner: creates the venv folder, installs the requirements,
REM downloads ExifTool and FFmpeg if missing, and creates a shortcut. Safe to run again to update the packages.
setlocal
cd /d "%~dp0"
title File Cleaner - Install

REM Find Python 3.11-3.14 (prefer the "py" launcher that comes with python.org installers).
set "PY="
for %%V in (3.14 3.13 3.12 3.11) do (
    if not defined PY (py -%%V -c "pass" >nul 2>&1 && set "PY=py -%%V")
)
if not defined PY (
    python -c "import sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 14))" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python 3.11 to 3.14 was not found ^(newer versions are not supported yet^).
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
    echo then run this file again.
    goto :fail
)

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment in venv ...
    %PY% -m venv venv || goto :fail
)

echo Updating pip ...
"venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail

echo Installing requirements (this can take a few minutes the first time) ...
"venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt || goto :fail

echo Downloading ExifTool and FFmpeg (about 170 MB the first time) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fetch-tools.ps1" || goto :fail

echo Creating the "File Cleaner" shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\make_shortcut.ps1" || goto :fail

"venv\Scripts\python.exe" run.py --selftest || goto :fail

echo.
echo Done! Double-click the "File Cleaner" shortcut in this folder to open the program.
echo (Tip: copy or drag it to your Desktop or Start menu.)
if not defined CI pause
exit /b 0

:fail
echo.
echo Installation failed. See the messages above.
if not defined CI pause
exit /b 1
