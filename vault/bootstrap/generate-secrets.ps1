param(
    [string]$OutputFile = "vault/bootstrap/djopenkb.env",
    [string]$ExampleFile = "vault/bootstrap/djopenkb.env.example",
    [int]$DjangoKeyLength = 72,
    [int]$PostgresPasswordLength = 40,
    [int]$FieldEncryptionKeyLength = 72,
    [int]$PlaceholderPasswordLength = 40
)

$ErrorActionPreference = "Stop"

function New-Secret {
    param([int]$Length)

    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)

    $result = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) {
        [void]$result.Append($chars[$b % $chars.Length])
    }
    return $result.ToString()
}

$dir = Split-Path $OutputFile -Parent
if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

if (-not (Test-Path $OutputFile)) {
    if (Test-Path $ExampleFile) {
        Copy-Item $ExampleFile $OutputFile
    } else {
@"
# ---------------------------------------------------------------------
# DjOpenKB Vault bootstrap secrets
# ---------------------------------------------------------------------
# Generated locally. Do not commit or share this file.
# After Vault is seeded and login works, delete this file from exported copies.
#
# Use no quotes.
# Do not put spaces around "=".
# Avoid spaces and shell special characters in values.

DJANGO_SECRET_KEY=replace-with-a-long-random-django-secret-key
DJANGO_FIELD_ENCRYPTION_KEY=replace-with-a-long-random-field-encryption-key
POSTGRES_PASSWORD=replace-with-stable-postgres-password

AI_API_KEY=replace-with-selected-ai-provider-api-key
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LDAP_BIND_PASSWORD=replace-with-real-svc-djopenkb-password
LDAP_PLACEHOLDER_PASSWORD=replace-with-placeholder-password-or-leave-random
"@ | Set-Content -Path $OutputFile -Encoding UTF8
    }
}

$replacements = @{
    "DJANGO_SECRET_KEY" = New-Secret $DjangoKeyLength
    "POSTGRES_PASSWORD" = New-Secret $PostgresPasswordLength
    "DJANGO_FIELD_ENCRYPTION_KEY" = New-Secret $FieldEncryptionKeyLength
    "LDAP_PLACEHOLDER_PASSWORD" = New-Secret $PlaceholderPasswordLength
}

$lines = Get-Content $OutputFile
$out = New-Object System.Collections.Generic.List[string]
$found = @{
    "DJANGO_SECRET_KEY" = $false
    "POSTGRES_PASSWORD" = $false
    "DJANGO_FIELD_ENCRYPTION_KEY" = $false
    "LDAP_PLACEHOLDER_PASSWORD" = $false
}

foreach ($line in $lines) {
    $trimmed = $line.Trim()
    $handled = $false

    foreach ($key in $replacements.Keys) {
        if ($trimmed.StartsWith("$key=")) {
            $out.Add("$key=$($replacements[$key])")
            $found[$key] = $true
            $handled = $true
            break
        }
    }

    if (-not $handled) {
        $out.Add($line)
    }
}

if (-not $found["LDAP_PLACEHOLDER_PASSWORD"]) {
    if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -ne "") {
        $out.Add("")
    }
    $out.Add("# Only used if LDAP_PLACEHOLDER_ENABLED=true.")
    $out.Add("LDAP_PLACEHOLDER_PASSWORD=$($replacements['LDAP_PLACEHOLDER_PASSWORD'])")
}


# Keep provider-specific AI key placeholders available. Do not generate or
# overwrite API keys because those are manually issued by the provider.
foreach ($key in @("AI_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LDAP_BIND_PASSWORD")) {
    $exists = $false
    foreach ($line in $out) {
        if ($line.Trim().StartsWith("$key=")) { $exists = $true; break }
    }
    if (-not $exists) {
        if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -ne "") { $out.Add("") }
        if ($key -eq "AI_API_KEY") {
            $out.Add("# General fallback AI provider key.")
        } elseif ($key -in @("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")) {
            $out.Add("# Optional provider-specific AI key.")
        } elseif ($key -eq "LDAP_BIND_PASSWORD") {
            $out.Add("# AD LDAP service account password.")
        }
        $out.Add("$key=")
    }
}

$out | Set-Content -Path $OutputFile -Encoding UTF8

Write-Host "Generated bootstrap secrets in: $OutputFile"
Write-Host "Updated: DJANGO_SECRET_KEY, DJANGO_FIELD_ENCRYPTION_KEY, POSTGRES_PASSWORD, LDAP_PLACEHOLDER_PASSWORD"
Write-Host "Preserved: comments, AI_API_KEY/GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY, LDAP_BIND_PASSWORD"
Write-Host ""
Write-Host "Next: edit the correct AI provider key and LDAP_BIND_PASSWORD manually."
Write-Host "Use no quotes, no spaces around '=', and avoid spaces/shell symbols."
Write-Host "Good example: LDAP_BIND_PASSWORD=P@ssw0rd"
Write-Host "Avoid: LDAP_BIND_PASSWORD=`"P@ssw0rd!`""
