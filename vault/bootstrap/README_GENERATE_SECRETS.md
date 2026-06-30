# DjOpenKB bootstrap secret generator

Use these scripts to create a **first-time** `vault/bootstrap/djopenkb.env` file safely.

The generator creates only these application-generated values when their current
values are blank or still obvious placeholders:

```env
DJANGO_SECRET_KEY=...
POSTGRES_PASSWORD=...
DJANGO_FIELD_ENCRYPTION_KEY=...
LDAP_PLACEHOLDER_PASSWORD=...
```

It never generates or overwrites real API keys or service-account credentials.
It preserves these manual values:

```env
AI_API_KEY=
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LDAP_BIND_PASSWORD=
SMTP_RELAY_USERNAME=
SMTP_RELAY_PASSWORD=
SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false
```

`SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false` is the recommended setting.
Set it to `true` only for a controlled temporary transition where SMTP must use
the exact password already stored as `LDAP_BIND_PASSWORD`. The dedicated SMTP
service account remains the preferred production design.

## Windows PowerShell

From any directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File vault/bootstrap/generate-secrets.ps1
```

Or use the helper batch file:

```bat
vault\bootstrap\generate-secrets.bat
```

The batch file passes optional PowerShell arguments through. For example, a
fresh review file can be created without replacing the existing bootstrap file:

```bat
vault\bootstrap\generate-secrets.bat -OutputFile vault\bootstrap\djopenkb.new.env
```

## Linux/macOS/Git Bash

From any directory:

```sh
sh vault/bootstrap/generate-secrets.sh
```

To create an alternate review file:

```sh
OUTPUT_FILE=vault/bootstrap/djopenkb.new.env sh vault/bootstrap/generate-secrets.sh
```

## Safe default behaviour

The generator deliberately does **not** rotate an existing real value. This is
important because changing either of the following without a migration plan can
break a live deployment:

- `POSTGRES_PASSWORD` must also be changed inside PostgreSQL.
- `DJANGO_FIELD_ENCRYPTION_KEY` protects encrypted database values, including
  MFA-related secrets. Rotating it without re-encrypting existing values can
  make them unreadable.

An intentionally short update-only bootstrap file (for example, one containing
only SMTP settings) is also left short. The generator does not append unrelated
Django or PostgreSQL values to it, so a later `vault-init` run cannot silently
rotate production secrets.

Explicit rotation exists only for a controlled first-time rebuild or planned
secret rotation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File vault/bootstrap/generate-secrets.ps1 -RotateGeneratedSecrets
```

```sh
ROTATE_GENERATED_SECRETS=1 sh vault/bootstrap/generate-secrets.sh
```

Do not use either command on a running deployment unless the required database
and encryption-key migration work has already been planned and tested.

## File format requirements

`vault/bootstrap/djopenkb.env` is sourced by Linux `/bin/sh` during Vault
seeding. Keep it as plain `KEY=value` lines:

- Do not use spaces around `=`.
- Generated values are alphanumeric and remain unquoted.
- For a manual password containing shell-special characters or spaces, use
  single quotes, for example:

  ```env
  LDAP_BIND_PASSWORD='example-password-with-special-characters'
  ```

- Avoid double quotes for values containing shell-expansion characters.
- The Windows generator writes UTF-8 **without a BOM** and Linux-compatible LF
  line endings to avoid shell parsing failures.

## Important

`vault/bootstrap/djopenkb.env` contains real secrets. Do not commit, upload,
or submit it. Remove it after successful Vault seeding when your deployment
workflow allows it.

If Vault was already seeded, changing this file alone does not necessarily
change Vault. Use your approved Vault update workflow, then validate the
affected service before deleting the temporary bootstrap file.
