@echo off
REM Builds a standalone UnrealCustodian.exe -- Python + Tk bundled inside, no
REM requirement that the end user has Python installed. Must run ON Windows;
REM PyInstaller cannot cross-compile from macOS/Linux.
REM
REM Usage (from the repo root, or from this directory):
REM     py -3 -m pip install --upgrade pyinstaller
REM     packaging\windows\build.bat

setlocal
cd /d "%~dp0..\.."

py -3 -m PyInstaller --noconfirm --windowed --onefile ^
  --name "UnrealCustodian" ^
  --icon "packaging\windows\icon.ico" ^
  --paths . ^
  --hidden-import tkinter ^
  --distpath "packaging\windows\dist" ^
  --workpath "packaging\windows\build" ^
  --specpath "packaging\windows" ^
  "packaging\windows\launch_gui.py"

echo.
echo Built: packaging\windows\dist\UnrealCustodian.exe
