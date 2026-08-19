@echo off
chcp 65001 >nul
title Nigoh — kamera xaritasi
cd /d "%~dp0"

echo.
echo   NIGOH — ishga tushmoqda
echo   ------------------------
echo.

if not exist "venv\Scripts\python.exe" (
  echo   [!] venv topilmadi. Avval quyidagini bajaring:
  echo         py -m venv venv
  echo         venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

if not exist "mediamtx\mediamtx.exe" (
  echo   [!] mediamtx\mediamtx.exe topilmadi.
  echo       https://github.com/bluenviron/mediamtx/releases dan
  echo       windows_amd64 arxivini yuklab, mediamtx papkasiga chiqaring.
  echo.
  pause
  exit /b 1
)

if not exist "mediamtx.yml" (
  echo   [*] mediamtx.yml yaratilmoqda...
  venv\Scripts\python.exe -c "import main"
)

echo   [*] Eski jarayonlar to'xtatilmoqda...
taskkill /IM mediamtx.exe /F >nul 2>&1
taskkill /IM ffmpeg.exe /F >nul 2>&1

echo   [*] MediaMTX ishga tushmoqda (video oqimlar)...
start "MediaMTX" /min mediamtx\mediamtx.exe mediamtx.yml

echo   [*] Kameralar ulanmoqda...
timeout /t 6 /nobreak >nul

echo   [*] Sayt ishga tushmoqda...
start "" http://localhost:8010
venv\Scripts\python.exe main.py

echo.
echo   Sayt to'xtatildi. MediaMTX ham yopilmoqda...
taskkill /IM mediamtx.exe /F >nul 2>&1
taskkill /IM ffmpeg.exe /F >nul 2>&1
