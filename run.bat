@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "DAILY_DIR=%SCRIPT_DIR%wechat-daily"

if "%~1"=="--cli" (
  shift
  call "%DAILY_DIR%\run_group_daily.bat" %*
  exit /b %ERRORLEVEL%
)

set "DEFAULT_CONFIG=%DAILY_DIR%\config.yaml"
if exist "%DAILY_DIR%\config.local.yaml" set "DEFAULT_CONFIG=%DAILY_DIR%\config.local.yaml"
set "CONFIG_PATH=%DEFAULT_CONFIG%"
if not "%~1"=="" set "CONFIG_PATH=%~1"

set "PYTHON=%DAILY_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

set "FIRST_ARG=%~1"
if not "%FIRST_ARG%"=="" (
  if "%FIRST_ARG:~0,2%"=="--" (
    "%PYTHON%" "%DAILY_DIR%\web_ui.py" %*
    exit /b %ERRORLEVEL%
  )
)

"%PYTHON%" "%DAILY_DIR%\web_ui.py" --config "%CONFIG_PATH%"
exit /b %ERRORLEVEL%
