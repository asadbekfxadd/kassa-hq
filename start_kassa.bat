@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "Kassa server" /min cmd /c "%~dp0run_server.bat"
timeout /t 4 /nobreak >nul
set CHROME="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist %CHROME% (
  start "" %CHROME% --app=http://127.0.0.1:5050 --kiosk-printing --start-maximized --user-data-dir="%~dp0data\chrome"
) else (
  start "" msedge --app=http://127.0.0.1:5050 --kiosk-printing --start-maximized --user-data-dir="%~dp0data\edge"
)
