@echo off
setlocal
REM Generate secure local bootstrap secrets for DjOpenKB on Windows.
REM Run from the project root:
REM     vault\bootstrap\generate-secrets.bat

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate-secrets.ps1" %*
endlocal
