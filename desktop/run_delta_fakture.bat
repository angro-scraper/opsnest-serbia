@echo off
setlocal
cd /d "%~dp0"
if exist "dist\OpsNest\OpsNest.exe" (
  start "" "dist\OpsNest\OpsNest.exe"
  exit /b
)
if exist "%SystemRoot%\py.exe" (
  py -3 delta_fakture_app.py
) else (
  python delta_fakture_app.py
)
if errorlevel 1 pause
