<#
Generate secure local bootstrap secrets for DjOpenKB.

This script creates or updates vault/bootstrap/djopenkb.env with:
- DJANGO_SECRET_KEY
- POSTGRES_PASSWORD

It preserves any existing non-generated lines such as GEMINI_API_KEY or LDAP_BIND_PASSWORD.
Run from the project root:
    powershell -ExecutionPolicy Bypass -File vault/bootstrap/generate-secrets.ps1
#>

param(
    [string]$OutputFile = "vault/bootstrap/djopenkb.env",
    [int]$DjangoKeyLength = 64,
    [int]$PostgresPasswordLength = 32,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-RandomSecret {
    param(
        [int]$Length,
        [string]$Alphabet
    )

    $bytes = New-Object byte[] ($Length)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)

    $chars = New-Object char[] ($Length)
    for ($i = 0; $i -lt $Length; $i++) {
        $chars[$i] = $Alphabet[$bytes[$i] % $Alphabet.Length]
    }
    return -join $chars
}

function Set-Or-AppendEnvLine {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )

    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*="
    $newLine = "$Key=`"$Value`""
    $updated = $false
    $result = foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $updated = $true
            $newLine
        } else {
            $line
        }
    }

    if (-not $updated) {
        $result += $newLine
    }
    return $result
}

$projectRoot = (Resolve-Path ".").Path
$outputPath = Join-Path $projectRoot $OutputFile
$outputDir = Split-Path -Parent $outputPath

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$djangoAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*(-_=+)"
# Keep PostgreSQL password shell/env friendly: no spaces, quotes, backslashes, or dollar signs.
$postgresAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-@#%+=:."

$djangoSecretKey = New-RandomSecret -Length $DjangoKeyLength -Alphabet $djangoAlphabet
$postgresPassword = New-RandomSecret -Length $PostgresPasswordLength -Alphabet $postgresAlphabet

if ((Test-Path $outputPath) -and (-not $Force)) {
    $lines = Get-Content $outputPath
} else {
    $lines = @(
        "# ---------------------------------------------------------------------",
        "# DjOpenKB Vault bootstrap secrets",
        "# Generated locally. Do not commit or share this file.",
        "# After Vault is seeded and login works, delete this file from exported copies.",
        "# ---------------------------------------------------------------------",
        "",
        "# Required Django/PostgreSQL secrets"
    )
}

$lines = Set-Or-AppendEnvLine -Lines $lines -Key "DJANGO_SECRET_KEY" -Value $djangoSecretKey
$lines = Set-Or-AppendEnvLine -Lines $lines -Key "POSTGRES_PASSWORD" -Value $postgresPassword

Set-Content -Path $outputPath -Value $lines -Encoding UTF8

Write-Host "Generated secure secrets in: $OutputFile" -ForegroundColor Green
Write-Host "Updated: DJANGO_SECRET_KEY and POSTGRES_PASSWORD" -ForegroundColor Green
Write-Host "Keep this file private. Do not commit or submit it." -ForegroundColor Yellow
Write-Host "If Vault was already seeded, update Vault or reseed it so the new values are used." -ForegroundColor Yellow
