@echo off
chcp 65001 >nul
title Nigoh — kamera xaritasi
cd /d "%~dp0"

rem `.env` shu yerda o'qiladi. main.py uni O'QIMAYDI (python-dotenv
rem bog'liqlik sifatida qo'shilmagan) — ya'ni usiz servis .env dagi emas,
rem standart portlarda ko'tarilardi. Bitta mashinada ikkinchi Nigoh
rem o'rnatmasi bo'lsa bu portlar to'qnashadi va ikkala backend bitta
rem MediaMTX'ni tortib, bir-birining yo'llarini o'chirib turadi —
rem kameralar uzilib, qotib qoladi.
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    set "_k=%%a"
    setlocal enabledelayedexpansion
    if not "!_k:~0,1!"=="#" if not "%%b"=="" (
      endlocal
      set "%%a=%%b"
    ) else (
      endlocal
    )
  )
)

rem Lokal ishga tushirish — debug UI yoqiq bo'lsin (ishlab chiqarishda 0).
if not defined ENABLE_UI set ENABLE_UI=1
rem NIGOH_API_KEY majburiy (servis usiz ko'tarilmaydi). Lokal sinov uchun
rem vaqtinchalik kalit beriladi — tashqi tarmoqqa ochmang; haqiqiy muhitda
rem o'zingizning uzun kalitingizni qo'ying.
if not defined NIGOH_API_KEY (
  set NIGOH_API_KEY=lokal-dev-kalit-%RANDOM%%RANDOM%
  echo   [i] NIGOH_API_KEY berilmagan - vaqtinchalik lokal kalit ishlatiladi.
)

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

rem mediamtx.yml HAR SAFAR qayta yoziladi. Ilgari u faqat fayl yo'q
rem bo'lsa yaratilardi: .env dagi port o'zgarsa (masalan MEDIAMTX_API)
rem eski fayl qolib ketardi, MediaMTX band portga urinib ko'tarilmasdi,
rem reconciler esa qo'shni o'rnatmaning API'sini ko'rib unga ulanardi va
rem har 30 soniyada uning yo'llarini o'chirardi - kameralar uzilardi.
echo   [*] mediamtx.yml yangilanmoqda...
venv\Scripts\python.exe -c "import main"

rem Faqat SHU papkadagi jarayonlar to'xtatiladi. Ilgari bu yerda
rem `taskkill /IM mediamtx.exe /F` turardi va u mashinadagi HAR QANDAY
rem MediaMTX'ni o'ldirardi — shu jumladan ikkinchi loyihanikini. Natijada
rem ikki o'rnatma bitta 9997-portni bo'lishib qolardi va har 30 soniyada
rem bir-birining yo'llarini o'chirib turardi: kameralar uzilib, qotib
rem qolardi. Sabab esa jurnalda ko'rinmasdi.
echo   [*] Shu papkadagi eski jarayonlar to'xtatilmoqda...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\toxtatish.ps1"

echo   [*] MediaMTX ishga tushmoqda (video oqimlar)...
start "MediaMTX" /min mediamtx\mediamtx.exe mediamtx.yml

echo   [*] Kameralar ulanmoqda...
timeout /t 6 /nobreak >nul

if not defined PORT set PORT=8010
echo   [*] Sayt ishga tushmoqda (port %PORT%)...
start "" http://localhost:%PORT%
venv\Scripts\python.exe main.py

echo.
echo   Sayt to'xtatildi. MediaMTX ham yopilmoqda...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\toxtatish.ps1"
