@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install Andrew first, then run this companion setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" installers\install_companion.py --register
if errorlevel 1 (
  echo Companion setup needs attention. Read the error above.
  pause
  exit /b 1
)
echo Open chrome://extensions in regular Chrome.
echo Enable Developer mode, choose Load unpacked, and select:
echo %~dp0companion
echo This setup never changes browser account sign-ins.
pause
