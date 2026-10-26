@echo off
rem Luna Studio - automacao local da Naiaflow (deixe esta janela aberta)
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python luna_worker.py
pause
