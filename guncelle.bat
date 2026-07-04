@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "SRC=%~dp0guncelleme\package"
if not exist "%SRC%" (
    echo [HATA] Guncelleme paketi bulunamadi: guncelleme\package
    pause
    exit /b 1
)

echo Tribal Wars Bot guncelleniyor...
echo Ayar dosyasi tw_config.json korunuyor.

if exist "%~dp0tw_config.json" (
    copy /Y "%~dp0tw_config.json" "%~dp0tw_config.json.bak" >nul
)

xcopy /E /Y /I "%SRC%\*" "%~dp0" >nul

if exist "%~dp0tw_config.json.bak" (
    move /Y "%~dp0tw_config.json.bak" "%~dp0tw_config.json" >nul
)

echo.
echo Guncelleme tamamlandi.

if exist "%~dp0TribalWarsBot.exe" (
    start "" "%~dp0TribalWarsBot.exe"
) else if exist "%~dp0tribal_wars_bot.exe" (
    start "" "%~dp0tribal_wars_bot.exe"
)

echo Bot yeniden baslatildi.
timeout /t 3 >nul
exit /b 0
