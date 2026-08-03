<#
.SYNOPSIS
  Generates a self-signed development TLS certificate for DjOpenKB Nginx.

.DESCRIPTION
  Creates nginx/certs/localhost.crt and nginx/certs/localhost.key with SANs for
  localhost, the Nginx service name, and an optional browser-facing IPv4 address.
  The certificate is intended for internal development only.

.EXAMPLE
  cd C:\path\to\DjOpenKB
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File nginx\certs\generate-localhost-cert.ps1

.EXAMPLE
  cd C:\path\to\DjOpenKB
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File nginx\certs\generate-localhost-cert.ps1 <server-ip-address> 825

.NOTES
  Parameter 1 is the optional server IPv4 address. Parameter 2 is the optional
  certificate lifetime in days (default 365). After copying the generated files
  to the Linux server, run from /opt/DjOpenKB:
      sudo docker compose restart nginx
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$TargetIp = "",

    [Parameter(Position = 1)]
    [ValidateRange(1, 36500)]
    [int]$Days = 365
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"



$ScriptDir = Split-Path -Parent $PSCommandPath
$CertFile = Join-Path $ScriptDir "localhost.crt"
$KeyFile = Join-Path $ScriptDir "localhost.key"
$OpenSslCnf = Join-Path $ScriptDir "localhost-openssl.cnf"

$ParsedIp = $null
if (-not [string]::IsNullOrWhiteSpace($TargetIp)) {
    $IsValidIp = [System.Net.IPAddress]::TryParse($TargetIp, [ref]$ParsedIp)
    if (-not $IsValidIp -or
        $ParsedIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Provide a valid IPv4 address, for example <INTERNAL_SERVER_IP>."
    }
}

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    throw "OpenSSL was not found in PATH. Install OpenSSL, reopen PowerShell, and try again."
}

$CommonName = "localhost"
$ExtraIpSan = ""
if (-not [string]::IsNullOrWhiteSpace($TargetIp)) {
    $CommonName = $TargetIp
    $ExtraIpSan = "IP.3 = $TargetIp"
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
O = DjOpenKB Development
OU = Internal Development
CN = $CommonName

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
$ExtraIpSan
"@ | Set-Content -Path $OpenSslCnf -Encoding ASCII

try {
    Write-Host "Generating development TLS certificate..."

    & $openssl.Source req -x509 -nodes -days $Days `
        -newkey rsa:2048 `
        -keyout $KeyFile `
        -out $CertFile `
        -config $OpenSslCnf

    if (-not (Test-Path -LiteralPath $CertFile) -or
        -not (Test-Path -LiteralPath $KeyFile)) {
        throw "Certificate generation failed. Output files were not created."
    }

    Write-Host ""
    Write-Host "Certificate generated successfully:"
    Write-Host "  $CertFile"
    Write-Host "  $KeyFile"

    Write-Host ""
    Write-Host "Browser URL:"
    if (-not [string]::IsNullOrWhiteSpace($TargetIp)) {
        Write-Host "  https://$TargetIp"
    }
    else {
        Write-Host "  https://localhost"
    }

    Write-Host ""
    Write-Host "Trust the certificate on the development browser to avoid its self-signed warning."
}
finally {
    if (Test-Path -LiteralPath $OpenSslCnf) {
        Remove-Item -LiteralPath $OpenSslCnf -Force
    }
}
