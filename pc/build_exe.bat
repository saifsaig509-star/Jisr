@echo off
setlocal
cd /d "%~dp0"
:: build_exe.bat      -> dist\Jisr.exe  (one portable file; double-click it)
:: build_exe.bat ci   -> dist\Jisr\     (a folder; this is what the Setup.exe installer packs; no prompts)
set JISR_CI=0
if /i "%~1"=="ci" set JISR_CI=1

echo ============================================
echo  Building Jisr.exe  (needs Python + internet, takes a few minutes)
echo ============================================

python --version >nul 2>&1 || (echo Python was not found. Install it from python.org and tick "Add to PATH". & goto :fail)

set WHEELS=
set MODE=--onefile
if "%JISR_CI%"=="1" set WHEELS=--only-binary=:all:
if "%JISR_CI%"=="1" set MODE=--onedir

echo.
echo [1/3] Installing build tools and optional features...
python -m pip install --upgrade pyinstaller || goto :fail
python -m pip install cryptography   || goto :fail
python -m pip install %WHEELS% PyAudioWPatch  || echo   - PyAudioWPatch skipped (no PC-audio streaming in the exe)
python -m pip install %WHEELS% vgamepad       || echo   - vgamepad skipped (no virtual Xbox pad in the exe)
python -m pip install pystray pillow || echo   - pystray/pillow skipped (no tray icon in the exe)

set EXTRA=
python -c "import vgamepad" >nul 2>&1 && set EXTRA=%EXTRA% --collect-all vgamepad
python -c "import pyaudiowpatch" >nul 2>&1 && set EXTRA=%EXTRA% --collect-all pyaudiowpatch
python -c "import pystray, PIL" >nul 2>&1 && set EXTRA=%EXTRA% --hidden-import pystray._win32

python installer\make_versioninfo.py VERSION installer\version_info.txt || goto :fail

echo.
echo [2/3] Packaging...
python -m PyInstaller --noconfirm --clean %MODE% --windowed --name Jisr ^
    --icon assets\jisr.ico --version-file installer\version_info.txt ^
    --add-data "assets\jisr.ico;assets" %EXTRA% btbridge_gui.pyw || goto :fail

echo.
if "%JISR_CI%"=="1" goto :done_ci
echo [3/3] Done:  %~dp0dist\Jisr.exe
echo.
echo Double-click it. To start it with Windows, tick "Start with Windows" inside the program.
echo (Windows SmartScreen may warn about an unknown publisher: More info -^> Run anyway.)
explorer "%~dp0dist"
pause
exit /b 0

:done_ci
echo [3/3] Done:  %~dp0dist\Jisr\Jisr.exe
exit /b 0

:fail
echo.
echo Build failed - read the messages above.
if not "%JISR_CI%"=="1" pause
exit /b 1
