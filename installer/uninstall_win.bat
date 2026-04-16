@echo off
REM Alma Insights — Windows uninstaller launcher
REM Runs from the installed bundle; forwards any flags to uninstall.py.

setlocal

set "INSTALL_DIR=%~dp0"
set "PYTHON=%INSTALL_DIR%python\pythonw.exe"
if not exist "%PYTHON%" set "PYTHON=%INSTALL_DIR%python\python.exe"
set "UNINSTALL=%INSTALL_DIR%_installer\uninstall.py"

if not exist "%PYTHON%" (
  echo [ERROR] Bundled Python not found at %PYTHON%
  exit /b 1
)
if not exist "%UNINSTALL%" (
  echo [ERROR] Uninstaller not found at %UNINSTALL%
  exit /b 1
)

"%PYTHON%" "%UNINSTALL%" --install-dir "%INSTALL_DIR:~0,-1%" %*
endlocal
