@echo off
chcp 65001 >nul
rem ============================================================
rem  УСТАНОВЩИК «КНИЖНОГО СТРАЖА» (Windows)
rem  Запуск: install.bat  (лучше «правой кнопкой - от имени администратора»)
rem ============================================================
cd /d "%~dp0"
setlocal EnableDelayedExpansion

echo ============================================================
echo   УСТАНОВКА «КНИЖНОГО СТРАЖА»
echo ============================================================
echo.

rem ---------- 0/6 проверка прав ----------
net session >nul 2>nul
if %errorlevel%==0 (
  echo [i] Права администратора - отлично (Wi-Fi блокируется сразу).
) else (
  echo [!] Запуск НЕ от имени администратора.
  echo     Установка продолжится, но блокировка Wi-Fi будет требовать
  echo     подтверждение UAC. Рекомендую: повторить «от имени администратора».
  echo.
)

rem ---------- 1/6 Python ----------
echo --- 1/6 Проверяю Python ---
where python >nul 2>nul
if errorlevel 1 (
  echo [x] Не найден Python 3.
  echo     Скачайте с https://python.org  (галочка "Add Python to PATH")
  echo     и повторите установку.
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python -c "import sys; print(sys.version_info.major)"') do set PYVER=%%v
if "%PYVER%"=="3" (
  echo [ok] Python 3 найден.
) else (
  echo [x] Нужен Python 3 (найден: %PYVER%).
  pause
  exit /b 1
)

rem ---------- 2/6 виртуальное окружение ----------
echo --- 2/6 Виртуальное окружение и зависимости ---
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -q vosk sounddevice
if errorlevel 1 (
  echo [x] Не удалось установить vosk/sounddevice (нужен интернет).
  pause
  exit /b 1
)
echo [ok] vosk + sounddevice установлены.

rem ---------- 3/6 модель распознавания ----------
echo --- 3/6 Модель распознавания речи (офлайн, русский) ---
if not exist models mkdir models
set CHOOSE=1
if not exist models\vosk-model-small-ru if not exist models\vosk-model-ru-big (
  echo.
  echo  1) Быстрая модель            ~33 МБ   (для слабых компьютеров)
  echo  2) Максимальная точность     ~1.4 ГБ  (минимум ошибок в словах)
  echo.
  set /p CHOOSE="Выберите (1 или 2,Enter=1): "
  if "%CHOOSE%"=="" set CHOOSE=1
)
if "%CHOOSE%"=="2" (
  if not exist models\vosk-model-ru-big (
    echo Скачиваю модель «максимальная точность» (~1.4 ГБ, это долго)...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip' -OutFile 'models\vosk-big.zip' -UseBasicParsing } catch { exit 1 }"
    if not exist models\vosk-big.zip (
      echo [x] Не удалось скачать большую модель. Установится быстрая.
      set CHOOSE=1
    ) else (
      powershell -NoProfile -Command "Expand-Archive -Force 'models\vosk-big.zip' 'models'"
      ren models\vosk-model-ru-0.42 vosk-model-ru-big 2>nul
      del models\vosk-big.zip 2>nul
    )
  )
)
if not exist models\vosk-model-ru-big if "%CHOOSE%"=="1" if not exist models\vosk-model-small-ru (
  echo Скачиваю быструю модель (~33 МБ)...
  powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip' -OutFile 'models\vosk-ru.zip' -UseBasicParsing } catch { exit 1 }"
  if exist models\vosk-ru.zip (
    powershell -NoProfile -Command "Expand-Archive -Force 'models\vosk-ru.zip' 'models'"
    ren models\vosk-model-small-ru-0.22 vosk-model-small-ru 2>nul
    del models\vosk-ru.zip 2>nul
  ) else (
    echo alphacephei.com недоступен - пробую зеркало GitHub...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://codeload.github.com/s1reeen/Klara/zip/refs/heads/main' -OutFile 'models\klara.zip' -UseBasicParsing } catch { exit 1 }"
    powershell -NoProfile -Command "Expand-Archive -Force 'models\klara.zip' 'models'"
    xcopy /E /I /Y models\Klara-main\vosk-model-small-ru models\vosk-model-small-ru >nul 2>nul
    rd /s /q models\Klara-main 2>nul
    del models\klara.zip 2>nul
  )
)
if "%CHOOSE%"=="2" (
  .venv\Scripts\python.exe -m bookguard model big
) else (
  .venv\Scripts\python.exe -m bookguard model fast
)

rem ---------- 4/6 база и самопроверка ----------
echo --- 4/6 База данных и самопроверка ---
if not exist data\library.db (
  echo [x] В комплекте должна быть data\library.db. Сообщите разработчику.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m bookguard selftest
if errorlevel 1 (
  echo [x] Самопроверка не прошла. Подробности выше.
  pause
  exit /b 1
)
echo [ok] Самопроверка пройдена.

rem ---------- 5/6 ярлыки на рабочем столе ----------
echo --- 5/6 Ярлыки ---
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut(\"$env:USERPROFILE\Desktop\Книжный страж.lnk\");" ^
  "$lnk.TargetPath = '%~dp0run.bat';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = 'shell32.dll,137';" ^
  "$lnk.Description = 'Книжный страж';" ^
  "$lnk.Save();" ^
  "$lnk2 = $ws.CreateShortcut(\"$env:USERPROFILE\Desktop\Книжный страж (админ).lnk\");" ^
  "$lnk2.TargetPath = '%~dp0run.bat';" ^
  "$lnk2.WorkingDirectory = '%~dp0';" ^
  "$lnk2.IconLocation = 'shell32.dll,137';" ^
  "$lnk2.Description = 'Книжный страж (от имени администратора)';" ^
  "$lnk2.RunLevel = 1;" ^
  "$lnk2.Save(); echo 'ярлыки созданы'"
echo [ok] Ярлыки: «Книжный страж» и «Книжный страж (админ)».

rem ---------- 6/6 автозапуск + защита от удаления ----------
echo --- 6/6 Автозапуск и защита от удаления ---
.venv\Scripts\python.exe -m bookguard autostart on
if errorlevel 1 (
  echo [!] Автозапуск включить не удалось.
  echo     Повторите: правый клик по install.bat - «Запуск от имени администратора».
) else (
  echo [ok] Автозапуск: при входе в систему программа стартует сама.
)
.venv\Scripts\python.exe -m bookguard guard-setup
echo [ok] Защита от удаления: программа не удаляется без пароля 67676767.

echo.
echo ============================================================
echo   УСТАНОВКА ЗАВЕРШЕНА
echo ============================================================
echo  * Программа стартует сама при включении компьютера и блокирует Wi-Fi.
echo  * Ярлык «Книжный страж (админ)» — запуск с правами администратора.
echo  * Удаление программы: uninstall.bat  (нужен пароль 67676767).
echo  * Демо без микрофона: run.bat demo
echo ============================================================
echo.
set /p OPEN="Открыть программу сейчас? (Y/N): "
if /i "%OPEN%"=="Y" (
  start "" "%~dp0run.bat"
)
endlocal
exit /b 0
