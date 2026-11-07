@echo off
setlocal
cd /d "%~dp0"
py -3.13 "%~dp0rebuild_master_lookup.py"
pause
endlocal
