@echo off
setlocal
cd /d "%~dp0"

set PORT=5000

where uv >nul 2>&1
if errorlevel 1 (
    echo Installing uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

uv sync

REM Free port if occupied by a previous run
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    echo Killing existing process on port %PORT% (PID: %%a)...
    taskkill /PID %%a /F >nul 2>&1
)

echo Starting server on port %PORT%...
uv run loanratio

echo.
echo Server stopped. Cleaning up port %PORT%...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo Done.
endlocal
