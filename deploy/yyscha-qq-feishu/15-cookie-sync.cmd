@echo off
setlocal
set PYTHONUTF8=1
python "%~dp0recovery_web.py" --open-browser
pause
