@echo off
REM Command Centre dashboard launcher.
REM Started by Task Scheduler (and usable manually). Uses the explicit
REM Python interpreter and the project directory, redirects stdout+stderr
REM to a log, and rotates the log when it grows past LOG_MAX bytes.
REM
REM This does NOT change the dashboard bind (127.0.0.1) or any config.

setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"

REM The scheduled task always boots on PROD. The runtime UI switch is
REM temporary and is not persisted.
set "PYEXE=C:\Users\ruair\AppData\Local\hermes\tools\python-3.14.7+202****0901-win32-x64\python.exe"
set "LOG_DIR=%PROJECT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\dashboard.log"
set "LOG_MAX=5242880"
if not defined CC_HOME set "CC_HOME=%PROJECT_DIR%..\command-center-prod"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if not exist "%PYEXE%" (
    where python >nul 2>nul && set "PYEXE=python" || (
        echo [launcher] ERROR: no Python interpreter found >> "%LOG_FILE%" 2>&1
        exit /b 1
    )
)

REM --- bounded log rotation: keep at most one .1 archive ---
set "LOG_SIZE=0"
for %%A in ("%LOG_FILE%") do set "LOG_SIZE=%%~zA"
if !LOG_SIZE! GTR %LOG_MAX% (
    if exist "%LOG_FILE%.1" del /q "%LOG_FILE%.1"
    move /y "%LOG_FILE%" "%LOG_FILE%.1" >nul
)

cd /d "%PROJECT_DIR%"
"%PYEXE%" lib\cc_dashboard.py >> "%LOG_FILE%" 2>&1
exit /b %ERRORLEVEL%
