@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "DEFAULT_CONFIG=%SCRIPT_DIR%config.yaml"
if exist "%SCRIPT_DIR%config.local.yaml" set "DEFAULT_CONFIG=%SCRIPT_DIR%config.local.yaml"
set "CONFIG_PATH=%DEFAULT_CONFIG%"
if not "%~1"=="" set "CONFIG_PATH=%~1"

set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [run_group_daily] using config: %CONFIG_PATH%
"%PYTHON%" "%SCRIPT_DIR%run_group_daily_pipeline.py" --config "%CONFIG_PATH%"
exit /b %ERRORLEVEL%
