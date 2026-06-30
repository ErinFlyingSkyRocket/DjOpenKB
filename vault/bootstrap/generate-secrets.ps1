<#
.SYNOPSIS
  Safely creates first-time DjOpenKB bootstrap secrets.

.DESCRIPTION
  Generated values are written only when the matching value is blank or still
  an obvious placeholder. Existing real values are preserved by default.

  This is intentional: DJANGO_FIELD_ENCRYPTION_KEY and POSTGRES_PASSWORD must
  not be silently changed on an existing deployment.
#>
[CmdletBinding()]
param(
    [string]$OutputFile,
    [string]$ExampleFile,
    [ValidateRange(32, 4096)]
    [int]$DjangoKeyLength = 72,
    [ValidateRange(24, 4096)]
    [int]$PostgresPasswordLength = 40,
    [ValidateRange(32, 4096)]
    [int]$FieldEncryptionKeyLength = 72,
    [ValidateRange(24, 4096)]
    [int]$PlaceholderPasswordLength = 40,
    [switch]$RotateGeneratedSecrets
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$BootstrapDirectory = Split-Path -Parent $PSCommandPath
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $OutputFile = Join-Path $BootstrapDirectory "djopenkb.env"
}
if ([string]::IsNullOrWhiteSpace($ExampleFile)) {
    $ExampleFile = Join-Path $BootstrapDirectory "djopenkb.env.example"
}

$OutputFile = [System.IO.Path]::GetFullPath($OutputFile)
$ExampleFile = [System.IO.Path]::GetFullPath($ExampleFile)

function New-Secret {
    param(
        [ValidateRange(1, 4096)]
        [int]$Length
    )

    # Windows PowerShell 5.1-compatible cryptographic RNG.
    # Values remain alphanumeric so the bootstrap file is safe for /bin/sh to source.
    $Characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $Bytes = New-Object byte[] ($Length * 2)
    $Result = New-Object System.Text.StringBuilder
    $Limit = 256 - (256 % $Characters.Length)
    $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        while ($Result.Length -lt $Length) {
            $Rng.GetBytes($Bytes)
            foreach ($Byte in $Bytes) {
                if ($Byte -lt $Limit) {
                    [void]$Result.Append($Characters[$Byte % $Characters.Length])
                    if ($Result.Length -eq $Length) {
                        break
                    }
                }
            }
        }
    }
    finally {
        $Rng.Dispose()
    }

    return $Result.ToString()
}

function Test-IsPlaceholderValue {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    $Candidate = $Value.Trim()
    if ($Candidate.Length -ge 2 -and
        (($Candidate.StartsWith("'") -and $Candidate.EndsWith("'")) -or
         ($Candidate.StartsWith('"') -and $Candidate.EndsWith('"')))) {
        $Candidate = $Candidate.Substring(1, $Candidate.Length - 2)
    }

    return $Candidate -match '^(replace-with|change[-_]?me|example|todo|your[-_]?|<.+>)'
}

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string[]]$Lines
    )

    # Windows PowerShell's Set-Content -Encoding UTF8 writes a BOM. The
    # bootstrap file is sourced by /bin/sh, so write UTF-8 without a BOM and
    # use LF line endings to prevent Linux shell parsing problems.
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $Content = [string]::Join("`n", $Lines) + "`n"
    [System.IO.File]::WriteAllText($Path, $Content, $Utf8NoBom)
}

$OutputDirectory = Split-Path -Parent $OutputFile
if ($OutputDirectory -and -not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
}

$CreatedNewFile = -not (Test-Path -LiteralPath $OutputFile)
if ($CreatedNewFile) {
    if (Test-Path -LiteralPath $ExampleFile) {
        Copy-Item -LiteralPath $ExampleFile -Destination $OutputFile
    }
    else {
@(
    "# ---------------------------------------------------------------------",
    "# DjOpenKB Vault bootstrap secrets",
    "# ---------------------------------------------------------------------",
    "# Generated locally. Do not commit or share this file.",
    "# Do not put spaces around '='.",
    "# Generated values are alphanumeric-only and should stay unquoted.",
    "",
    "DJANGO_SECRET_KEY=replace-with-a-long-random-django-secret-key",
    "DJANGO_FIELD_ENCRYPTION_KEY=replace-with-a-long-random-field-encryption-key",
    "POSTGRES_PASSWORD=replace-with-stable-postgres-password",
    "",
    "AI_API_KEY=",
    "GEMINI_API_KEY=",
    "OPENAI_API_KEY=",
    "ANTHROPIC_API_KEY=",
    "LDAP_BIND_PASSWORD=",
    "LDAP_PLACEHOLDER_PASSWORD=replace-with-placeholder-password-or-leave-random",
    "",
    "# Direct SMTP review-notification service account.",
    "SMTP_RELAY_USERNAME=",
    "SMTP_RELAY_PASSWORD=",
    "# Set true only for a controlled transition using LDAP_BIND_PASSWORD.",
    "SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false"
) | ForEach-Object { $_ } | Set-Content -LiteralPath $OutputFile -Encoding Ascii
    }
}

# ReadAllLines recognises an existing UTF-8 BOM. The file is rewritten below
# without a BOM and with LF line endings.
$Lines = [System.IO.File]::ReadAllLines($OutputFile)

$GeneratedValues = [ordered]@{
    "DJANGO_SECRET_KEY" = New-Secret $DjangoKeyLength
    "POSTGRES_PASSWORD" = New-Secret $PostgresPasswordLength
    "DJANGO_FIELD_ENCRYPTION_KEY" = New-Secret $FieldEncryptionKeyLength
    "LDAP_PLACEHOLDER_PASSWORD" = New-Secret $PlaceholderPasswordLength
}

$ExistingKeys = @{}
foreach ($Line in $Lines) {
    if ($Line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $ExistingKeys[$Matches[1]] = $true
    }
}

# A short update-only bootstrap file may deliberately contain only SMTP or LDAP
# values. Do not append unrelated secrets to such a file; otherwise a later
# Vault init could unintentionally rotate production keys.
$LooksLikeFullBootstrap = $CreatedNewFile -or
    $ExistingKeys.ContainsKey("DJANGO_SECRET_KEY") -or
    $ExistingKeys.ContainsKey("POSTGRES_PASSWORD") -or
    $ExistingKeys.ContainsKey("DJANGO_FIELD_ENCRYPTION_KEY")

$OutputLines = New-Object System.Collections.Generic.List[string]
$UpdatedKeys = New-Object System.Collections.Generic.List[string]

foreach ($Line in $Lines) {
    $Handled = $false

    if ($Line -match '^(\s*)([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $LeadingWhitespace = $Matches[1]
        $Key = $Matches[2]
        $CurrentValue = $Matches[3]

        if ($GeneratedValues.Contains($Key) -and
            ($RotateGeneratedSecrets -or (Test-IsPlaceholderValue $CurrentValue))) {
            $OutputLines.Add("$LeadingWhitespace$Key=$($GeneratedValues[$Key])")
            $UpdatedKeys.Add($Key)
            $Handled = $true
        }
    }

    if (-not $Handled) {
        $OutputLines.Add($Line)
    }
}

$AddedSmtpKeys = New-Object System.Collections.Generic.List[string]
if ($LooksLikeFullBootstrap) {
    $MissingSmtpKeys = @(
        "SMTP_RELAY_USERNAME",
        "SMTP_RELAY_PASSWORD",
        "SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD"
    ) | Where-Object { -not $ExistingKeys.ContainsKey($_) }

    if ($MissingSmtpKeys.Count -gt 0) {
        if ($OutputLines.Count -gt 0 -and $OutputLines[$OutputLines.Count - 1].Trim() -ne "") {
            $OutputLines.Add("")
        }
        $OutputLines.Add("# Direct SMTP review-notification service account. Fill these only when SMTP notifications are enabled.")

        foreach ($Key in $MissingSmtpKeys) {
            switch ($Key) {
                "SMTP_RELAY_USERNAME" {
                    $OutputLines.Add("SMTP_RELAY_USERNAME=")
                }
                "SMTP_RELAY_PASSWORD" {
                    $OutputLines.Add("SMTP_RELAY_PASSWORD=")
                }
                "SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD" {
                    $OutputLines.Add("# Set true only for a controlled transition using LDAP_BIND_PASSWORD; false is recommended.")
                    $OutputLines.Add("SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false")
                }
            }
            $AddedSmtpKeys.Add($Key)
        }
    }
}

Write-Utf8NoBom -Path $OutputFile -Lines $OutputLines.ToArray()

Write-Host "Bootstrap file: $OutputFile"
if ($UpdatedKeys.Count -gt 0) {
    Write-Host "Generated: $($UpdatedKeys -join ', ')"
}
else {
    Write-Host "Generated: none (existing non-placeholder secrets were preserved)"
}
if ($AddedSmtpKeys.Count -gt 0) {
    Write-Host "Added direct-SMTP placeholders: $($AddedSmtpKeys -join ', ')"
}
elseif (-not $LooksLikeFullBootstrap -and -not $CreatedNewFile) {
    Write-Host "Detected an update-only bootstrap file; no unrelated settings were appended."
}

Write-Host "Manual values preserved/not generated: AI API keys, LDAP_BIND_PASSWORD, SMTP_RELAY_USERNAME, SMTP_RELAY_PASSWORD"
Write-Host ""
Write-Host "Review the file before using it. Do not commit, upload, or submit it."
if ($RotateGeneratedSecrets) {
    Write-Warning "-RotateGeneratedSecrets was used. Do not apply rotated POSTGRES_PASSWORD or DJANGO_FIELD_ENCRYPTION_KEY to an existing deployment without a deliberate migration plan."
}
