@echo off
setlocal EnableExtensions DisableDelayedExpansion

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
