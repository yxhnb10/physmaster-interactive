@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
python -X utf8 web_server.py --cfg_file config.yaml %*
endlocal
