@echo off
REM All-Seeing Eye - Windows launcher (native desktop app + face recognition).
REM Double-click this file to run. First launch installs everything.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: creating environment and installing dependencies...
    echo This can take a few minutes.
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements-app.txt
) else (
    call ".venv\Scripts\activate.bat"
)

if not exist "run" mkdir run
echo.
echo Starting All-Seeing Eye...  (the window will open shortly)
echo Camera/decoder log messages go to run\app.log - this console stays clean.
echo Close the app window to quit.
python desktop_main.py > run\app.log 2>&1
echo.
echo App closed.
pause
