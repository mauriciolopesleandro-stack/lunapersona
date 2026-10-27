@echo off
rem Luna Studio - automacao local da Naiaflow (deixe esta janela aberta)
rem Abre sozinha quando o Windows liga (atalho na pasta Inicializar).
cd /d "%~dp0"
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*luna_worker.py*' }) { exit 1 }"
if errorlevel 1 (
  echo A automacao ja esta aberta em outra janela.
  timeout /t 10
  exit /b
)
python -m pip install -q -r requirements.txt
:rodar
python luna_worker.py
rem parou por erro (ex.: sem internet logo depois de ligar o PC): tenta de novo em 2 minutos
echo.
echo A automacao parou. Tento de novo em 2 minutos (feche esta janela para desligar).
timeout /t 120
goto rodar
