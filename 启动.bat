@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
title antigank

rem ---------------------------------------------------------------------------
rem Pick the interpreter explicitly.
rem
rem Do NOT trust "python" from PATH: on this machine PATH only contains
rem   C://Users//Lenovo//AppData//Local//Microsoft//WindowsApps
rem and python.exe there is the Microsoft Store stub - it exits silently with
rem zero output. Always prefer the known-good portable interpreter first.
rem
rem NOTE: this file MUST keep CRLF line endings. cmd.exe mis-parses LF-only
rem batch files and every line turns into a "is not recognized" error.
rem ---------------------------------------------------------------------------
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if exist "%PY%" goto havepy
for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%~d\python.exe" set "PY=%%~d\python.exe"
)
if exist "%PY%" goto havepy
set "PY=python"
:havepy

echo [antigank] python: %PY%
"%PY%" -c "import cv2, numpy, mss" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [antigank] ERROR: this python cannot import cv2 / numpy / mss.
    echo   interpreter: %PY%
    echo   fix:  "%PY%" -m pip install -i https://mirrors.cloud.tencent.com/pypi/simple numpy opencv-python mss Pillow
    echo.
    pause
    exit /b 1
)

echo [antigank] starting...  (log: antigank.log)
echo.

rem No sub-command = open the graphical menu:
rem   [1] start monitoring   [2] pick minimap   [3] choose champions
"%PY%" -u main.py %*

echo.
echo [antigank] exited. code=%errorlevel%
echo log file: %~dp0antigank.log
pause
exit /b 0
