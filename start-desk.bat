@echo off
cd /d "%~dp0"
echo Starting FX-GLITCH desk...
python serve.py --open
if errorlevel 1 (
  echo.
  echo Desk failed to start. If this says pip / python not found, install Python 3 and tick "Add python.exe to PATH".
  pause
)
