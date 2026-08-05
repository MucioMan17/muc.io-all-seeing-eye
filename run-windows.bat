@echo off
REM All-Seeing Eye - Windows launcher (face + object detection desktop app).
REM Double-click to run. Installs/updates dependencies automatically.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: creating environment...
    python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo Checking dependencies (quick unless something new needs installing)...
python -m pip install -q --upgrade pip
pip install -q -r requirements-app.txt

if not exist "run" mkdir run
echo.
echo Starting All-Seeing Eye...  (log: run\app.log)
echo Close the app window to quit.
python desktop_main.py > run\app.log 2>&1
echo.
echo App closed.
pause
