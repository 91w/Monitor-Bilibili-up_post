@echo off
setlocal
set PYTHONUTF8=1
python "%~dp0board_status.py" --session
pause
