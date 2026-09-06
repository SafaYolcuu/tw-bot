@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "TWB_DIR=%CD%"

echo ============================================
echo   Tribal Wars Bot
echo ============================================
echo Klasor: %TWB_DIR%
echo.

echo %TWB_DIR% | findstr /I /C:"\Temp\" /C:"\AppData\Local\Temp" >nul
if not errorlevel 1 (
    echo [HATA] Bot TEMP klasorunden aciliyor. ZIP'i C:\TWBot\ gibi bir yere ayiklayin.
    pause
    exit /b 1
)

if not exist "TribalWarsBot.exe" (
    echo [HATA] TribalWarsBot.exe yok.
    pause
    exit /b 1
)
if not exist "_internal" (
    echo [HATA] _internal yok — ZIP'i tamamen ayiklayin.
    pause
    exit /b 1
)

REM Chromium sessiz cokme / Baslat'ta donma icin guvenli bayraklar
set "QTWEBENGINE_DISABLE_SANDBOX=1"
set "QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu --disable-gpu-compositing --no-sandbox --disable-software-rasterizer --disable-webgl --disable-3d-apis --disable-features=VizDisplayCompositor --disable-dev-shm-usage --in-process-gpu"

echo Baslatiliyor (guvenli WebEngine bayraklari)...
echo.

start "" /wait "TribalWarsBot.exe"
set "EC=%ERRORLEVEL%"

echo.
echo Cikis kodu: %EC%

if exist "boot.log" (
    echo.
    echo --- boot.log (son) ---
    powershell -NoProfile -Command "Get-Content -LiteralPath 'boot.log' -Tail 20 -ErrorAction SilentlyContinue"
)
if exist "crash.log" (
    echo.
    echo --- crash.log ---
    type "crash.log"
)

if not "%EC%"=="0" (
    echo.
    echo Donup kapandiysa:
    echo  1^) %%LOCALAPPDATA%%\TribalWarsBot\webengine_data klasorunu silin
    echo  2^) VC++ x64: https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo  3^) Botu C:\TWBot\ altinda calistirin ^(OneDrive/Masaustu degil^)
    echo  4^) boot.log gonderin
)

echo.
pause
