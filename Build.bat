@echo off
REM Double-click to build the standalone version, which runs on PCs without Python.
REM Run Install.bat once first. The result is in the dist folder.
setlocal
cd /d "%~dp0"
title File Cleaner - Build

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-exe.ps1" || goto :fail

echo.
echo Done! Share the zip in the dist folder. Unzip it and run "File Cleaner.exe".
if not defined CI pause
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
if not defined CI pause
exit /b 1
