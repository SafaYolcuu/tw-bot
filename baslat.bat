@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "tribal_wars_bot.py" (
  echo tribal_wars_bot.py bulunamadi: %~dp0
  pause
  exit /b 1
)
py -3 tribal_wars_bot.py
if %errorlevel% equ 0 exit /b 0
python tribal_wars_bot.py
if %errorlevel% equ 0 exit /b 0
echo.
echo [HATA] py veya python calistirilamadi. Python kurulumunu ve PATH'i kontrol edin.
pause
exit /b 1
