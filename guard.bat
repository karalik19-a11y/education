@echo off
rem Фоновый «страж» (защита от удаления) — скрытый запуск, без окна.
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe -m bookguard guard
) else (
  start "" /min .venv\Scripts\python.exe -m bookguard guard
)
exit /b 0
