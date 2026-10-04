@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ============================================
echo   Tribal Wars Bot - baslat
echo ============================================
echo.

if not exist "%~dp0TribalWarsBot.exe" (
    echo HATA: TribalWarsBot.exe bu klasorde yok.
    echo ZIP'i tam cikardiniz mi?
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0_internal\" (
    echo HATA: _internal klasoru yok.
    echo ZIP'i bir klasore TAM cikartin; zip icinden calistirmayin.
    echo.
    pause
    exit /b 1
)

echo Baslatiliyor...
start "" "%~dp0TribalWarsBot.exe"
if errorlevel 1 (
    echo.
    echo HATA: Bot baslatilamadi.
    echo 1^) VC++ x64 kurun: https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo 2^) gerekler-kontrol.bat calistirin
    echo 3^) boot.log / crash.log varsa destek ekibine gonderin
    echo.
    pause
    exit /b 1
)

exit /b 0
