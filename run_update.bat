@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install it from:
  echo https://www.python.org/downloads/
  echo During install, check "Add python.exe to PATH".
  pause
  exit /b 1
)
python update_data.py
echo.
pause
