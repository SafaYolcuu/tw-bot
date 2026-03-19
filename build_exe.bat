@echo off
chcp 65001 >nul 2>&1
echo ============================================
echo   Tribal Wars Bot - EXE Build
echo ============================================
echo.

echo [1/3] PyInstaller kontrol ediliyor...
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo       PyInstaller bulunamadi, yukleniyor...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo HATA: PyInstaller yuklenemedi!
        pause
        exit /b 1
    )
)
echo       PyInstaller hazir.
echo.

echo [2/3] Build baslatiliyor...
echo       Bu islem birkaç dakika surebilir...
echo.
pyinstaller tribal_wars_bot.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo.
    echo HATA: Build basarisiz!
    pause
    exit /b 1
)
echo.

echo [3/3] Kurulum dosyasi kopyalaniyor...
copy /Y README_KURULUM.txt dist\TribalWarsBot\ >nul 2>&1
echo.
echo ============================================
echo   BUILD TAMAMLANDI!
echo   Cikti klasoru: dist\TribalWarsBot\
echo   Kullaniciya bu klasoru zip'leyip gonderin.
echo ============================================
echo.
pause
