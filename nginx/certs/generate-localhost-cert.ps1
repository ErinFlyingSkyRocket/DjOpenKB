param(
    [int]$Days = 825
)

$ErrorActionPreference = "Stop"

# Generate a local self-signed HTTPS certificate for DjOpenKB Nginx.
#
# Output files:
#   nginx/certs/localhost.crt
#   nginx/certs/localhost.key
#
# These paths match nginx/nginx.conf:
#   ssl_certificate     /etc/nginx/certs/localhost.crt;
#   ssl_certificate_key /etc/nginx/certs/localhost.key;
#
# Run from the project root:
#   powershell -ExecutionPolicy Bypass -File nginx/certs/generate-localhost-cert.ps1
#
# Or double-click/run:
#   nginx\certs\generate-localhost-cert.bat

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CertFile = Join-Path $ScriptDir "localhost.crt"
$KeyFile = Join-Path $ScriptDir "localhost.key"
$OpenSslCnf = Join-Path $ScriptDir "localhost-openssl.cnf"

Write-Host "Generating local self-signed HTTPS certificate..."
Write-Host "Output folder: $ScriptDir"

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    Write-Host ""
    Write-Host "ERROR: OpenSSL was not found in PATH."
    Write-Host "Install OpenSSL first, then reopen PowerShell and try again."
    Write-Host "On Windows, Git Bash or OpenSSL for Windows usually provides openssl.exe."
    exit 1
}

@"
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
C = SG
ST = Singapore
L = Singapore
O = DjOpenKB Local
OU = Development
CN = localhost

[v3_req]
subjectAltName = @alt_names
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:FALSE

[alt_names]
DNS.1 = localhost
DNS.2 = nginx
DNS.3 = djopenkb.local
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
"@ | Set-Content -Path $OpenSslCnf -Encoding ASCII

try {
    & openssl req -x509 -nodes -days $Days `
        -newkey rsa:2048 `
        -keyout $KeyFile `
        -out $CertFile `
        -config $OpenSslCnf

    if (-not (Test-Path $CertFile) -or -not (Test-Path $KeyFile)) {
        throw "Certificate generation failed. Output files were not created."
    }

    Write-Host ""
    Write-Host "Certificate generated successfully:"
    Write-Host "  $CertFile"
    Write-Host "  $KeyFile"
    Write-Host ""
    Write-Host "These files match the Nginx container paths:"
    Write-Host "  /etc/nginx/certs/localhost.crt"
    Write-Host "  /etc/nginx/certs/localhost.key"
    Write-Host ""
    Write-Host "You can now run:"
    Write-Host "  docker compose up -d --build"
    Write-Host ""
    Write-Host "Then open:"
    Write-Host "  https://localhost:8080"
}
finally {
    if (Test-Path $OpenSslCnf) {
        Remove-Item $OpenSslCnf -Force
    }
}
