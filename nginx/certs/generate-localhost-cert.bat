@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM ---------------------------------------------------------------------------
REM DjOpenKB development TLS certificate helper (Windows)
REM ---------------------------------------------------------------------------
REM What it does:
REM   Calls generate-localhost-cert.ps1 to create localhost.crt and localhost.key.
REM
REM Run from a Windows copy of the project:
REM   cd C:\path\to\DjOpenKB
REM   nginx\certs\generate-localhost-cert.bat
REM
REM Optional server IPv4 address and certificate lifetime in days:
REM   nginx\certs\generate-localhost-cert.bat <server-ip-address>
REM   nginx\certs\generate-localhost-cert.bat <server-ip-address> 825
REM
REM After copying the generated files to /opt/DjOpenKB/nginx/certs on Linux:
REM   cd /opt/DjOpenKB
REM   sudo docker compose restart nginx
REM
REM Internal development only; use a properly issued certificate for production.
REM ---------------------------------------------------------------------------

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
