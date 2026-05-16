@echo off
REM generate-localhost-cert.bat
REM Place this file inside: DjOpenKB\nginx\certs\
REM It runs generate-localhost-cert.ps1 from the same folder.

set SCRIPT_DIR=%~dp0

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%generate-localhost-cert.ps1"

pause
