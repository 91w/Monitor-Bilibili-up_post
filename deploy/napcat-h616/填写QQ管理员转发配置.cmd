@echo off
setlocal
cd /d "%~dp0"
if exist "D:\python\python.exe" (
  "D:\python\python.exe" -B -u "%~dp0tools\windows\qq-forward-settings.py"
) else (
  python -B -u "%~dp0tools\windows\qq-forward-settings.py"
)
if errorlevel 1 pause
