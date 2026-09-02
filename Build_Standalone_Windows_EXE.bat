@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.11 or newer before building the standalone application.
    pause
    exit /b 1
)

if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv
if errorlevel 1 goto :failed

".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

".build-venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name "PX4_Pix4D_Tagger" ^
  --collect-all pyulog ^
  px4_pix4d_gui.py
if errorlevel 1 goto :failed

copy /y course_config.json "dist\course_config.json" >nul
echo.
echo Build complete:
echo %CD%\dist\PX4_Pix4D_Tagger.exe
echo.
echo Distribute the EXE and course_config.json together.
pause
exit /b 0

:failed
echo.
echo The Windows application build failed. Review the messages above.
pause
exit /b 1
