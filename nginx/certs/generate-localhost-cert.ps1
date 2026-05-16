# generate-localhost-cert.ps1
# Place this file inside: DjOpenKB\nginx\certs\
# It generates:
#   DjOpenKB\nginx\certs\localhost.crt
#   DjOpenKB\nginx\certs\localhost.key

$ErrorActionPreference = "Stop"

$CertsDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$KeyPath = Join-Path $CertsDir "localhost.key"
$CrtPath = Join-Path $CertsDir "localhost.crt"

New-Item -ItemType Directory -Force -Path $CertsDir | Out-Null

$OpenSslCandidates = @(
    "C:\Program Files\Git\usr\bin\openssl.exe",
    "C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
    "C:\Program Files\OpenSSL-Win32\bin\openssl.exe"
)

$OpenSslExe = $null

foreach ($Candidate in $OpenSslCandidates) {
    if (Test-Path $Candidate) {
        $OpenSslExe = $Candidate
        break
    }
}

if (-not $OpenSslExe) {
    $Command = Get-Command openssl.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $OpenSslExe = $Command.Source
    }
}

if (-not $OpenSslExe) {
    Write-Host ""
    Write-Host "ERROR: OpenSSL was not found." -ForegroundColor Red
    Write-Host "Install Git for Windows, then try again:"
    Write-Host "https://git-scm.com/download/win"
    Write-Host ""
    pause
    exit 1
}

Write-Host ""
Write-Host "Using OpenSSL: $OpenSslExe"
Write-Host "Generating localhost HTTPS certificate..."
Write-Host ""

& $OpenSslExe req -x509 -nodes -days 365 -newkey rsa:2048 `
    -keyout $KeyPath `
    -out $CrtPath `
    -subj "/C=SG/ST=Singapore/L=Singapore/O=DjOpenKB/OU=Local/CN=localhost" `
    -addext "subjectAltName=DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"

if ((Test-Path $KeyPath) -and (Test-Path $CrtPath)) {
    Write-Host ""
    Write-Host "Certificate generated successfully!" -ForegroundColor Green
    Write-Host "CRT: $CrtPath"
    Write-Host "KEY: $KeyPath"
    Write-Host ""
    Write-Host "For Docker Compose, mount this folder into your nginx container:"
    Write-Host "  ./nginx/certs:/etc/nginx/certs:ro"
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "ERROR: Certificate generation failed." -ForegroundColor Red
    Write-Host ""
    exit 1
}

pause
