@echo off
REM All-Seeing Eye - TELEGRAM BOT: night-watch plus remote control from your phone.
REM Records driveway/room events (like watch-windows.bat) AND lets you control it
REM and receive detection photos over a Telegram chat. Outbound internet only - no
REM port-forwarding. Use THIS instead of watch-windows.bat (don't run both, or the
REM cameras get opened twice and every event records twice).
REM
REM First-time setup (once):
REM   1. In Telegram, message @BotFather -> /newbot -> copy the token.
REM   2. Put the token in config\local.yml under telegram.token, set enabled: true.
REM   3. Start this, message your bot /status - it replies with your chat_id.
REM   4. Put that chat_id in config\local.yml and restart this.
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
echo TELEGRAM BOT running - recording events + chat control.  (log: run\bot.log)
echo Close this window to stop.
python -m allseeingeye --config config\local.yml --bot > run\bot.log 2>&1
echo.
echo Bot stopped.
pause
