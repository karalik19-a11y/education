@echo off
chcp 65001 >nul
rem ============================================================
rem  Запуск «Книжного стража» (Windows).  run.bat [аргументы]
rem  * стартовое окно открывается ВСЕГДА (до 3 попыток с логи);
rem  * фоновый «страж» (защита от удаления) запускается сам, если жив;
rem  * ошибки пишутся в data\startup.log и data\app.log.
rem ============================================================
cd /d "%~dp0"
if not exist data mkdir data

rem ---------- фоновый страж (скрытый, один экземпляр) ----------
if exist .venv\Scripts\pythonw.exe (
  start "" /min .venv\Scripts\pythonw.exe -m bookguard guard
) else (
  where pythonw >nul 2>nul
  if %errorlevel%==0 (start "" /min pythonw -m bookguard guard)
)

rem ---------- самочиняющийся запуск приложения ----------
set BG_ATTEMPT=0
:BG_LOOP
set /a BG_ATTEMPT+=1
if %BG_ATTEMPT% LEQ 1 goto :BG_RUN
echo.
echo Не удалось запустить (попытка %BG_ATTEMPT%/3). Повтор через 2 секунды...
echo Подробности: data\startup.log
timeout /t 2 /nobreak >nul
:BG_RUN
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe run.py %* 2>> data\startup.log
) else (
  python run.py %* 2>> data\startup.log
)
if %errorlevel%==0 exit /b 0
if %BG_ATTEMPT% LSS 3 goto :BG_LOOP

echo.
echo ============================================================
echo   НЕ УДАЛОСЬ ЗАПУСТИТЬ ПРОГРАММУ
echo   См. data\startup.log и data\app.log
echo   Для восстановления запустите:  install.bat
echo ============================================================
pause
exit /b 1
