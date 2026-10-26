@echo off
rem Luna Studio - login na Naiaflow (so na primeira vez, ou se a sessao expirar)
rem
rem O Google bloqueia o login com conta Google no Chrome controlado pelo robo
rem ("Esse navegador ou app pode nao ser seguro"). Este atalho abre o Chrome
rem NORMAL, sem automacao, mas usando a mesma pasta de perfil do robo
rem (.perfil-naiaflow): o login feito aqui fica salvo e o robo ja abre logado.
rem
rem Feche a janela do robo (Iniciar automacao.bat) antes: o mesmo perfil nao
rem abre em dois Chromes ao mesmo tempo.
cd /d "%~dp0"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo Nao achei o Google Chrome instalado.
    pause
    exit /b 1
)

rem O Chrome do robo as vezes continua aberto depois que o robo fecha; ai o
rem Chrome abaixo so abriria uma aba DENTRO dele (ainda em modo automacao, e
rem o Google bloquearia de novo). Fecha qualquer Chrome com este perfil antes.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*perfil-naiaflow*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

echo.
echo 1. Na janela do Chrome que vai abrir, entre na Naiaflow com a sua conta Google.
echo 2. Quando aparecer a tela de geracao da Naiaflow, FECHE essa janela do Chrome.
echo 3. Depois abra "Iniciar automacao.bat" - o robo ja vai entrar logado.
echo.
start "" "%CHROME%" --user-data-dir="%~dp0.perfil-naiaflow" --no-first-run --no-default-browser-check https://app.naiaflow.com/generations
pause
