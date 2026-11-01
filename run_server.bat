@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Kassa server
:loop
if exist data\update_applied.flag (
  echo Обновление: установка зависимостей...
  python -m pip install -q -r requirements.txt
  del data\update_applied.flag
)
python app.py
if %errorlevel%==3 goto loop
echo Сервер кассы остановлен. Нажмите любую клавишу, чтобы закрыть.
pause >nul
