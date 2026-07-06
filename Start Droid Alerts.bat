@echo off
rem Double-click this file to start Droid Alerts.
cd /d "%~dp0"

set PY=python
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Python is not installed yet.
        echo Get it from https://www.python.org/downloads/ and make sure to
        echo tick "Add python.exe to PATH" in the installer, then run this again.
        echo.
        pause
        exit /b 1
    )
    set PY=py
)

%PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
%PY% main.py gui
if errorlevel 1 pause
