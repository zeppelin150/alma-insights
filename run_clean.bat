@echo off
echo ═══════════════════════════════════════
echo   Alma Insights — Clean Launch
echo ═══════════════════════════════════════
echo.
echo Clearing cached bytecode...
for /d /r "%~dp0" %%d in (__pycache__) do (
    if exist "%%d" (
        echo   Removing: %%d
        rd /s /q "%%d"
    )
)
echo.
echo Launching Alma Insights...
echo.
cd /d "%~dp0"
python main.py
pause
