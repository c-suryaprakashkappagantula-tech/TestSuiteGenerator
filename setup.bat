@echo off
echo ============================================
echo   TSG - Test Suite Generator Setup
echo ============================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo FAILED: pip install. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browser...
playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo FAILED: playwright install. Try running as administrator.
    pause
    exit /b 1
)

echo.
echo [3/3] Setup complete!
echo.
echo To start the dashboard, run:
echo   streamlit run TSG_Dashboard_V2.0.py
echo.
echo Or double-click: run_dashboard.bat
echo.
pause
