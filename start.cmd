@echo off
setlocal
rem Run from PowerShell or Command Prompt without relying on .ps1 file associations.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-backend.ps1" %*
exit /b %errorlevel%
