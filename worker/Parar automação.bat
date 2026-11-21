@echo off
rem Luna Studio - desliga a automacao (robo escondido, janela preta e Chrome da Naiaflow)
powershell -NoProfile -Command "$n = 0; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'luna_worker|perfil-naiaflow|Iniciar automa' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; if ($n) { 'Automacao desligada.' } else { 'A automacao ja estava desligada.' }"
echo Para ligar de novo: Iniciar automacao (escondida).vbs
timeout /t 8
