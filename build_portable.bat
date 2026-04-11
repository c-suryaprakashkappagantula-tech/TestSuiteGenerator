@echo off
echo ============================================
echo   TSG - Build Portable Distribution
echo ============================================
echo.
echo This creates a self-contained folder that
echo testers can run WITHOUT installing Python,
echo pip, or any dependencies.
echo.
echo Prerequisites: You need Python 3.13 installed
echo on THIS machine (the build machine only).
echo.

set DIST=dist\TSG_Portable
set PYVER=3.13

echo [1/6] Creating distribution folder...
if exist %DIST% rmdir /s /q %DIST%
mkdir %DIST%
mkdir %DIST%\modules
mkdir %DIST%\templates
mkdir %DIST%\outputs
mkdir %DIST%\checkpoints
mkdir %DIST%\inputs
mkdir %DIST%\attachments
mkdir %DIST%\logs

echo [2/6] Downloading embedded Python...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.13.0/python-3.13.0-embed-amd64.zip' -OutFile '%DIST%\python_embed.zip'"
powershell -Command "Expand-Archive -Path '%DIST%\python_embed.zip' -DestinationPath '%DIST%\python' -Force"
del %DIST%\python_embed.zip

echo [3/6] Installing pip into embedded Python...
powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%DIST%\python\get-pip.py'"
%DIST%\python\python.exe %DIST%\python\get-pip.py --no-warn-script-location
del %DIST%\python\get-pip.py

REM Enable site-packages in embedded Python
powershell -Command "(Get-Content '%DIST%\python\python313._pth') -replace '#import site','import site' | Set-Content '%DIST%\python\python313._pth'"

echo [4/6] Installing dependencies into embedded Python...
%DIST%\python\python.exe -m pip install --no-warn-script-location -r requirements.txt

echo [5/6] Installing Playwright browser...
%DIST%\python\python.exe -m playwright install chromium

echo [6/6] Copying application files...
copy TSG_Dashboard_V2.0.py %DIST%\
copy TSG_Dashboard_V2.1.py %DIST%\ 2>nul
copy requirements.txt %DIST%\
copy modules\*.py %DIST%\modules\
copy templates\* %DIST%\templates\ 2>nul

REM Copy pre-built DB if it exists (so testers get instant features)
if exist tsg_cache.db copy tsg_cache.db %DIST%\

REM Create the launcher
(
echo @echo off
echo echo Starting TSG Dashboard...
echo echo.
echo echo Once loaded, open your browser to: http://localhost:8501
echo echo Press Ctrl+C to stop.
echo echo.
echo "%%~dp0python\python.exe" -m streamlit run "%%~dp0TSG_Dashboard_V2.0.py" --server.port 8501 --server.headless true --browser.gatherUsageStats false
echo pause
) > %DIST%\START_TSG.bat

echo.
echo ============================================
echo   BUILD COMPLETE!
echo ============================================
echo.
echo Distribution folder: %DIST%\
echo Size: 
dir /s %DIST% | find "File(s)"
echo.
echo To distribute:
echo   1. Zip the %DIST% folder
echo   2. Share the zip with testers
echo   3. Testers extract and double-click START_TSG.bat
echo.
echo No Python, no pip, no installs needed on tester machines!
echo.
pause
