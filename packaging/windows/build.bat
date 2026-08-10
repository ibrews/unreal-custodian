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

REM --icon and --add-data SRC paths both resolve relative to --specpath
REM (packaging\windows), NOT the cwd -- despite --paths below being cwd-
REM relative. Bitten by this twice now: --icon doubled to a nonexistent
REM path early on, and --add-data's icon.png repeated the same mistake
REM later (assumed cwd-relative like --paths, found wrong on real hardware).
py -3 -m PyInstaller --noconfirm --windowed --onefile ^
  --name "UnrealCustodian" ^
  --icon "icon.ico" ^
  --add-data "..\..\custodian\icon.png;custodian" ^
  --paths . ^
  --hidden-import tkinter ^
  --distpath "packaging\windows\dist" ^
  --workpath "packaging\windows\build" ^
  --specpath "packaging\windows" ^
  "packaging\windows\launch_gui.py"

if errorlevel 1 (
  echo.
  echo Build FAILED - see PyInstaller output above.
  exit /b 1
)

echo.
echo Built: packaging\windows\dist\UnrealCustodian.exe
