@echo off
title Website Change Detector
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Install it from python.org and tick "Add Python to PATH". & pause & exit /b)
if not exist venv\setup_done.txt (
  echo First-time setup - this takes a few minutes, please wait...
  python -m venv venv
  call venv\Scripts\activate.bat
  pip install -r requirements.txt || (echo Install failed. Check your internet and try again. & pause & exit /b)
  python -m playwright install chromium || (echo Browser download failed. Try again. & pause & exit /b)
  echo done> venv\setup_done.txt
) else (
  call venv\Scripts\activate.bat
)
start "" cmd /c "timeout /t 5 >nul & start http://localhost:5000"
echo Running at http://localhost:5000  -  close this window to stop.
python app.py
pause
