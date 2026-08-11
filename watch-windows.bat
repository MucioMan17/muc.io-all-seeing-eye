@echo off
REM All-Seeing Eye - NIGHT WATCH: lightweight 24/7 recorder.
REM No window, no face recognition, no web server - it just records driveway/yard
REM events and barely touches your CPU, so you can leave it running while you game
REM or work. Open the full app (run-windows.bat) only when you want live view,
REM face recognition, or Alan.
cd /d "%~dp0"

REM Quiet the camera decoder so the log stays clean and never prints RTSP passwords.
set OPENCV_FFMPEG_LOGLEVEL=-8
set OPENCV_LOG_LEVEL=SILENT

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
echo NIGHT WATCH running - recording events, face AI OFF.  (log: run\watch.log)
echo Close this window to stop watching.
python -m allseeingeye --config config\local.yml --lite > run\watch.log 2>&1
echo.
echo Night watch stopped.
pause
