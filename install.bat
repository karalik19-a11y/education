@echo off
setlocal
chcp 65001 >nul
rem Bookguard Windows installer. ASCII-only; keep CRLF line endings.
cd /d "%~dp0"
if errorlevel 1 goto :failed
echo === Bookguard installer v2 - Windows ASCII ===

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 from https://python.org with "Add to PATH".
  pause
  exit /b 1
)

echo --- 1/5 Creating Python environment...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install vosk sounddevice
if errorlevel 1 goto :failed

echo --- 2/5 Downloading offline Russian speech model...
if not exist models\vosk-model-small-ru (
  mkdir models 2>nul
  powershell -Command "try { Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip' -OutFile 'models\vosk-ru.zip' -UseBasicParsing } catch { exit 1 }"
  if not errorlevel 1 (
    powershell -Command "$ErrorActionPreference='Stop'; Expand-Archive -Force 'models\vosk-ru.zip' 'models'"
    if errorlevel 1 goto :failed
    ren models\vosk-model-small-ru-0.22 vosk-model-small-ru
    if errorlevel 1 goto :failed
    del models\vosk-ru.zip
  ) else (
    echo Download failed. Trying the GitHub mirror...
    powershell -Command "try { Invoke-WebRequest -Uri 'https://codeload.github.com/s1reeen/Klara/zip/refs/heads/main' -OutFile 'models\klara.zip' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 goto :failed
    powershell -Command "$ErrorActionPreference='Stop'; Expand-Archive -Force 'models\klara.zip' 'models'"
    if errorlevel 1 goto :failed
    xcopy /E /I /Y models\Klara-main\vosk-model-small-ru models\vosk-model-small-ru >nul
    if errorlevel 1 goto :failed
    rd /s /q models\Klara-main
    del models\klara.zip
  )
)

echo --- 3/5 Checking library database...
if not exist data\library.db (
  echo Missing data\library.db. Extract the entire project archive first.
  goto :failed
)

echo --- 4/5 Running self-test...
".venv\Scripts\python.exe" -m bookguard selftest
if errorlevel 1 goto :failed

echo --- 5/5 Configuring startup...
echo Creating desktop shortcut...
set "BOOKGUARD_ROOT=%CD%"
powershell -NoProfile -EncodedCommand JABFAHIAcgBvAHIAQQBjAHQAaQBvAG4AUAByAGUAZgBlAHIAZQBuAGMAZQA9ACcAUwB0AG8AcAAnADsAIAAkAGQAZQBzAGsAdABvAHAAPQBbAEUAbgB2AGkAcgBvAG4AbQBlAG4AdABdADoAOgBHAGUAdABGAG8AbABkAGUAcgBQAGEAdABoACgAJwBEAGUAcwBrAHQAbwBwACcAKQA7ACAAJABzAD0AKABOAGUAdwAtAE8AYgBqAGUAYwB0ACAALQBDAE8ATQAgAFcAUwBjAHIAaQBwAHQALgBTAGgAZQBsAGwAKQAuAEMAcgBlAGEAdABlAFMAaABvAHIAdABjAHUAdAAoACgASgBvAGkAbgAtAFAAYQB0AGgAIAAkAGQAZQBzAGsAdABvAHAAIAAnABoEPQQ4BDYEPQRLBDkEIABBBEIEQAQwBDYEIAAoADAENAQ8BDgEPQQpAC4AbABuAGsAJwApACkAOwAgACQAcwAuAFQAYQByAGcAZQB0AFAAYQB0AGgAPQBKAG8AaQBuAC0AUABhAHQAaAAgACQAZQBuAHYAOgBCAE8ATwBLAEcAVQBBAFIARABfAFIATwBPAFQAIAAnAHIAdQBuAC4AYgBhAHQAJwA7ACAAJABzAC4AVwBvAHIAawBpAG4AZwBEAGkAcgBlAGMAdABvAHIAeQA9ACQAZQBuAHYAOgBCAE8ATwBLAEcAVQBBAFIARABfAFIATwBPAFQAOwAgACQAcwAuAFMAYQB2AGUAKAApAA==
if errorlevel 1 echo WARNING: Desktop shortcut could not be created. Use run.bat.
.venv\Scripts\python.exe -m bookguard autostart on
if errorlevel 1 (
  echo WARNING: Could not enable startup. Run install.bat as administrator.
  echo You can retry later with: run.bat autostart on
)
.venv\Scripts\python.exe -m bookguard autostart doctor
if errorlevel 1 (
  echo WARNING: Startup check failed, see details above.
)

echo.
echo === Installation completed! ===
echo When startup is enabled, Bookguard starts at login and blocks Wi-Fi.
echo Disable startup: run.bat autostart off
echo Start manually: desktop shortcut or run.bat
echo Demo without microphone: run.bat demo
pause
exit /b 0

:failed
echo.
echo ERROR: Installation failed. Copy the error message above.
pause
exit /b 1
