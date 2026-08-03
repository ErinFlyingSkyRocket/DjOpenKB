@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM ---------------------------------------------------------------------------
REM DjOpenKB Vault bootstrap secret generator (Windows)
REM ---------------------------------------------------------------------------
REM What it does:
REM   Calls generate-secrets.ps1, fills blank/placeholder generated secrets, and
REM   preserves existing real values unless explicit rotation is requested.
REM
REM Run from a Windows copy of the project:
REM   cd C:\path\to\DjOpenKB
REM   vault\bootstrap\generate-secrets.bat
REM
REM Pass PowerShell options through the Batch helper when needed:
REM   vault\bootstrap\generate-secrets.bat -OutputFile C:\secure\djopenkb.env
REM
REM Dangerous explicit rotation:
REM   vault\bootstrap\generate-secrets.bat -RotateGeneratedSecrets
REM
REM Review and protect the file before copying it to the Linux server at:
REM   /opt/DjOpenKB/vault/bootstrap/djopenkb.env
REM Then run "sudo docker compose up --build -d" and delete the plaintext file
REM after Vault and the application have been verified.
REM ---------------------------------------------------------------------------

set "SCRIPT=%~dp0generate-secrets.ps1"

echo DjOpenKB bootstrap secret generator
echo - Generates separate Django signing and field-encryption values for fresh deployments
echo - Preserves existing non-placeholder values unless rotation is explicitly requested
echo.

if not exist "%SCRIPT%" (
    echo ERROR: Could not find "%SCRIPT%".
    endlocal & exit /b 1
)

rem %* is intentionally passed through so optional PowerShell parameters such as
rem -OutputFile or -RotateGeneratedSecrets are available from the batch helper.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Secret generation failed. Review the error above.
)

endlocal & exit /b %EXIT_CODE%
