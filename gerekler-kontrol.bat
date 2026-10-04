@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal EnableExtensions

echo ============================================
echo   Tribal Wars Bot - sistem kontrolu
echo ============================================
echo.
echo Klasor: %~dp0
echo.

set FAIL=0

REM --- Klasor yapisi ---
if exist "%~dp0TribalWarsBot.exe" (
    echo [OK] TribalWarsBot.exe
) else (
    echo [HATA] TribalWarsBot.exe yok
    set FAIL=1
)

if exist "%~dp0_internal\" (
    echo [OK] _internal klasoru
) else (
    echo [HATA] _internal klasoru yok — ZIP'i tam cikarin
    set FAIL=1
)

REM --- Qt WebEngine (PyInstaller layout: _internal\PyQt5\Qt5\...) ---
set WE_OK=0
if exist "%~dp0_internal\PyQt5\Qt5\bin\QtWebEngineProcess.exe" set WE_OK=1
if exist "%~dp0_internal\QtWebEngineProcess.exe" set WE_OK=1
if exist "%~dp0_internal\PyQt5\Qt5\bin\Qt5WebEngineCore.dll" set WE_OK=1
if exist "%~dp0_internal\Qt5WebEngineCore.dll" set WE_OK=1

if "%WE_OK%"=="1" (
    echo [OK] Qt WebEngine dosyalari gorunuyor
) else (
    echo [UYARI] QtWebEngineProcess / Qt5WebEngine* bulunamadi
    echo         Paket eksik olabilir; ZIP'i yeniden indirip cikarin
    set FAIL=1
)

REM --- VC++ runtime (System32) ---
if exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [OK] vcruntime140.dll ^(System32^)
) else (
    echo [HATA] vcruntime140.dll yok — VC++ x64 kurun:
    echo        https://aka.ms/vs/17/release/vc_redist.x64.exe
    set FAIL=1
)

if exist "%SystemRoot%\System32\msvcp140.dll" (
    echo [OK] msvcp140.dll ^(System32^)
) else (
    echo [UYARI] msvcp140.dll yok — VC++ x64 kurulumu eksik olabilir
    set FAIL=1
)

REM --- Mimari ipucu ---
echo.
echo Windows: %OS%
if defined PROCESSOR_ARCHITECTURE (
    echo Islemci: %PROCESSOR_ARCHITECTURE%
    if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
        if /I not "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
            echo [UYARI] 64-bit Windows onerilir; 32-bit desteklenmez
            set FAIL=1
        )
    )
)

echo.
if "%FAIL%"=="0" (
    echo Sonuc: GEREKSINIMLER TAMAM — baslat.bat ile acabilirsiniz.
) else (
    echo Sonuc: EKSIKLER VAR — README_KURULUM.txt okuyun.
    echo VC++: https://aka.ms/vs/17/release/vc_redist.x64.exe
)
echo.
pause
endlocal
exit /b %FAIL%
