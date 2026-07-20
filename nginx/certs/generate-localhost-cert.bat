@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM Generate a self-signed development TLS certificate for DjOpenKB Nginx.
REM
REM Optional arguments:
REM   1. Browser-facing server IPv4 address.
REM   2. Certificate lifetime in days. Default: 365.
REM
REM Examples:
REM   nginx\certs\generate-localhost-cert.bat
REM   nginx\certs\generate-localhost-cert.bat <INTERNAL_SERVER_IP>
REM   nginx\certs\generate-localhost-cert.bat <INTERNAL_SERVER_IP> 825

set "SCRIPT=%~dp0generate-localhost-cert.ps1"

if not exist "%SCRIPT%" (
    echo ERROR: Could not find "%SCRIPT%".
    endlocal & exit /b 1
)

REM Pass all arguments through so the Batch, PowerShell, and Linux versions
REM support the same optional target IPv4 address and certificate lifetime.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Certificate generation failed. Review the error above.
)

endlocal & exit /b %EXIT_CODE%
