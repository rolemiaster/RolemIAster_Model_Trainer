@echo off
setlocal

cd /d "%~dp0"
title ABAFE Models Trainer - BUILDER

echo =======================================================
echo    ABAFE Models Trainer - BUILD PROCESS
echo =======================================================

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

REM --- 1. CHECK VENV ---
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found at .venv
    echo Please run 'run_trainer.bat' first to setup the environment.
    pause
    exit /b 1
)

echo [INFO] Using venv: %VENV_PYTHON%

REM --- 2. ENSURE PYINSTALLER IS INSTALLED IN VENV ---
echo [INFO] Checking for PyInstaller...
"%VENV_PYTHON%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller in venv...
    "%VENV_PYTHON%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

REM --- 3. RUN BUILD SCRIPT ---
echo [INFO] Starting build_trainer.py...
"%VENV_PYTHON%" build_trainer.py --type folder

if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo [SUCCESS] Build completed.
pause
