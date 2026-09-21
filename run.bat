@echo off
chcp 1251 >nul
setlocal EnableExtensions
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe run.py %*
) else (
  python run.py %*
)
