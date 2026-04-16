@echo off
set "PATH=%~dp0node;%PATH%"
start "" "%~dp0python\pythonw.exe" "%~dp0app\main.py"
