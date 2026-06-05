@echo off
setlocal

REM Generate a local self-signed HTTPS certificate for DjOpenKB Nginx.
REM Output files:
REM   nginx\certs\localhost.crt
REM   nginx\certs\localhost.key
REM
REM Run from the project root:
REM   nginx\certs\generate-localhost-cert.bat

powershell -ExecutionPolicy Bypass -File "%~dp0generate-localhost-cert.ps1"

endlocal
