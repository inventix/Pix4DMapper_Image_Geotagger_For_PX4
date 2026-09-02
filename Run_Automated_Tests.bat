@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required to run the automated tests.
    pause
    exit /b 1
)

if not exist ".test-venv\Scripts\python.exe" py -3 -m venv .test-venv
if errorlevel 1 goto :failed

".test-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 goto :failed

".test-venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :failed

echo.
echo All automated tests passed.
pause
exit /b 0

:failed
echo.
echo Verification failed. Do not distribute or publish this build.
pause
exit /b 1
