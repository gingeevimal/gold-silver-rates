@echo off
REM Double-click this file once to set up the daily schedule (11:30 AM and 3:00 PM).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_scheduler.ps1"
pause
