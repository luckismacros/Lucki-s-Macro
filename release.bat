@echo off
rem Build and publish a release: release.bat 3.6.1   (see tools\release.py)
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\release.py %*
pause
