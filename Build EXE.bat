@echo off
setlocal
cd /d "%~dp0"

set PY=python
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python is required to build the release exe.
        echo Install it from https://www.python.org/downloads/ and tick Add python.exe to PATH.
        pause
        exit /b 1
    )
    set PY=py
)

echo Installing build requirements...
%PY% -m pip install -r requirements-build.txt --disable-pip-version-check
if errorlevel 1 (
    pause
    exit /b 1
)

echo Building Droid Alerts.exe...
%PY% -m PyInstaller --noconfirm --clean "Droid Alerts.spec"
if errorlevel 1 (
    pause
    exit /b 1
)

echo Verifying packaged QML runtime...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$roots = @('dist\Droid Alerts\_internal\PySide6\qml', 'dist\Droid Alerts\_internal\PySide6\Qt\qml'); $root = $roots | Where-Object { Test-Path $_ } | Select-Object -First 1; if (-not $root) { Write-Error 'Qt QML runtime folder is missing from the release.'; exit 1 }; $required = @('QtQml\qmldir', 'QtQuick\qmldir', 'QtQuick\Controls\qmldir', 'QtQuick\Layouts\qmldir', 'QtQuick\Shapes\qmldir'); foreach ($relative in $required) { if (-not (Test-Path (Join-Path $root $relative))) { Write-Error ('Required QML module is missing: ' + $relative); exit 1 } }"
if errorlevel 1 (
    pause
    exit /b 1
)

echo Creating release zip...
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path 'dist\DroidAlerts.zip') { Remove-Item 'dist\DroidAlerts.zip' -Force }; Compress-Archive -Path 'dist\Droid Alerts' -DestinationPath 'dist\DroidAlerts.zip' -Force"
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo Done.
echo Folder: dist\Droid Alerts
echo Zip:    dist\DroidAlerts.zip
pause
