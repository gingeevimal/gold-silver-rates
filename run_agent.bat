@echo off
REM Runs the metal rates agent (used by the scheduled task).
cd /d "%~dp0"

REM Right after the laptop wakes up, Wi-Fi may not be connected yet.
REM Wait up to ~5 minutes for internet before running.
set tries=0
:waitnet
ping -n 1 www.goodreturns.in >nul 2>&1
if not errorlevel 1 goto run
set /a tries+=1
if %tries% geq 20 goto run
ping -n 16 127.0.0.1 >nul
goto waitnet

:run
echo ===== %date% %time% >> agent_console.log
python metal_rates_agent.py >> agent_console.log 2>&1
