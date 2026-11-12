@echo off
setlocal
set PYTHONUTF8=1
python "%~dp0update_agent.py" sync
pause
