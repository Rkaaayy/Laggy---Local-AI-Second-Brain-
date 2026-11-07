@echo off
echo =====================================================
echo Booting up Laggy Local Second Brain...
echo =====================================================

REM Ensure logs folder exists
if not exist "logs" mkdir logs

REM Create a timestamped server log file name
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c-%%a-%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set logfile=logs\server_live_%mydate%_%mytime%.log

REM Start the FastAPI server in the background and pipe ALL its output into a server log file
echo Starting server... Server logs are being written to %logfile%
start /B "Laggy Server" cmd /c "python src\main.py > %logfile% 2>&1"

REM Give the server 30 seconds to fully boot up and initialize SQLite/Qdrant
echo Waiting 30 seconds for server to initialize...
timeout /t 30 /nobreak >nul

REM Run the automated test suite in this current window
echo Running Test Suite...
python test_suite.py

REM Pause so you can read the test results before the window closes
pause

REM Terminate the background Python server gracefully
taskkill /f /im uvicorn.exe >nul 2>&1
echo Shutting down background server...