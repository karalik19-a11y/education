@echo off
chcp 65001 >nul
rem Установка "Книжного стража" (Windows). Запуск: install.bat
cd /d "%~dp0"
echo === Установка "Книжный страж" ===

where python >nul 2>nul
if errorlevel 1 (
  echo Не найден Python 3. Скачайте с https://python.org и при установке отметьте "Add to PATH".
  pause
  exit /b 1
)

echo --- 1/4 Виртуальное окружение...
if not exist .venv (
  python -m venv .venv
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -q vosk sounddevice

echo --- 2/4 Модель распознавания речи (русский, офлайн)...
if not exist models\vosk-model-small-ru (
  mkdir models 2>nul
  powershell -Command "try { Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip' -OutFile 'models\vosk-ru.zip' -UseBasicParsing } catch { exit 1 }"
  if exist models\vosk-ru.zip (
    powershell -Command "Expand-Archive -Force 'models\vosk-ru.zip' 'models'"
    ren models\vosk-model-small-ru-0.22 vosk-model-small-ru
    del models\vosk-ru.zip
  ) else (
    echo Не удалось скачать с alphacephei.com, пробую зеркало GitHub...
    powershell -Command "try { Invoke-WebRequest -Uri 'https://codeload.github.com/s1reeen/Klara/zip/refs/heads/main' -OutFile 'models\klara.zip' -UseBasicParsing } catch { exit 1 }"
    powershell -Command "Expand-Archive -Force 'models\klara.zip' 'models'"
    xcopy /E /I /Y models\Klara-main\vosk-model-small-ru models\vosk-model-small-ru >nul
    rd /s /q models\Klara-main
    del models\klara.zip
  )
)

echo --- 3/4 База данных библиотеки...
if not exist data\library.db (
  echo В комплекте должна быть data\library.db. Если её нет - сообщите разработчику.
)

echo --- 4/5 Самопроверка...
.venv\Scripts\python.exe -m bookguard selftest

echo --- 5/5 Автозапуск при включении компьютера...
echo Создаю ярлык "Книжный страж (админ)" на рабочем столе...
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%USERPROFILE%\Desktop\Книжный страж (админ).lnk'); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"
.venv\Scripts\python.exe -m bookguard autostart on
if errorlevel 1 (
  echo ВНИМАНИЕ: автозапуск включить не удалось. Запустите install.bat
  echo "правой кнопкой - Запуск от имени администратора" и повторите.
)

echo.
echo === Установка завершена! ===
echo "Книжный страж" будет сам запускаться при входе в Windows и блокировать Wi-Fi.
echo Отключить автозапуск: run.bat autostart off
echo Запуск вручную: ярлык "Книжный страж (админ)" или run.bat
echo Демо без микрофона: run.bat demo
pause
