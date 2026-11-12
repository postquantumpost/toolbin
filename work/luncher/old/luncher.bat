@echo off
setlocal EnableDelayedExpansion

:: 1. Check for Administrative rights
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :RUN_LUNCHER
) else (
    goto :ELEVATE
)

:: 2. Prompt for UAC Elevation using VBScript wrapper
:ELEVATE
echo Administrative privileges required. Requesting elevation...
set "BAT_PATH=%~f0"
set "VBS_PATH=%temp%\getadmin_%RANDOM%.vbs"

echo Set UAC = CreateObject("Shell.Application") > "%VBS_PATH%"
echo UAC.ShellExecute "cmd.exe", "/c ""%BAT_PATH%""", "", "runas", 1 >> "%VBS_PATH%"

"%VBS_PATH%"
del "%VBS_PATH%"
exit /b

:: 3. Run luncher.py in the batch file's working directory
:RUN_LUNCHER
cd /d "%~dp0"

:: Check if Python is installed/in PATH
where python >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python was not found in PATH. Please install Python or add it to system environment variables.
    pause
    exit /b 1
)

:: Execute the python script
python luncher.py

if %errorLevel% neq 0 (
    echo.
    echo Script ended with an error (Code: %errorLevel%).
    pause
)