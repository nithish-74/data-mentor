@echo off
rem Starts the mentor bot. First run creates the Python environment (kept outside OneDrive) and installs packages.
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.data-mentor\venv"
if not exist "%VENV%\Scripts\python.exe" (
    py -3.11 -m venv "%VENV%" || python -m venv "%VENV%"
)
"%VENV%\Scripts\python.exe" -c "import telegram, google.genai, dotenv, tzdata, fastapi, uvicorn, itsdangerous, google.auth" 2>nul || (
    echo Installing packages, please wait...
    "%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt
)
"%VENV%\Scripts\python.exe" app.py
pause
