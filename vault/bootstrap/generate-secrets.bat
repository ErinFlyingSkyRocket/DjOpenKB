@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0generate-secrets.ps1"
endlocal
