@echo off
setlocal
set PYTHONUTF8=1
python "%~dp0history_test.py" --send
pause
