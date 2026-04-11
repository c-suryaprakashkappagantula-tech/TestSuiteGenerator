@echo off
title TSG - Pre-loading Database
echo.
echo   ========================================
echo     TSG - Database Pre-loader
echo   ========================================
echo.
echo   This fetches ALL PI features + Chalk data
echo   and saves to the local DB. Run once.
echo   After this, the dashboard loads instantly.
echo.
echo   Press Ctrl+C to cancel.
echo.
python preload_db.py
echo.
pause
