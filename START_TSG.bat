@echo off
title TSG - Test Suite Generator
echo.
echo   ========================================
echo     TSG - Test Suite Generator Dashboard
echo   ========================================
echo.

REM Check if running from portable distribution (has embedded python)
if exist "%~dp0python\python.exe" (
    echo [Portable Mode] Using embedded Python...
    set PYTHON="%~dp0python\python.exe"
    goto :run
)

REM Check if system Python is available
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [System Mode] Using system Python...
    set PYTHON=python
    goto :run
)

echo ERROR: Python not found!
echo.
echo Either:
echo   a) Use the portable distribution (has embedded Python)
echo   b) Install Python 3.10+ from python.org
echo.
pause
exit /b 1

:run
echo Starting dashboard on http://localhost:8501
echo Press Ctrl+C to stop.
echo.
%PYTHON% -m streamlit run "%~dp0TSG_Dashboard_V2.0.py" --server.port 8501 --server.headless true --browser.gatherUsageStats false
pause
