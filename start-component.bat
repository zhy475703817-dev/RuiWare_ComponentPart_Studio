@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1"
if errorlevel 1 (
  echo Component platform startup failed. See the message above and .runtime logs.
  pause
  exit /b 1
)
endlocal
