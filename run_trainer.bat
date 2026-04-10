@echo off
setlocal

cd /d "%~dp0"
title ABAFE Models Trainer - DEV LAUNCHER

echo =======================================================
echo    ABAFE Models Trainer - DEV MODE
echo =======================================================

REM --- MSVC: Requerido para que Triton compile kernels CUDA en runtime (GPU) ---
REM Sin esto, Triton no encuentra compilador C++ y FLA cae silenciosamente a CPU.
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PYTHONUTF8=1
set DISTUTILS_USE_SDK=1
set CUDA_HOME=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8
set PATH=%CUDA_HOME%\bin;%PATH%

REM --- 1. DETECT PYTHON 3.12 ---
set "PYTHON_BOOTSTRAP="
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_BOOTSTRAP=py -3.12"
    echo [INFO] Python 3.12 detected via py launcher.
) else (
    python --version 2>&1 | findstr "3.12" >nul
    if not errorlevel 1 (
        set "PYTHON_BOOTSTRAP=python"
        echo [INFO] Python 3.12 detected via PATH python.
    )
)

if "%PYTHON_BOOTSTRAP%"=="" (
    echo [ERROR] Python 3.12 not found.
    pause
    exit /b 1
)

REM --- 2. CREATE VENV IF NEEDED ---
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating .venv...
    %PYTHON_BOOTSTRAP% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
)

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Missing venv python at %VENV_PYTHON%
    pause
    exit /b 1
)

echo [INFO] Using venv python: %VENV_PYTHON%

REM ===========================================================================
REM  3. DEPENDENCIAS â€” TODAS LAS VERSIONES FIJADAS
REM  NO usar --upgrade en NINGÃšN pip install.
REM  Cambiar versiones SOLO con validaciÃ³n manual previa.
REM  Ãšltima auditorÃa: 2026-03-10
REM ===========================================================================
echo [INFO] Checking dependencies (versiones fijadas)...
"%VENV_PYTHON%" -m pip install pip==25.1.1
if errorlevel 1 goto :pip_error

"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_error

REM [CUDA 12.8 / sm_120] Use PyTorch cu128 builds for compatibility with newer NVIDIA architectures.
REM Uncomment the option below that matches your installation preference.
"%VENV_PYTHON%" -m pip install torch==2.5.1+cu128 torchvision==0.20.1+cu128 torchaudio==2.5.1+cu128 --index-url https://download.pytorch.org/whl/cu128
REM "%VENV_PYTHON%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128
if errorlevel 1 goto :pip_error

REM Unsloth â€” commit fijado, NO latest
REM "%VENV_PYTHON%" -m pip install "unsloth==2026.3.4" --no-deps
REM if errorlevel 1 goto :pip_error

REM Unsloth Zoo â€” versiÃ³n fijada, NO --upgrade
REM "%VENV_PYTHON%" -m pip install "unsloth_zoo==2026.3.2"
REM if errorlevel 1 goto :pip_error

REM Triton para Windows â€” versiÃ³n fijada
REM "%VENV_PYTHON%" -m pip install "triton-windows==3.6.0.post25"
REM if errorlevel 1 (
REM     echo [WARN] triton-windows 3.6.0.post25 no disponible. Omitiendo.
REM )

REM --- 4. ENGINE CHECK ---
if not exist "src\engines\cuda\engine_info.txt" echo [WARN] Missing CUDA engine info.
if not exist "src\engines\vulkan\engine_info.txt" echo [WARN] Missing Vulkan engine info.

REM --- 5. RUN GUI ---
echo [INFO] Launching GUI...
set "PYTHONPATH=%~dp0src"
"%VENV_PYTHON%" src\gui.py

if errorlevel 1 (
    echo [ERROR] Application crashed with code %errorlevel%
    pause
    exit /b 1
)

echo [INFO] Application exited normally.
exit /b 0

:pip_error
echo [ERROR] Dependency installation failed.
pause
