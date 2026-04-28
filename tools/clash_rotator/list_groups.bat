@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0clash_rotator.py" --list-groups
) else (
  python "%~dp0clash_rotator.py" --list-groups
)

echo.
pause
