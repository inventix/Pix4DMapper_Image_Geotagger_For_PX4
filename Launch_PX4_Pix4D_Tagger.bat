@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul && py -3.13 -c "import sys; assert sys.version_info ^>= (3,11)" >nul 2>nul && set "PYTHON_CMD=py -3.13"
if not defined PYTHON_CMD where py >nul 2>nul && py -3.12 -c "import sys; assert sys.version_info ^>= (3,11)" >nul 2>nul && set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD where py >nul 2>nul && py -3.11 -c "import sys; assert sys.version_info ^>= (3,11)" >nul 2>nul && set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD (
    where python >nul 2>nul && python -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo Python 3.11 or newer is not installed on this computer.
    echo Ask the lab instructor to install it,
    echo or use the standalone PX4_Pix4D_Tagger.exe.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
    if errorlevel 1 ren ".venv" ".venv-incompatible-%RANDOM%"
)

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: creating the local program environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :setup_failed
)

if not exist ".venv\.setup_complete" (
    echo Installing the image-tagging components...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :setup_failed
    type nul > ".venv\.setup_complete"
)

start "" ".venv\Scripts\pythonw.exe" px4_pix4d_gui.py
exit /b 0

:setup_failed
echo.
echo Setup did not finish. Check the internet connection or contact the instructor.
pause
exit /b 1
