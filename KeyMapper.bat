@echo off
setlocal EnableExtensions
rem KeyMapper launcher: reuses a running server or starts one, opens the browser,
rem and exits when the server shuts itself down (all browser tabs closed).
rem Deliberately avoids PowerShell: health checks use Windows' built-in curl.exe
rem and delays use ping, so a broken PowerShell module path cannot break launch.
set "ROOT=%~dp0"
set "PORT=8756"
for /f "tokens=2 delims=: " %%p in ('findstr /r /c:"^ *port:" "%ROOT%backend\data\configuration\config.yaml"') do set "PORT=%%p"
set "URL=http://127.0.0.1:%PORT%"

curl -s -o nul -m 2 "%URL%/api/health"
if not errorlevel 1 (
    start "" "%URL%"
    exit /b 0
)

start "KeyMapper Server" /min /D "%ROOT%backend" "%ROOT%backend\.venv\Scripts\python.exe" -m src

set /a TRIES=0
:wait_up
curl -s -o nul -m 2 "%URL%/api/health"
if not errorlevel 1 goto up
set /a TRIES+=1
if %TRIES% geq 90 goto failed
ping -n 2 127.0.0.1 >nul
goto wait_up

:failed
echo KeyMapper server failed to start. Check backend\data\logs for details.
pause
exit /b 1

:up
start "" "%URL%"

:wait_down
curl -s -o nul -m 2 "%URL%/api/health"
if errorlevel 1 exit /b 0
ping -n 4 127.0.0.1 >nul
goto wait_down
